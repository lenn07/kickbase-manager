"""WebSocket-Endpoint für den Live-Log-Stream (Phase 6, F-6).

Der Client bekommt beim Connect zunächst die im Ringpuffer gehaltene History
(damit ein Reload nicht nackt aussieht) und danach jede neue Log-Zeile in
Echtzeit als JSON-Frame.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.infrastructure.logging import LogBroadcaster

router = APIRouter()

_log = logging.getLogger(__name__)


@router.websocket("/ws/logs")
async def logs_ws(websocket: WebSocket) -> None:
    broadcaster: LogBroadcaster = websocket.app.state.log_broadcaster
    await websocket.accept()
    queue = broadcaster.subscribe()
    try:
        for entry in broadcaster.history():
            await websocket.send_json(entry.to_dict())
        while True:
            try:
                entry = await asyncio.wait_for(queue.get(), timeout=30.0)
            except TimeoutError:
                # Keep-Alive: verhindert Proxy-Timeout ohne echten Traffic.
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(entry.to_dict())
    except WebSocketDisconnect:
        pass
    finally:
        broadcaster.unsubscribe(queue)
