from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.schemas import (
    ChatMessageCreateRequest,
    DispatchOrderRequest,
    DispatcherIncidentMarkRequest,
    HoseCreateRequest,
    HoseUpdateRequest,
    NozzleCreateRequest,
    NozzleUpdateRequest,
    ParticipantRoleUpdateRequest,
    ScenarioEventCreateRequest,
    VehicleDriveIntentRequest,
    VehicleRouteRequest,
)
from app.config import Settings, get_settings
from app.db.models import AuthSession, TrainingSession
from app.db.session import get_db
from app.domain.session_flow_v2 import (
    apply_vehicle_object_drive,
    assign_participant_role,
    create_dispatch_order,
    create_chat_message,
    create_hose,
    create_nozzle,
    create_runtime_event,
    get_current_auth_session,
    list_chat_messages,
    get_session_state_payload,
    get_session_with_related,
    mark_dispatcher_incident_guess,
    start_training_drill,
    update_hose,
    update_nozzle,
    update_vehicle_object_route,
)
from app.security.csrf import validate_csrf


router = APIRouter(prefix="/api/sessions", tags=["sessions"])
DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def _load_context(
    request: Request,
    db: Session,
    settings: Settings,
    session_code: str,
) -> tuple[AuthSession, TrainingSession]:
    auth_session = get_current_auth_session(db, request.cookies.get(settings.session_cookie_name))
    if auth_session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация.")
    training_session = get_session_with_related(db, session_code)
    if training_session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сессия не найдена.")
    if training_session.id != auth_session.training_session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ запрещён.")
    return auth_session, training_session


@router.get("/{session_code}/state")
def get_session_state(
    session_code: str,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session, training_session = _load_context(request, db, settings, session_code)
    payload = get_session_state_payload(training_session, viewer_role=auth_session.role)
    payload["current_role"] = auth_session.role
    payload["scenario"] = {
        "incident_revealed": training_session.scenario_state.incident_revealed if training_session.scenario_state else False,
        "dispatcher_guess_index": training_session.scenario_state.dispatcher_guess_index if training_session.scenario_state else None,
        "dispatcher_guess_correct": training_session.scenario_state.dispatcher_guess_correct if training_session.scenario_state else None,
    }
    return payload


@router.post("/{session_code}/participants/{participant_id}/role")
async def post_participant_role(
    session_code: str,
    participant_id: str,
    payload: ParticipantRoleUpdateRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session, training_session = _load_context(request, db, settings, session_code)
    await validate_csrf(request, auth_session, settings)
    participant = assign_participant_role(
        db,
        training_session=training_session,
        auth_session=auth_session,
        participant_id=participant_id,
        role=payload.role,
        ip_address=request.client.host if request.client else None,
    )
    await request.app.state.ws_manager.broadcast(
        session_code,
        {"type": "participant_role_updated", "participant_id": participant.id, "role": participant.role},
    )
    return {"ok": True, "participant_id": participant.id, "role": participant.role}


@router.post("/{session_code}/start")
async def post_start_session(
    session_code: str,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session, training_session = _load_context(request, db, settings, session_code)
    await validate_csrf(request, auth_session, settings)
    scenario_state = start_training_drill(
        db,
        training_session=training_session,
        auth_session=auth_session,
        ip_address=request.client.host if request.client else None,
    )
    request.app.state.runtime_manager.start(training_session)
    await request.app.state.ws_manager.broadcast(
        session_code,
        {"type": "session_phase_changed", "status": scenario_state.status},
    )
    return {"ok": True, "status": scenario_state.status}


@router.post("/{session_code}/events")
async def post_runtime_event(
    session_code: str,
    payload: ScenarioEventCreateRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session, training_session = _load_context(request, db, settings, session_code)
    await validate_csrf(request, auth_session, settings)
    event_payload = create_runtime_event(
        db,
        training_session=training_session,
        auth_session=auth_session,
        event_type=payload.event_type,
        payload=payload.payload,
        ip_address=request.client.host if request.client else None,
    )
    await request.app.state.ws_manager.broadcast(session_code, {"type": "event_created", **event_payload})
    return {"ok": True, "event": event_payload}


@router.post("/{session_code}/dispatcher/mark-incident")
async def post_dispatcher_mark_incident(
    session_code: str,
    payload: DispatcherIncidentMarkRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session, training_session = _load_context(request, db, settings, session_code)
    await validate_csrf(request, auth_session, settings)
    scenario_state = mark_dispatcher_incident_guess(
        db,
        training_session=training_session,
        auth_session=auth_session,
        guess_index=payload.guess_index,
        ip_address=request.client.host if request.client else None,
    )
    await request.app.state.ws_manager.broadcast(
        session_code,
        {
            "type": "session_phase_changed",
            "status": scenario_state.status,
            "dispatcher_guess_index": scenario_state.dispatcher_guess_index,
            "dispatcher_guess_correct": scenario_state.dispatcher_guess_correct,
        },
    )
    return {
        "ok": True,
        "status": scenario_state.status,
        "dispatcher_guess_index": scenario_state.dispatcher_guess_index,
        "dispatcher_guess_correct": scenario_state.dispatcher_guess_correct,
    }


@router.post("/{session_code}/dispatch/orders")
async def post_dispatch_order(
    session_code: str,
    payload: DispatchOrderRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session, training_session = _load_context(request, db, settings, session_code)
    await validate_csrf(request, auth_session, settings)
    result = create_dispatch_order(
        db,
        training_session=training_session,
        auth_session=auth_session,
        counts=payload.counts,
        ip_address=request.client.host if request.client else None,
    )
    await request.app.state.ws_manager.broadcast(
        session_code,
        {"type": "vehicle_path_updated", "status": result["status"], "counts": payload.counts, "vehicle_ids": result["vehicle_ids"]},
    )
    return {"ok": True, **result}


@router.get("/{session_code}/chat/messages")
def get_chat_messages(
    session_code: str,
    thread_key: str,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session, training_session = _load_context(request, db, settings, session_code)
    return {"items": list_chat_messages(db, training_session=training_session, auth_session=auth_session, thread_key=thread_key)}


@router.post("/{session_code}/chat/messages")
async def post_chat_message(
    session_code: str,
    payload: ChatMessageCreateRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session, training_session = _load_context(request, db, settings, session_code)
    await validate_csrf(request, auth_session, settings)
    message = create_chat_message(
        db,
        training_session=training_session,
        auth_session=auth_session,
        thread_key=payload.thread_key,
        body=payload.body,
        ip_address=request.client.host if request.client else None,
    )
    message_payload = {
        "id": message.id,
        "thread_key": message.thread_key,
        "body": message.body,
        "participant_id": message.participant_id,
        "created_at": message.created_at.isoformat(),
    }
    await request.app.state.ws_manager.broadcast(session_code, {"type": "chat_message_created", "message": message_payload})
    return {"ok": True, "message": message_payload}


@router.post("/{session_code}/vehicles/{vehicle_id}/object-route")
async def post_vehicle_object_route(
    session_code: str,
    vehicle_id: str,
    payload: VehicleRouteRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session, training_session = _load_context(request, db, settings, session_code)
    await validate_csrf(request, auth_session, settings)
    vehicle = update_vehicle_object_route(
        db,
        training_session=training_session,
        auth_session=auth_session,
        vehicle_id=vehicle_id,
        route_points=[{"x": point.x, "y": point.y} for point in payload.points],
        ip_address=request.client.host if request.client else None,
    )
    await request.app.state.ws_manager.broadcast(
        session_code,
        {"type": "vehicle_state_changed", "vehicle_id": vehicle.id, "status": vehicle.status, "route_json": vehicle.route_json},
    )
    return {"ok": True, "vehicle_id": vehicle.id, "status": vehicle.status}


@router.post("/{session_code}/vehicles/{vehicle_id}/object-drive")
async def post_vehicle_object_drive(
    session_code: str,
    vehicle_id: str,
    payload: VehicleDriveIntentRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session, training_session = _load_context(request, db, settings, session_code)
    await validate_csrf(request, auth_session, settings)
    vehicle = apply_vehicle_object_drive(
        db,
        training_session=training_session,
        auth_session=auth_session,
        vehicle_id=vehicle_id,
        heading_deg=payload.heading_deg,
        speed_mps=payload.speed_mps,
        ip_address=request.client.host if request.client else None,
    )
    await request.app.state.ws_manager.broadcast(
        session_code,
        {
            "type": "vehicle_state_changed",
            "vehicle_id": vehicle.id,
            "status": vehicle.status,
            "heading_deg": vehicle.heading_deg,
            "speed_mps": vehicle.speed_mps,
        },
    )
    return {"ok": True, "vehicle_id": vehicle.id, "status": vehicle.status}


@router.post("/{session_code}/hoses")
async def post_hose(
    session_code: str,
    payload: HoseCreateRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session, training_session = _load_context(request, db, settings, session_code)
    await validate_csrf(request, auth_session, settings)
    hose = create_hose(
        db,
        training_session=training_session,
        auth_session=auth_session,
        source_vehicle_id=payload.source_vehicle_id,
        polyline_points=[{"x": point.x, "y": point.y} for point in payload.polyline_points],
        ip_address=request.client.host if request.client else None,
    )
    await request.app.state.ws_manager.broadcast(session_code, {"type": "hose_state_changed", "hose": hose})
    return {"ok": True, "hose": hose}


@router.put("/{session_code}/hoses/{hose_id}")
async def put_hose(
    session_code: str,
    hose_id: str,
    payload: HoseUpdateRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session, training_session = _load_context(request, db, settings, session_code)
    await validate_csrf(request, auth_session, settings)
    hose = update_hose(
        db,
        training_session=training_session,
        auth_session=auth_session,
        hose_id=hose_id,
        polyline_points=None if payload.polyline_points is None else [{"x": point.x, "y": point.y} for point in payload.polyline_points],
        flow_state=payload.flow_state,
        ip_address=request.client.host if request.client else None,
    )
    await request.app.state.ws_manager.broadcast(session_code, {"type": "hose_state_changed", "hose": hose})
    return {"ok": True, "hose": hose}


@router.post("/{session_code}/nozzles")
async def post_nozzle(
    session_code: str,
    payload: NozzleCreateRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session, training_session = _load_context(request, db, settings, session_code)
    await validate_csrf(request, auth_session, settings)
    nozzle = create_nozzle(
        db,
        training_session=training_session,
        auth_session=auth_session,
        hose_id=payload.hose_id,
        target_x=payload.target_x,
        target_y=payload.target_y,
        flow_lps=payload.flow_lps,
        ip_address=request.client.host if request.client else None,
    )
    await request.app.state.ws_manager.broadcast(session_code, {"type": "fire_state_changed", "nozzle": nozzle})
    return {"ok": True, "nozzle": nozzle}


@router.put("/{session_code}/nozzles/{nozzle_id}")
async def put_nozzle(
    session_code: str,
    nozzle_id: str,
    payload: NozzleUpdateRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session, training_session = _load_context(request, db, settings, session_code)
    await validate_csrf(request, auth_session, settings)
    nozzle = update_nozzle(
        db,
        training_session=training_session,
        auth_session=auth_session,
        nozzle_id=nozzle_id,
        target_x=payload.target_x,
        target_y=payload.target_y,
        flow_lps=payload.flow_lps,
        ip_address=request.client.host if request.client else None,
    )
    await request.app.state.ws_manager.broadcast(session_code, {"type": "fire_state_changed", "nozzle": nozzle})
    return {"ok": True, "nozzle": nozzle}
