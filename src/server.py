from __future__ import annotations

import os
import asyncio
from contextlib import contextmanager, asynccontextmanager
from typing import Any
from collections.abc import Generator
import traceback

from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, PlainTextResponse

from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans
from fastmcp.exceptions import ToolError

from . import cleanup
# Named import, not `from . import commands`: mutatePageBatch's own parameter would shadow it.
from .commands import transition_guidance
from .describe import describe_mutations, describe_page_type
from .errors import PastaError
from .hmr_live_refresh import ws_reloader
from .pagetypes.core.specs import status_guidance
from .pagetypes._registry import get_page_type, registered_tags, validate_registry
from .render import escape_markdown, render_workspace_links
from .render_html import md2html
from .serialize import page_to_dict
from .store import Store

# Fail fast: validate every page type once at load. This runs on a cold start and re-runs on
# every HMR reload (this module re-executes then), so a misconfigured type surfaces every error
# at once instead of piecemeal during a later request.
validate_registry()

DATA_DIR = os.environ.get("PASTA_DATA_DIR", ".pasta-data")
STORE = Store(DATA_DIR)


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    # Covers plain ASGI hosting; under the HMR dev server this never fires.
    cleanup.start_scheduler(STORE)
    yield
    await cleanup.stop_scheduler()

mcp: FastMCP = FastMCP("pasta")
mcp_app = mcp.http_app(path="/mcp")

app = FastAPI(
    title="Pasta Wiki with MCP",
    lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan))

app.mount("/static", StaticFiles(directory="src/static"), name="static")
app.mount("/sphinx", StaticFiles(directory="docsite/_build/html"), name="sphinx")

templates = Jinja2Templates(directory="src/templates")


# --- No HTTP caching ---------------------------------------------------------
# The server is only ever hosted locally, so browser caching buys nothing and has
# been serving stale images. Stamp a no-cache header on most responses. This wraps
# the /static and /sphinx StaticFiles mounts too (where image files live) - the
# per-route responses alone wouldn't cover those. CSS stylesheet is excepted.
# BaseHTTPMiddleware only sees HTTP scopes, the /ws/reloader websocket passes through.
@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.endswith(".css"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


# --- Websocket reloader ------------------------------------------------------
# The browser-facing connection manager (`ws_reloader`) lives in src.hmr_live_refresh so its
# live connections survive hot reloads. Under hot-module-reload (src.hmr_server) the
# refresh is fired on file changes; the mutation tools below also fire it on data changes.

@app.websocket("/ws/reloader")
async def fastapi_reloader(websocket: WebSocket):
    await ws_reloader.connect(websocket)

    async def send_updates():
        while True:
            await websocket.send_text('{"refresh": 0}')
            await asyncio.sleep(5)

    task = asyncio.create_task(send_updates())
    try:
        while True:
            _ = await websocket.receive_text()  # receive and do nothing
    except WebSocketDisconnect:
        _ = task.cancel()


# --- FastAPI routes ----------------------------------------------------------
@contextmanager
def _guard_http() -> Generator[None]:
    """Translate unexpected errors into internal errors with refresh support."""
    try:
        yield
    except Exception as exc:
        tb = traceback.TracebackException(type(exc), exc, exc.__traceback__)
        raise InternalError(tb)


@app.get("/", response_class=HTMLResponse)
async def route_index(request: Request, archived: str | None = None):
    with _guard_http():
        show_archived = True if archived == "true" else False
        body = md2html.render("\n\n".join(f"[{escape_markdown(x['name'])}](/{x['id']})" for x in STORE.list_workspaces()))
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "show_archived": show_archived,
                "body": body,
            }
        )


@app.get("/ws:{workspaceIdPart}", response_class=HTMLResponse)
async def route_tree(request: Request, workspaceIdPart: str, archived: str | None = None, markdown: str | None = None):
    with _guard_http():
        workspace_id = f"ws:{workspaceIdPart}"
        workspace = STORE.load_workspace(workspace_id)
        show_archived = True if archived == "true" else False
        pages_tree = STORE.tree(workspace_id, show_archived)
        body = md2html.render(render_workspace_links(pages_tree, show_archived, show_meta=True, escape_plain_text=True))
        return templates.TemplateResponse(
            request=request,
            name="tree.html",
            context={
                "workspace_id": workspace_id,
                "workspace_name": workspace.name,
                "show_archived": show_archived,
                "body": body,
            }
        )


@app.get("/ws:{workspaceIdPart}/page/{pageId}", response_class=HTMLResponse)
async def route_page(request: Request, workspaceIdPart: str, pageId: str, archived: str | None = None, markdown: str | None = None):
    with _guard_http():
        workspace_id = f"ws:{workspaceIdPart}"
        workspace = STORE.load_workspace(workspace_id)
        page = STORE.get_page(workspace_id, pageId)
        page_type = get_page_type(page.type)
        show_archived = True if archived == "true" else False
        nav = md2html.render(render_workspace_links(STORE.tree(workspace_id, show_archived), show_archived, show_meta=False, escape_plain_text=True))
        body = STORE.render_html(workspace_id, pageId, show_archived)
        if markdown == "true":
            body = md2html.render(STORE.render_markdown(workspace_id, pageId, show_archived, escape_plain_text=True))
        return templates.TemplateResponse(
            request=request,
            name="page.html",
            context={
                "workspace_id": workspace_id,
                "workspace_name": workspace.name,
                "show_archived": show_archived,
                "nav": nav,
                "body": body,
                # Drives the Archive/Unarchive button at the bottom of the page (see page.html).
                "page_id": page.id,
                "archived": page.archived,
                # Drives the status dropdown next to the Archive button: every status of this
                # page's type, with the current one preselected.
                "statuses": page_type.fsm.states if page_type is not None else (),
                "status": page.status,
                # The Model overlay loads the docsite page for the page's type AND current status.
                "page_type_doc": f"{page.type}-{page.status}",
            },
        )


# Archive/unarchive a page from its web view. These mirror the archivePage / unarchivePage MCP
# tools (a browser can't call MCP), so the wiki's Archive/Unarchive button POSTs here. Each fires
# the live-reload refresh like the MCP tools do, then 303-redirects back to the page (GET) so a
# reload/back-button doesn't re-POST the mutation.
@app.post("/ws:{workspaceIdPart}/page/{pageId}/archive", response_class=PlainTextResponse)
async def route_archive_page(workspaceIdPart: str, pageId: str):
    with _guard_http():
        workspace_id = f"ws:{workspaceIdPart}"
        STORE.archive_page(workspace_id, pageId)
        await ws_reloader.refresh()
        return PlainTextResponse(status_code=202)


@app.post("/ws:{workspaceIdPart}/page/{pageId}/unarchive", response_class=PlainTextResponse)
async def route_unarchive_page(workspaceIdPart: str, pageId: str):
    with _guard_http():
        workspace_id = f"ws:{workspaceIdPart}"
        STORE.unarchive_page(workspace_id, pageId)
        await ws_reloader.refresh()
        return PlainTextResponse(status_code=202)


# Directly set a page's lifecycle status from its web view. Backs the status dropdown + Apply button
# next to the Archive control (see page.html): a deliberate FSM-bypassing admin override, so a human
# can force any of the type's declared statuses. `status` arrives as a form field. Like the archive
# routes it fires the live-reload refresh and 202s (no MCP equivalent - a browser can't call MCP).
@app.post("/ws:{workspaceIdPart}/page/{pageId}/status", response_class=PlainTextResponse)
async def route_set_page_status(workspaceIdPart: str, pageId: str, status: str = Form(...)):
    with _guard_http():
        workspace_id = f"ws:{workspaceIdPart}"
        STORE.set_page_status(workspace_id, pageId, status)
        await ws_reloader.refresh()
        return PlainTextResponse(status_code=202)


# --- FastAPI exception handlers ----------------------------------------------
class InternalError(Exception):
    tb: traceback.TracebackException

    def __init__(self, tb: traceback.TracebackException):
        super().__init__()
        self.tb = tb


@app.exception_handler(InternalError)
async def http_exception_handler(request: Request, exc: InternalError):
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={
            "message": "".join(exc.tb.format_exception_only()).strip(),
            "trace": "".join(exc.tb.format(chain=True)).strip(),
        },
        status_code=500
    )


# --- MCP -------------------------------------------------------------------
app.mount("/pasta", mcp_app)  # MCP endpoint at /pasta/mcp


@contextmanager
def _guard_tool() -> Generator[None]:
    """Translate expected domain errors into client-visible tool errors."""
    try:
        yield
    except PastaError as exc:
        raise ToolError(str(exc)) from exc


# --- Reads -------------------------------------------------------------------
@mcp.tool
async def instructions() -> str:
    """Retrieve instructions for pasta MCP."""
    return """A **structured wiki** as the main project documentation with a FSM driven workflows:

- **Find** the right workspace `listWorkspaces`; **inspect page types and authoring** with `describePageType` / `describeMutations`.
- **Orient to the pasta MCP documentation:** `tree` to list the workspace contents, find the overall project document, read with `renderMarkdown`.
- **Pasta is the main documentation:** `search` to find specific words by prefix, `renderMarkdown` to read entire pages. Always ground in existing documentation.
- **Self-directing:** the FSM encodes next steps, surfaced via `nextActions` / `next` results on writes and creates. Do not ask the user which step comes next.
- **"Agency" comes straight from the metadata:** (`agency: "agent" | "human"`). Agent edges are yours to drive; human edges are real stopping points.

## Page types

- **Always use a page type to initiate workflows before grounding in code:** this drives the self-directing FSM, create an appropriate page unless the user explicitly asks to skip using the pasta MCP.
- **The authoring responses decide the next page edit - you don't.** After every write, read the echoed `next`; call `nextActions` when unsure for `do` / `blocked` / `humanGates` / `attention`. Drive `do` edges as needed, following instructions; for each `blocked` edge author exactly the content its `reason` names, then check the `next` result. Never hardcode a command sequence - if a `reason` changes, follow the new one.
- **Stop at human gates.** `humanGates` and `attention` items are not yours to cross. Drive up to them, then stop and hand back with a summary.
- **Gates must reflect reality.** `markStepDone` only after the step's code is written; `markCasePassed` only when a test genuinely passed; Default to *not* advancing when unsure.

**How to apply:** Make these instructions the very first actions of a new user request, ahead of code exploration and grounding. Evaluate the page type to use, then create it and following the authoring `do` / `blocked` edges.

**Why:** The user runs a structured workflow discipline in the pasta MCP - features, documentation, ADRs and architecture pages all live there, and the MCP is self-directing. Working without that process leads to work that ignores the established discipline or bases it outside the established architecture (e.g. reading drifted code comments instead of the architecture and documentation pages). If pasta MCP is not loaded, stop.

## Use Pasta: Red Flags

These thoughts mean STOP — you're rationalizing how to continue without using pasta:

| Thought | Reality |
|---------|---------|
| "I need more context first" | Pasta search comes BEFORE clarifying questions. |
| "Let me explore the codebase first" | Pasta pages tell you WHEN to explore. Create a page first. |
| "I can check git/files quickly" | Files lack conversation context. Check for pasta pages. |
| "Let me gather information first" | Pasta tell you WHEN to gather information. |
| "This doesn't count as a task" | Action = task. Create a pasta page. |
| "The discipline is overkill" | Simple things become complex. Create a pasta page. |
| "I'll just do this one thing first" | Create a page first, then do the thing if it still fits. |
| "This feels productive" | Undisciplined action wastes time. Pasta prevents this. |
| "I know what that means" | Knowing the concept is not following the process. Invoke pasta. |

## Pasta setup

If calling `tree` reveals an empty workspace, no project overview doc or the `toc` pages: Documentation, ADRs, Features and Bug Reports - suggest to create and setup the workspace with these `toc` pages and a "Project Overview" page at the users request. The workspace page structure is:

- Project Overview + `architecture` and `document` pages go under the Documentation `toc` page.
- `decision-record` pages go under the ADRs `toc` page.
- `feature-brief`, `simple-change` and `epic` pages go under the Features `toc` page.
- An `epic`'s `feature-brief` children are created under the epic itself, NOT under the Features `toc` page - the epic's ship gate only sees briefs that are its own children.
- `bug-report` pages go under the Bug Reports `toc` page.

## Response shapes

`describePageType` returns:

- **Page-type commands** - the intended authoring surface. They carry a real args schema, a description, a target section/field, and for transitions the FSM event they fire. **These are what you should call.**
- **A command's `description` is a short label**, not guidance. The authoring instruction for a field lives on that field in the `sections` listing, and is echoed as `instruction` on a `next` field edge - read it there before authoring.

`mutatePageBatch` returns:

- **`mutatePageBatch` success message:** _"Ran N command(s) … in one atomic commit."_ (All-or-nothing: a rejected command aborts the whole batch, and the error names the failing index + command + reason.)
- **Every successful write echoes a `next` summary object same as `nextActions`:** `{ do: [...], blocked: [...], humanGates: [...], attention: [...] }`.

`createPage` returns:

- **Created page id and children:** author into those.
- **Every successful create echoes a `next` summary object same as `nextActions`:** `{ do: [...], blocked: [...], humanGates: [...], attention: [...] }`.

## A summary of `describePageType` to quickly evaluate against user requests

architecture: Documents a part of the system that already exists in the codebase: its purpose, data model, code references, dependencies, and whether it is current or has drifted.

document: A general-purpose prose page for content that doesn't fit a typed page - notes, guides, references, narratives. The richest block-editing surface in the wiki.

toc: A table-of-contents container whose only content is the child pages placed under it. It holds no subject matter of its own and has no authoring commands - pages are filed by reparenting them beneath the toc, and its Child pages list IS the table of contents.

bug-report: Tracks a defect in existing behavior - what's wrong, how to reproduce it, and its resolution.

simple-change: Tracks a small, self-contained change or minor feature. Use this page type ONLY when the user specifically asks to make a small/simple change or a small/simple feature; for larger work create a feature-brief, and for a defect in existing behavior use a bug-report.

feature-brief: The root of a feature the user intends to build - drives new work from intent through grounding, planning, and a plan review to build and a human ship gate. Lifecycle transitions are gated on the required content for that stage being present first.

implementation-plan: The step-by-step build plan for a feature. Auto-created as a child of a feature-brief.

testing-plan: The verification cases for a feature. Auto-created as a child of a feature-brief.

feature-spec: The detailed product/UX specification for a feature, authored during planning on top of the grounded base and sealed before the plan review. Auto-created as a child of a feature-brief.

epic: The root of a major feature too large for one feature-brief - it decomposes into several child feature-briefs and is built by subagents dispatched from its pinned agent plan. Use it when the work splits into parts that each ship on their own; when it does not, use a feature-brief.

agent-plan: Which subagents an epic creates, the order they are dispatched in, and how their results are reported back onto the feature-briefs. Auto-created as a child of an epic.
"""


@mcp.tool
async def listWorkspaces() -> list[dict[str, str]]:
    """List all workspaces (id, name, status)."""
    with _guard_tool():
        return STORE.list_workspaces()


@mcp.tool
async def tree(workspaceId: str) -> dict[str, Any]:
    """The ordered page tree (title, type, status, id) of LIVE work. Archived pages and their
    subtrees are always hidden - there is no flag to include them, because the archived tail is
    history and returning it inline buries the active pages. To reach an archived page, use
    `getPage` with its id (which does not filter on archived), or the web UI's ?archived=true
    view."""
    with _guard_tool():
        return STORE.tree(workspaceId)


@mcp.tool
async def getPage(workspaceId: str, pageId: str) -> dict[str, Any]:
    """Fetch one page's projected state (type, title, status, sections). Large pages exceed
    the tool response limit and are **persisted to a temp JSON file on disk** instead of being
    inlined."""
    with _guard_tool():
        return page_to_dict(STORE.get_page(workspaceId, pageId))


@mcp.tool
async def describePageType(type: str | None = None) -> dict[str, Any]:
    """Describe a page type's sections, fields, commands, and FSM. Omit `type` to list types."""
    with _guard_tool():
        if type is None:
            return {"types": registered_tags()}
        page_type = get_page_type(type)
        if page_type is None:
            raise ToolError(f"Unknown page type '{type}'. Registered: {', '.join(registered_tags())}.")
        return describe_page_type(page_type)


@mcp.tool
async def describeMutations(workspaceId: str, pageId: str) -> dict[str, Any]:
    """List the commands a page can run now - each with its arg schema and current legality."""
    with _guard_tool():
        page = STORE.get_page(workspaceId, pageId)
        page_type = get_page_type(page.type)
        if page_type is None:
            raise ToolError(f"Page '{pageId}' has unregistered type '{page.type}'.")
        return {
            "pageId": page.id,
            "type": page.type,
            "status": page.status,
            "commands": describe_mutations(page, page_type),
        }


@mcp.tool
async def outline(workspaceId: str, pageId: str) -> dict[str, Any]:
    """A page's section tree (key, name, order, field kinds). Structure only, no body content."""
    with _guard_tool():
        return STORE.outline(workspaceId, pageId)


@mcp.tool
async def renderPage(workspaceId: str, pageId: str | None = None) -> dict[str, str]:
    """Render a page to Markdown; omit `pageId` to render the whole (non-archived) workspace tree.
    Large pages exceed the tool response limit and are **persisted to a temp JSON file on disk**
    instead of being inlined."""
    with _guard_tool():
        return {"markdown": STORE.render_markdown(workspaceId, pageId)}


@mcp.tool
async def search(workspaceId: str, query: str, limit: int = 20) -> dict[str, Any]:
    """Full-text search over page content in a workspace: ranked hits with a snippet each.
    Case-insensitive, matches by word prefix, and excludes archived pages AND their descendants -
    the same subtree rule `tree` applies, so the two agree on what is live. Prefix the query
    with `id:` to resolve a full or partial page id instead (e.g. `id:msakene4`); id search
    spans archived pages too, and every hit carries an `archived` flag."""
    with _guard_tool():
        return STORE.search(workspaceId, query, limit)


@mcp.tool
async def nextActions(workspaceId: str, pageId: str | None = None) -> dict[str, Any]:
    """Roll up FSM edges over a page's subtree (or the whole workspace) into `do` (agent edges
    legal now), `blocked` (agent edges with the unmet precondition to fix), `humanGates`
    (sign-off edges - stop), and `attention` (items awaiting a human)."""
    with _guard_tool():
        return STORE.next_actions(workspaceId, pageId)


@mcp.tool
async def attention(workspaceId: str) -> dict[str, Any]:
    """Scan a workspace for element instances awaiting a human (e.g. an escalated open question)."""
    with _guard_tool():
        return STORE.attention(workspaceId)


# --- Writes ------------------------------------------------------------------
@mcp.tool
async def createWorkspace(name: str) -> dict[str, str]:
    """Create a new workspace and return its id."""
    with _guard_tool():
        workspace = STORE.create_workspace(name)
        await ws_reloader.refresh()
        return {"id": workspace.id, "name": workspace.name, "status": workspace.status}


@mcp.tool
async def createPage(workspaceId: str, type: str, title: str, parentId: str | None = None) -> dict[str, Any]:
    """Create a new page of a registered type, optionally under a parent.
    If the type declares pinned children, they are auto-created in the same commit and returned
    under `children` - author into those, do not create your own. Returns the page id, status
    and next actions.
    """
    with _guard_tool():
        result = STORE.create_page(workspaceId, type, title, parentId)
        page = result.page
        next_actions = STORE.next_actions(workspaceId, page.id)
        page_type = get_page_type(page.type)
        await ws_reloader.refresh()
        response: dict[str, Any] = {
            "id": page.id,
            "type": page.type,
            "title": page.title,
            "status": page.status,
            "parentId": page.parent_id,
            "statusRevisionToken": page.status_revision_token,
            # Guidance is not shown for auto-pinned child pages.
            "children": [
                {"id": child.id, "type": child.type, "title": child.title, "status": child.status}
                for child in result.children
            ],
            "next": next_actions,
        }
        # Creating a page enters a status, so its initial guidance echoes here too.
        guidance = status_guidance(page_type.fsm, page.status) if page_type is not None else None
        if guidance is not None:
            response["guidance"] = guidance
        response.update(STORE.page_workspace_guidance(workspaceId, page))
        return response


@mcp.tool
async def mutatePageBatch(
    workspaceId: str, pageId: str, commands: list[dict[str, Any]]
) -> dict[str, Any]:
    """Run an ordered batch of commands on a page as a single atomic commit (each `{command, args}`
    decided against the state left by the previous). Every command must carry the page's current
    `statusRevisionToken` as the first entry in its `args` - a short token read from getPage, the
    render* meta line, or a prior write/nextActions echo. A status transition regenerates it, so at
    most one transition is legal per batch and only as the final command: a command after a transition
    carries a now-stale token. All-or-nothing: any rejection aborts the whole batch and nothing commits
    - the error names the failing index and command. Echoes the new status, the current
    `statusRevisionToken`, and next actions."""
    with _guard_tool():
        page, created = STORE.mutate_page_batch(workspaceId, pageId, commands)
        page_type = get_page_type(page.type)
        next_actions = STORE.next_actions(workspaceId, pageId)
        await ws_reloader.refresh()
        response: dict[str, Any] = {
            "pageId": page.id,
            "status": page.status,
            "statusRevisionToken": page.status_revision_token,
            "count": len(created),
            "createdIds": created,
            "next": next_actions,
        }
        # The stage guidance for a status just entered, beside `next`.
        guidance = (transition_guidance(page_type, commands, page.status)
                    if page_type is not None else None)
        if guidance is not None:
            response["guidance"] = guidance
        response.update(STORE.page_workspace_guidance(workspaceId, page))
        return response


# --- Workspace guidance configuration ----------------------------------------
@mcp.tool
async def setWorkspaceGuidance(workspaceId: str, field: str, text: str) -> dict[str, Any]:
    """Set the workspace's stored guidance text for a configurable guidance field.

    An unknown field is rejected, listing the ones that are declared. `text` is a single string,
    and an empty string clears the field. Once set, the text is surfaced to pages that declare the
    field while they sit at one of its statuses. Returns the workspace id, the field, and the config.
    """
    with _guard_tool():
        workspace = STORE.set_workspace_guidance(workspaceId, field, text)
        await ws_reloader.refresh()
        return {"workspaceId": workspace.id, "field": field,
                "guidanceConfig": dict(workspace.guidance_config)}


# --- Archiving ---------------------------------------------------------------
@mcp.tool
async def archiveWorkspace(workspaceId: str) -> dict[str, str]:
    """Archive a whole workspace - mark it archived in listings. Reversible; pages preserved."""
    with _guard_tool():
        workspace = STORE.archive_workspace(workspaceId)
        await ws_reloader.refresh()
        return {"id": workspace.id, "name": workspace.name, "status": workspace.status}


@mcp.tool
async def unarchiveWorkspace(workspaceId: str) -> dict[str, str]:
    """Unarchive a previously archived workspace - restore it to active. Runnable while archived."""
    with _guard_tool():
        workspace = STORE.unarchive_workspace(workspaceId)
        await ws_reloader.refresh()
        return {"id": workspace.id, "name": workspace.name, "status": workspace.status}


@mcp.tool
async def archivePage(workspaceId: str, pageId: str) -> dict[str, Any]:
    """Archive a page - hide it (and its subtree) from default tree views. It cannot be mutated
    while archived (unarchive first). Reversible."""
    with _guard_tool():
        page = STORE.archive_page(workspaceId, pageId)
        await ws_reloader.refresh()
        return {"id": page.id, "archived": page.archived}


@mcp.tool
async def unarchivePage(workspaceId: str, pageId: str) -> dict[str, Any]:
    """Unarchive a page - restore it to default tree views. Lifecycle status is unchanged."""
    with _guard_tool():
        page = STORE.unarchive_page(workspaceId, pageId)
        await ws_reloader.refresh()
        return {"id": page.id, "archived": page.archived}


# --- Page-tree structure -----------------------------------------------------
@mcp.tool
async def reparentPage(
    workspaceId: str, pageId: str, newParentId: str | None = None
) -> dict[str, Any]:
    """Move a page under a new parent (or to top level when newParentId is null), appended to the
    new parent's children - use reorderPage to position it. Rejects a cycle (the new parent being
    the page or one of its descendants). Sibling titles are not reserved."""
    with _guard_tool():
        page, sibling_ids = STORE.reparent_page(workspaceId, pageId, newParentId)
        await ws_reloader.refresh()
        return {"id": page.id, "parentId": page.parent_id, "siblingIds": sibling_ids}


@mcp.tool
async def reorderPage(
    workspaceId: str, pageId: str, toIndex: int, precedingId: str | None = None
) -> dict[str, Any]:
    """Move a page to an anchored position among its siblings, mirroring block/element reorder:
    toIndex is the resting index and precedingId is the sibling expected just before it (null for
    the front). A drifted index or a mismatched precedingId is rejected as a stale read."""
    with _guard_tool():
        page, sibling_ids = STORE.reorder_page(workspaceId, pageId, toIndex, precedingId)
        await ws_reloader.refresh()
        return {"id": page.id, "parentId": page.parent_id, "siblingIds": sibling_ids}


@mcp.tool
async def renamePage(workspaceId: str, pageId: str, title: str) -> dict[str, Any]:
    """Change a page's title. Sibling titles are not reserved - renaming to a title already used by
    a sibling is allowed (titles are display labels, not identifiers). Works on archived and pinned
    pages. Rejects a blank title."""
    with _guard_tool():
        page = STORE.rename_page(workspaceId, pageId, title)
        await ws_reloader.refresh()
        return {"id": page.id, "title": page.title}


# --- Page reference graph ----------------------------------------------------
@mcp.tool
async def link(workspaceId: str, fromId: str, toId: str, role: str) -> dict[str, Any]:
    """Create a typed reference link `fromId --role--> toId`: a directed edge between two pages beyond
    the parent/child tree, listed in the source page's 'References' section. The source must be
    non-archived; the target may be archived. Rejects a self-link, an empty role, and a duplicate
    (toId, role) edge."""
    with _guard_tool():
        page, links = STORE.link_page(workspaceId, fromId, toId, role)
        await ws_reloader.refresh()
        return {"id": page.id, "links": links}


@mcp.tool
async def unlink(workspaceId: str, fromId: str, toId: str, role: str) -> dict[str, Any]:
    """Remove the typed reference link `fromId --role--> toId`. Rejects a missing endpoint, an
    archived source, or an edge that isn't present."""
    with _guard_tool():
        page, links = STORE.unlink_page(workspaceId, fromId, toId, role)
        await ws_reloader.refresh()
        return {"id": page.id, "links": links}


# --- capture HMR reload errors to hmr_debug.log (see src/_hmr_debug.py) -------
from . import _hmr_debug  # noqa: E402, F401
