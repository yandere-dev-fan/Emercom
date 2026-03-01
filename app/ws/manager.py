from __future__ import annotations

from collections import defaultdict

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, session_code: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[session_code].add(websocket)

    def disconnect(self, session_code: str, websocket: WebSocket) -> None:
        connections = self._connections.get(session_code)
        if not connections:
            return
        connections.discard(websocket)
        if not connections:
            self._connections.pop(session_code, None)

    async def broadcast(self, session_code: str, message: dict[str, object]) -> None:
        for connection in list(self._connections.get(session_code, set())):
            await connection.send_json(message)

    async def broadcast_except(self, session_code: str, message: dict[str, object], exclude: WebSocket) -> None:
        """Broadcast to all clients EXCEPT the specified one (prevents self-notification loops)."""
        for connection in list(self._connections.get(session_code, set())):
            if connection is not exclude:
                await connection.send_json(message)

    def count(self, session_code: str) -> int:
        return len(self._connections.get(session_code, set()))
