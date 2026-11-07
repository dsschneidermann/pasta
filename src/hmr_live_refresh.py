"""Browser live-reload WebSocket connection manager.

This module is deliberately kept OUT of the hot-reload set: `src/hmr_server.py`
excludes it from the reactive import finder. It holds live browser WebSocket
connections as in-process state (`ws_reloader.active`); if it were re-executed on
every source change, that state would be dropped and browser auto-refresh would
silently stop working. The manager therefore lives here, imported by both the
reactively-reloaded `src.server` (routes + mutation refreshes) and the stable
`src.hmr_server` (file-change refreshes), so a single instance survives reloads.
"""

from __future__ import annotations

from fastapi import WebSocket


class ReloaderConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active.remove(websocket)

    async def refresh(self):
        dead: list[WebSocket] = []
        for conn in self.active:
            try:
                await conn.send_text('{"refresh": 1}')
            except Exception:
                dead.append(conn)
        for conn in dead:
            self.active.remove(conn)


# Process-wide singleton. Must survive hot reloads, so it lives in this
# (reload-excluded) module rather than in the reactively-reloaded server module.
ws_reloader = ReloaderConnectionManager()
