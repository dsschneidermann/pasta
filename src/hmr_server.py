"""Combined hot-module-reload dev server for pasta.

Runs one uvicorn server, one file watcher and one reactive (HMR) context that serve
both surfaces of ``src.server`` with in-process hot reload - no process respawn:

* FastMCP tools  -> a stable proxy (``base_app``). The reloaded ``src.server.mcp``
  is mounted under it; on a source change the old mount is swapped for the reloaded
  one and connected MCP client sessions are sent ``tools/list_changed``, so sessions
  SURVIVE the reload instead of dropping.

* FastAPI HTML   -> a reactive dispatcher that forwards every request to the freshly
  reloaded ``src.server.app``. On a source change connected browsers are told to
  reload over the existing ``/ws/reloader`` WebSocket (``ws_reloader.refresh``).

This merges the two reference patterns - ``uvicorn-hmr`` (serve a reloaded ASGI app +
browser refresh) and ``mcp-hmr`` (stable proxy + session-preserving tool swap) - into
one module, started from ``main.py``.

The hot part is powered by ``hmr`` (``reactivity.hmr``): the reloader installs an import
finder so ``src.server`` and its submodules load as reactive modules that
re-execute in place when their files change, propagating along the dependency graph.

Two brittleness notes (dev-only tool):
* It reaches into a few private fastmcp internals (``base_app.providers`` /
  ``FastMCPProvider`` for mount/unmount, ``base_app._mcp_server.run`` for session
  capture). These changed across fastmcp versions; revisit on a fastmcp upgrade.
* ``src.server`` and ``src.cleanup`` must be imported ONLY through the
  reloader's finder (never at module top level), or the hot reload silently becomes
  a no-op.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from asyncio import Event, Lock, TaskGroup
from contextlib import asynccontextmanager, suppress
from importlib import import_module
from pathlib import Path
from weakref import WeakSet

import uvicorn
from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans
from mcp.server.session import ServerSession
from reactivity import async_effect, derived
from reactivity.hmr.core import HMR_CONTEXT, AsyncReloader, ErrorFilter
from reactivity.hmr.hooks import call_post_reload_hooks, call_pre_reload_hooks
from starlette.applications import Starlette
from starlette.routing import Mount

from .hmr_live_refresh import ws_reloader

# The reactive target: imported ONLY through the reloader's finder (never at top level),
# so it and its submodules become hot-reloadable reactive modules.
TARGET_MODULE = "src.server"

# Watch/reload the src package, but keep live_reload.py out of the reload set: it
# owns the live browser sockets, which must survive reloads (see src.hmr_live_refresh).
_PACKAGE_DIR = str(Path(__file__).resolve().parent)
_LIVE_REFRESH_FILE = str(Path(__file__).resolve().parent / "hmr_live_refresh.py")

# Reload notifications go through uvicorn's status logger so they land in the dev server
# console next to uvicorn's own output. A standalone module logger at INFO would be
# dropped: uvicorn configures only its own loggers, not the root logger.
logger = logging.getLogger("uvicorn.error")


# --- Reload failure reporting ----------------------------------------------------------
class _LoggingErrorFilter(ErrorFilter):
    """Restate a swallowed reload failure through the dev console's error logger.

    hmr hands a module whose re-exec raised to ``sys.excepthook`` and then swallows it, so the dev
    server keeps running - leaving a bare traceback among uvicorn's own lines, at no level and
    saying nothing of what the failure cost. What the reader needs is the consequence: the edit did
    not take, so the modules loaded before it are the ones still serving.

    Only that summary is logged. The traceback itself still reaches ``sys.excepthook``, which is
    what tees it to the reload log, so nothing is lost and nothing is printed twice.
    """

    def __exit__(self, exc_type, exc_value, traceback_):
        if exc_value is not None:
            reason = "".join(traceback.format_exception_only(exc_type, exc_value)).strip()
            logger.error(
                "[HMR] reload FAILED - the edit did not take, and the modules loaded before it "
                "are still serving:\n%s", reason)
        return super().__exit__(exc_type, exc_value, traceback_)


# --- MCP session capture ---------------------------------------------------------------
# So we can push tools/list_changed to live sessions on reload, we briefly wrap
# ServerSession.__init__ to record each newly created session. Adapted from mcp-hmr.
_active_sessions: WeakSet[ServerSession] = WeakSet()
_pending_session_patches = 0


def _patch_session_init():
    """Wrap ServerSession.__init__ to capture the next new session, then self-restore."""
    global _pending_session_patches
    _pending_session_patches += 1
    original_init = ServerSession.__init__

    def capture_init(self: ServerSession, *args, **kwargs):
        original_init(self, *args, **kwargs)
        _active_sessions.add(self)
        global _pending_session_patches
        _pending_session_patches -= 1
        _unpatch()

    ServerSession.__init__ = capture_init

    def _unpatch():
        if _pending_session_patches < 1:
            ServerSession.__init__ = original_init

    return _unpatch


def build_dev_app() -> Starlette:
    """Build the stable outer ASGI app: /pasta/mcp -> proxy, everything else -> reloaded FastAPI."""

    # The proxy is built ONCE and never rebuilt; only what is mounted under it swaps.
    base_app: FastMCP = FastMCP(name="pasta-hmr-proxy")

    # Capture each new client session created while the proxy's low-level server runs.
    _original_run = base_app._mcp_server.run

    async def _run_with_session_capture(*args, **kwargs):
        unpatch = _patch_session_init()
        try:
            await _original_run(*args, **kwargs)
        finally:
            unpatch()

    base_app._mcp_server.run = _run_with_session_capture

    # Reactive handles on the reloaded module's two entrypoints. Reading either inside a
    # reactive computation subscribes it to reloads of src.server (and its deps).
    @derived(context=HMR_CONTEXT)
    def current_mcp() -> FastMCP:
        return import_module(TARGET_MODULE).mcp

    @derived(context=HMR_CONTEXT)
    def current_fastapi():
        return import_module(TARGET_MODULE).app

    # Mounting forwards live calls to `mcp`; each mount appends one FastMCPProvider to
    # base_app.providers (a fastmcp 3.4.x internal we pop to unmount on the next reload).
    def mount_mcp(mcp: FastMCP):
        base_app.mount(mcp)
        mounted_provider = base_app.providers[-1]

        def unmount():
            if mounted_provider in base_app.providers:
                base_app.providers.remove(mounted_provider)

        return unmount

    async def notify_sessions():
        for session in list(_active_sessions):
            for send in (
                session.send_tool_list_changed,
                session.send_resource_list_changed,
                session.send_prompt_list_changed,
            ):
                with suppress(Exception):
                    await send()

    # Only one mount is live at a time; a reload tears the old one down before mounting new.
    mount_lock = Lock()
    task_group: TaskGroup | None = None
    stop_event: Event | None = None
    finish_event: Event | None = None

    async def serve_mcp(mcp: FastMCP, stop: Event, finish: Event):
        async with mount_lock:
            unmount = mount_mcp(mcp)
            try:
                await notify_sessions()  # tell live sessions the tool set may have changed
                await stop.wait()
            finally:
                unmount()
                finish.set()

    @async_effect(context=HMR_CONTEXT, call_immediately=False)
    async def mcp_reload_effect():
        nonlocal stop_event, finish_event
        # A live previous mount means this run is a reload, not the initial mount.
        is_reload = stop_event is not None and finish_event is not None
        # Tear down the previous mount (if any) before swapping in the reloaded server.
        if is_reload:
            stop_event.set()
            await finish_event.wait()
        mcp = current_mcp()  # subscribe to reloads
        if is_reload:
            logger.info("[HMR] FastMCP server reloaded (src.server.mcp)")
        stop_event, finish_event = Event(), Event()
        assert task_group is not None
        task_group.create_task(serve_mcp(mcp, stop_event, finish_event))

    # Reading an attribute subscribes this effect to src.cleanup, so an edit
    # re-executes it promptly - ReactiveModule.load is otherwise lazy. Its on_dispose
    # hook has already cancelled the old sweep by then; this starts the new one.
    @async_effect(context=HMR_CONTEXT, call_immediately=False)
    async def cleanup_reload_effect():
        cleanup = import_module("src.cleanup")
        await cleanup.stop_scheduler()
        cleanup.start_scheduler(import_module(TARGET_MODULE).STORE)

    class Reloader(AsyncReloader):
        def __init__(self):
            super().__init__(_PACKAGE_DIR, includes=[_PACKAGE_DIR], excludes=[_LIVE_REFRESH_FILE])
            # Every re-exec is wrapped in this filter, so reporting belongs here. Keep the frame
            # exclusions the base built.
            self.error_filter = _LoggingErrorFilter(*self.error_filter.exclude_filenames)
            self.error_filter.exclude_filenames.add(__file__)

        def on_changes(self, files):
            # Reload the changed modules (reactive propagation), then refresh browsers. The refresh
            # is unconditional, a failed reload included: what a tab should land on then is the
            # refusal, and since that page keeps the socket it is also how the tab recovers.
            super().on_changes(files)
            with suppress(RuntimeError):
                asyncio.get_running_loop().create_task(ws_reloader.refresh())

    # /pasta/mcp is served by the stable proxy's ASGI app (built once). Its session manager is
    # started by mcp_asgi.lifespan; the reloader lifespan drives module reloads + mounts.
    mcp_asgi = base_app.http_app(path="/mcp")

    @asynccontextmanager
    async def reloader_lifespan(_app):
        nonlocal task_group
        call_pre_reload_hooks()
        async with TaskGroup() as tg:
            task_group = tg
            reloader = Reloader()  # installs the reactive import finder
            await mcp_reload_effect()  # first mount of the current mcp
            call_post_reload_hooks()
            watch_task = tg.create_task(reloader.start_watching())
            await cleanup_reload_effect()  # first start of the sweep
            try:
                yield
            finally:
                cleanup_reload_effect.dispose()
                await import_module("src.cleanup").stop_scheduler()
                reloader.stop_watching()
                mcp_reload_effect.dispose()
                if stop_event is not None:
                    stop_event.set()
                watch_task.cancel()

    # The FastAPI app object last served; a reload swaps in a new one (see below).
    last_fastapi_app: object = None

    async def fastapi_dispatch(scope, receive, send):
        # Forward every non-/pasta/mcp request (incl. the /ws/reloader websocket) to the
        # freshly reloaded FastAPI app, re-pulled per request.
        nonlocal last_fastapi_app
        fastapi_app = current_fastapi()
        # src.server re-executing on reload rebuilds `app` as a new object; a changed
        # identity is the reload signal. Log the first request that observes each reload
        # (the initial startup request is skipped since there is nothing to compare to).
        if last_fastapi_app is not None and fastapi_app is not last_fastapi_app:
            logger.info("[HMR] FastAPI app reloaded (src.server.app)")
        last_fastapi_app = fastapi_app
        await fastapi_app(scope, receive, send)

    return Starlette(
        routes=[Mount("/pasta", app=mcp_asgi), Mount("/", app=fastapi_dispatch)],
        lifespan=combine_lifespans(mcp_asgi.lifespan, reloader_lifespan),
    )


def run_dev_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Serve the combined HMR dev app. Dev-only; blocks until interrupted."""
    uvicorn.run(build_dev_app(), host=host, port=port, timeout_graceful_shutdown=1)
