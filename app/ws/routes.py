from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import SessionLocal
from app.domain.services import get_session_for_code, load_auth_session


router = APIRouter()


@router.websocket("/ws/sessions/{session_code}")
async def session_socket(websocket: WebSocket, session_code: str) -> None:
    settings = get_settings()
    db: Session = SessionLocal()
    manager = websocket.app.state.ws_manager
    auth_session = None
    try:
        auth_session = load_auth_session(db, websocket.cookies.get(settings.session_cookie_name))
        if auth_session is None:
            await websocket.close(code=4401)
            return
        training_session = get_session_for_code(db, session_code)
        if training_session is None or training_session.id != auth_session.training_session_id:
            await websocket.close(code=4403)
            return
        csrf_cookie = websocket.cookies.get(settings.csrf_cookie_name)
        csrf_query = websocket.query_params.get("csrf")
        if not csrf_cookie or csrf_cookie != csrf_query or csrf_query != auth_session.csrf_token:
            await websocket.close(code=4403)
            return

        await manager.connect(session_code, websocket)
        await manager.broadcast(
            session_code,
            {"type": "participant_joined", "participant_id": auth_session.participant_id, "role": auth_session.role},
        )
        await manager.broadcast(
            session_code,
            {
                "type": "presence_state",
                "session_code": session_code,
                "connected": manager.count(session_code),
            },
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(session_code, websocket)
        await manager.broadcast(
            session_code,
            {"type": "participant_left", "participant_id": auth_session.participant_id if auth_session else None},
        )
        await manager.broadcast(
            session_code,
            {"type": "presence_state", "session_code": session_code, "connected": manager.count(session_code)},
        )
    finally:
        db.close()
