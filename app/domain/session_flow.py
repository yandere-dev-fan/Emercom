from __future__ import annotations

import asyncio
from datetime import timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas import CreateSessionRequest
from app.config import Settings
from app.db.models import AuthSession, HostAdminSession, MapDocument, MapLevel, Participant, RuntimeEvent, ScenarioState, TrainingSession, VehicleInstance
from app.domain.services import _client_id, _create_auth_session, _unique_session_code, load_auth_session, record_audit
from app.domain.template_maps import clone_template_to_session_map, get_template_map
from app.domain.vehicle_catalog import VEHICLE_CATALOG
from app.security.auth import (
    generate_join_key,
    generate_random_token,
    hash_secret,
    make_expiry,
    parse_auth_cookie_value,
    utc_now,
    verify_host_admin_secret,
    verify_secret,
)
from app.security.rate_limit import InMemoryJoinKeyLimiter


SESSION_ROLES = {"instructor", "dispatcher", "rtp", "observer", "waiting"}
ASSIGNABLE_ROLES = {"dispatcher", "rtp", "observer", "waiting"}
PRIMARY_RUNTIME_STATUSES = {"setup", "dispatch_call", "enroute", "recon", "tactical", "contained", "finished"}


def create_host_admin_session(db: Session, *, settings: Settings, password: str) -> tuple[HostAdminSession, str]:
    if not verify_host_admin_secret(settings, password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный пароль.")
    raw_token = generate_random_token()
    host_session = HostAdminSession(
        token_hash=hash_secret(raw_token),
        csrf_token=generate_random_token(),
        expires_at=make_expiry(hours=max(settings.host_admin_cookie_max_age_seconds // 3600, 1)),
        last_seen_at=utc_now(),
    )
    db.add(host_session)
    db.commit()
    db.refresh(host_session)
    return host_session, raw_token


def load_host_admin_session(db: Session, raw_cookie: str | None) -> HostAdminSession | None:
    parsed = parse_auth_cookie_value(raw_cookie)
    if not parsed:
        return None
    host_session_id, raw_token = parsed
    host_session = db.get(HostAdminSession, host_session_id)
    if host_session is None:
        return None
    expires_at = host_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= utc_now():
        return None
    if not verify_secret(host_session.token_hash, raw_token):
        return None
    host_session.last_seen_at = utc_now()
    host_session.expires_at = utc_now() + timedelta(hours=12)
    db.commit()
    db.refresh(host_session)
    return host_session


def require_instructor(auth_session: AuthSession) -> None:
    if auth_session.role not in {"admin", "instructor"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Требуются права создателя сессии.")


def active_session_maps(training_session: TrainingSession) -> list[Any]:
    return [item for item in training_session.maps if item.scope == "session"]


def template_maps_for_session(training_session: TrainingSession) -> list[Any]:
    return [item for item in training_session.maps if item.scope == "template"]


def group_runtime_maps(training_session: TrainingSession) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {"area": [], "object": []}
    for map_document in sorted(active_session_maps(training_session), key=lambda item: item.created_at):
        grouped.setdefault(map_document.kind, []).append(map_document)
    return grouped


def _materialize_vehicle_instances(
    *,
    db: Session,
    training_session: TrainingSession,
    scenario_state: ScenarioState,
    enabled_vehicle_types: list[str],
) -> None:
    for vehicle_type in enabled_vehicle_types:
        spec = VEHICLE_CATALOG.get(vehicle_type)
        if spec is None:
            continue
        db.add(
            VehicleInstance(
                training_session=training_session,
                scenario_state=scenario_state,
                vehicle_type=spec["vehicle_type"],
                display_name=spec["display_name"],
                assigned_role="rtp",
                status="staged",
                current_map_id=scenario_state.area_map_id,
                current_level_code="AREA_MAIN",
                position_x=0.0,
                position_y=0.0,
                heading_deg=0,
                speed_mps=0.0,
                water_remaining_l=int(spec["water_capacity_l"]),
                foam_remaining_l=int(spec["foam_capacity_l"]),
                route_json={"points": []},
            )
        )


def create_training_session_from_templates(
    db: Session,
    *,
    payload: CreateSessionRequest,
    settings: Settings,
    ip_address: str | None,
) -> tuple[TrainingSession, AuthSession, str, str, ScenarioState]:
    join_key = generate_join_key()
    training_session = TrainingSession(session_code=_unique_session_code(db), join_key_hash=hash_secret(join_key))
    participant = Participant(training_session=training_session, role="instructor")
    db.add_all([training_session, participant])
    db.flush()

    area_template = get_template_map(db, payload.area_template_id)
    object_template = get_template_map(db, payload.object_template_id)
    if area_template is None or area_template.kind != "area":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карта местности не найдена.")
    if object_template is None or object_template.kind != "object":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карта объекта не найдена.")

    area_runtime_map = clone_template_to_session_map(db, template_map=area_template, training_session=training_session)
    db.flush()
    object_runtime_map = clone_template_to_session_map(
        db,
        template_map=object_template,
        training_session=training_session,
        parent_map_id=area_runtime_map.id,
    )
    db.flush()

    scenario_state = ScenarioState(
        training_session=training_session,
        status="setup",
        area_map_id=area_runtime_map.id,
        object_map_id=object_runtime_map.id,
        time_scale=payload.time_scale,
        time_elapsed_seconds=0,
        weather_kind=payload.weather_kind,
        wind_direction_deg=payload.wind_direction_deg,
        wind_speed_level=payload.wind_speed_level,
        time_of_day=payload.time_of_day,
        incident_area_index=payload.incident_area_index,
        incident_object_level_code=payload.incident_object_level_code,
        incident_object_index=payload.incident_object_index,
        incident_revealed=False,
        detection_radius_cells=payload.detection_radius_cells,
        available_vehicles_json={"enabled_vehicle_types": payload.enabled_vehicle_types},
        runtime_state_json={
            "vehicles": [],
            "hoses": [],
            "nozzles": [],
            "events": [],
            "voice": {"channels": {}, "barge_mode": False},
        },
    )
    db.add(scenario_state)
    db.flush()

    _materialize_vehicle_instances(
        db=db,
        training_session=training_session,
        scenario_state=scenario_state,
        enabled_vehicle_types=payload.enabled_vehicle_types,
    )

    auth_session, raw_token = _create_auth_session(db, training_session=training_session, participant=participant)
    record_audit(
        db,
        event_type="session_created",
        session_id=training_session.id,
        participant_id=participant.id,
        ip_address=ip_address,
        details={
            "session_code": training_session.session_code,
            "area_template_id": area_template.id,
            "object_template_id": object_template.id,
        },
    )
    db.commit()
    db.refresh(training_session)
    db.refresh(auth_session)
    db.refresh(scenario_state)
    return training_session, auth_session, raw_token, join_key, scenario_state


async def join_training_session(
    db: Session,
    *,
    join_key: str,
    ip_address: str | None,
    limiter: InMemoryJoinKeyLimiter,
    settings: Settings,
) -> tuple[TrainingSession, AuthSession, str]:
    client_id = _client_id(ip_address)
    if limiter.is_blocked(client_id):
        record_audit(db, event_type="session_join_blocked", ip_address=ip_address, details={"reason": "ip_blocked"})
        db.commit()
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Слишком много неудачных попыток.")

    matched_session: TrainingSession | None = None
    sessions = db.scalars(select(TrainingSession).order_by(TrainingSession.created_at.desc())).all()
    for item in sessions:
        if verify_secret(item.join_key_hash, join_key):
            matched_session = item
            break

    if matched_session is None:
        failures = limiter.register_failure(client_id)
        record_audit(db, event_type="session_join_failed", ip_address=ip_address, details={"failures": failures})
        db.commit()
        await asyncio.sleep(settings.failed_login_delay_seconds)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный ключ сессии.")

    if len(matched_session.participants) >= settings.session_max_participants:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Достигнут лимит участников сессии.")

    limiter.register_success(client_id)
    participant = Participant(training_session=matched_session, role="waiting")
    db.add(participant)
    auth_session, raw_token = _create_auth_session(db, training_session=matched_session, participant=participant)
    record_audit(
        db,
        event_type="session_joined",
        session_id=matched_session.id,
        participant_id=participant.id,
        ip_address=ip_address,
        details={"role": "waiting"},
    )
    db.commit()
    db.refresh(matched_session)
    db.refresh(auth_session)
    return matched_session, auth_session, raw_token


def get_session_for_code(db: Session, session_code: str) -> TrainingSession | None:
    stmt = (
        select(TrainingSession)
        .where(TrainingSession.session_code == session_code)
        .options(
            selectinload(TrainingSession.participants).selectinload(Participant.auth_sessions),
            selectinload(TrainingSession.maps),
            selectinload(TrainingSession.scenario_state),
        )
    )
    return db.scalar(stmt)


def get_session_with_related(db: Session, session_code: str) -> TrainingSession | None:
    stmt = (
        select(TrainingSession)
        .where(TrainingSession.session_code == session_code)
        .options(
            selectinload(TrainingSession.participants).selectinload(Participant.auth_sessions),
            selectinload(TrainingSession.maps).selectinload(MapDocument.levels).selectinload(MapLevel.layers),
            selectinload(TrainingSession.scenario_state),
            selectinload(TrainingSession.vehicles),
        )
    )
    return db.scalar(stmt)


def get_session_state_payload(training_session: TrainingSession) -> dict[str, Any]:
    scenario_state = training_session.scenario_state
    participants = sorted(training_session.participants, key=lambda item: item.created_at)
    runtime_maps = group_runtime_maps(training_session)
    return {
        "session_code": training_session.session_code,
        "status": scenario_state.status if scenario_state else "setup",
        "participants": [
            {
                "id": participant.id,
                "role": participant.role,
                "display_name": participant.display_name,
                "created_at": participant.created_at.isoformat(),
            }
            for participant in participants
        ],
        "seat_limit": 4,
        "time_scale": scenario_state.time_scale if scenario_state else 1,
        "time_elapsed_seconds": scenario_state.time_elapsed_seconds if scenario_state else 0,
        "runtime_maps": {
            kind: [{"id": item.id, "title": item.title, "kind": item.kind, "version": item.version} for item in maps]
            for kind, maps in runtime_maps.items()
        },
        "vehicles": [
            {
                "id": vehicle.id,
                "vehicle_type": vehicle.vehicle_type,
                "display_name": vehicle.display_name,
                "status": vehicle.status,
            }
            for vehicle in sorted(training_session.vehicles, key=lambda item: item.created_at)
        ],
    }


def assign_participant_role(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    participant_id: str,
    role: str,
    ip_address: str | None,
) -> Participant:
    require_instructor(auth_session)
    if role not in ASSIGNABLE_ROLES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Недопустимая роль.")
    participant = next((item for item in training_session.participants if item.id == participant_id), None)
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Участник не найден.")
    if role != "waiting":
        existing = next((item for item in training_session.participants if item.role == role and item.id != participant_id), None)
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Роль {role} уже занята.")
    participant.role = role
    for item in participant.auth_sessions:
        item.role = role
    record_audit(
        db,
        event_type="participant_role_updated",
        session_id=training_session.id,
        participant_id=participant.id,
        ip_address=ip_address,
        details={"role": role},
    )
    db.commit()
    db.refresh(participant)
    return participant


def start_training_drill(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    ip_address: str | None,
) -> ScenarioState:
    require_instructor(auth_session)
    scenario_state = training_session.scenario_state
    if scenario_state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Состояние сценария не найдено.")
    if scenario_state.status != "setup":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Сессия уже запущена.")
    roles = {participant.role for participant in training_session.participants}
    if "dispatcher" not in roles or "rtp" not in roles:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Перед запуском нужно назначить диспетчера и РТП.")
    scenario_state.status = "dispatch_call"
    record_audit(
        db,
        event_type="session_started",
        session_id=training_session.id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"status": scenario_state.status},
    )
    db.commit()
    db.refresh(scenario_state)
    return scenario_state


def update_session_time_scale(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    time_scale: int,
    ip_address: str | None,
) -> ScenarioState:
    require_instructor(auth_session)
    if time_scale not in {1, 2, 4}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Недопустимая скорость времени.")
    scenario_state = training_session.scenario_state
    if scenario_state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario state not found.")
    scenario_state.time_scale = time_scale
    record_audit(
        db,
        event_type="time_scale_updated",
        session_id=training_session.id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"time_scale": time_scale},
    )
    db.commit()
    db.refresh(scenario_state)
    return scenario_state


def get_current_auth_session(db: Session, raw_cookie: str | None) -> AuthSession | None:
    return load_auth_session(db, raw_cookie)


def _require_session_roles(auth_session: AuthSession, allowed_roles: set[str]) -> None:
    normalized_role = "instructor" if auth_session.role == "admin" else auth_session.role
    if normalized_role not in allowed_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Для этой роли действие недоступно.")


def _scenario(training_session: TrainingSession) -> ScenarioState:
    if training_session.scenario_state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Состояние сценария не найдено.")
    return training_session.scenario_state


def create_runtime_event(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    event_type: str,
    payload: dict[str, object],
    ip_address: str | None,
) -> dict[str, object]:
    require_instructor(auth_session)
    scenario_state = _scenario(training_session)
    runtime_state = dict(scenario_state.runtime_state_json or {})
    events = list(runtime_state.get("events", []))
    event_payload = {"event_type": event_type, "payload": payload, "created_at": utc_now().isoformat()}
    events.append(event_payload)
    runtime_state["events"] = events
    scenario_state.runtime_state_json = runtime_state
    db.add(
        RuntimeEvent(
            training_session=training_session,
            scenario_state=scenario_state,
            event_type=event_type,
            payload_json=payload,
            created_by_participant_id=auth_session.participant_id,
        )
    )
    record_audit(
        db,
        event_type="runtime_event_created",
        session_id=training_session.id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"event_type": event_type},
    )
    db.commit()
    db.refresh(scenario_state)
    return event_payload


def mark_dispatcher_incident_guess(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    guess_index: int,
    ip_address: str | None,
) -> ScenarioState:
    _require_session_roles(auth_session, {"dispatcher", "instructor"})
    scenario_state = _scenario(training_session)
    scenario_state.dispatcher_guess_index = guess_index
    scenario_state.dispatcher_guess_correct = abs(scenario_state.incident_area_index - guess_index) <= scenario_state.detection_radius_cells
    if scenario_state.status == "dispatch_call":
        scenario_state.status = "enroute"
    record_audit(
        db,
        event_type="dispatcher_guess_marked",
        session_id=training_session.id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"guess_index": guess_index, "correct": scenario_state.dispatcher_guess_correct},
    )
    db.commit()
    db.refresh(scenario_state)
    return scenario_state


def create_dispatch_order(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    vehicle_types: list[str],
    ip_address: str | None,
) -> ScenarioState:
    _require_session_roles(auth_session, {"dispatcher", "instructor"})
    scenario_state = _scenario(training_session)
    runtime_state = dict(scenario_state.runtime_state_json or {})
    orders = list(runtime_state.get("dispatch_orders", []))
    orders.append({"vehicle_types": vehicle_types, "created_at": utc_now().isoformat()})
    runtime_state["dispatch_orders"] = orders
    scenario_state.runtime_state_json = runtime_state
    if scenario_state.status in {"dispatch_call", "setup"}:
        scenario_state.status = "enroute"
    record_audit(
        db,
        event_type="dispatch_order_created",
        session_id=training_session.id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"vehicle_types": vehicle_types},
    )
    db.commit()
    db.refresh(scenario_state)
    return scenario_state


def update_vehicle_route_plan(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    vehicle_id: str,
    route_points: list[dict[str, float]],
    ip_address: str | None,
) -> VehicleInstance:
    _require_session_roles(auth_session, {"rtp", "instructor"})
    vehicle = next((item for item in training_session.vehicles if item.id == vehicle_id), None)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Техника не найдена.")
    vehicle.route_json = {"points": route_points}
    vehicle.status = "moving"
    record_audit(
        db,
        event_type="vehicle_route_updated",
        session_id=training_session.id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"vehicle_id": vehicle_id, "waypoint_count": len(route_points)},
    )
    db.commit()
    db.refresh(vehicle)
    return vehicle


def apply_vehicle_drive_intent(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    vehicle_id: str,
    heading_deg: int,
    speed_mps: float,
    ip_address: str | None,
) -> VehicleInstance:
    _require_session_roles(auth_session, {"rtp", "instructor"})
    vehicle = next((item for item in training_session.vehicles if item.id == vehicle_id), None)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Техника не найдена.")
    vehicle.heading_deg = heading_deg
    vehicle.speed_mps = speed_mps
    vehicle.status = "driving"
    record_audit(
        db,
        event_type="vehicle_drive_intent_applied",
        session_id=training_session.id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"vehicle_id": vehicle_id, "heading_deg": heading_deg, "speed_mps": speed_mps},
    )
    db.commit()
    db.refresh(vehicle)
    return vehicle


def create_hose(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    source_vehicle_id: str,
    polyline_points: list[dict[str, float]],
    ip_address: str | None,
) -> dict[str, object]:
    _require_session_roles(auth_session, {"rtp", "instructor"})
    scenario_state = _scenario(training_session)
    runtime_state = dict(scenario_state.runtime_state_json or {})
    hoses = list(runtime_state.get("hoses", []))
    hose_id = generate_random_token()
    hose_payload = {
        "id": hose_id,
        "source_vehicle_id": source_vehicle_id,
        "polyline_points": polyline_points,
        "flow_state": "dry",
    }
    hoses.append(hose_payload)
    runtime_state["hoses"] = hoses
    scenario_state.runtime_state_json = runtime_state
    record_audit(
        db,
        event_type="hose_created",
        session_id=training_session.id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"hose_id": hose_id, "source_vehicle_id": source_vehicle_id},
    )
    db.commit()
    db.refresh(scenario_state)
    return hose_payload


def update_hose(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    hose_id: str,
    polyline_points: list[dict[str, float]] | None,
    flow_state: str | None,
    ip_address: str | None,
) -> dict[str, object]:
    _require_session_roles(auth_session, {"rtp", "instructor"})
    scenario_state = _scenario(training_session)
    runtime_state = dict(scenario_state.runtime_state_json or {})
    hoses = list(runtime_state.get("hoses", []))
    target = next((item for item in hoses if item.get("id") == hose_id), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Рукав не найден.")
    if polyline_points is not None:
        target["polyline_points"] = polyline_points
    if flow_state is not None:
        target["flow_state"] = flow_state
    runtime_state["hoses"] = hoses
    scenario_state.runtime_state_json = runtime_state
    record_audit(
        db,
        event_type="hose_updated",
        session_id=training_session.id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"hose_id": hose_id},
    )
    db.commit()
    db.refresh(scenario_state)
    return target


def create_nozzle(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    hose_id: str,
    target_x: float,
    target_y: float,
    flow_lps: float,
    ip_address: str | None,
) -> dict[str, object]:
    _require_session_roles(auth_session, {"rtp", "instructor"})
    scenario_state = _scenario(training_session)
    runtime_state = dict(scenario_state.runtime_state_json or {})
    nozzles = list(runtime_state.get("nozzles", []))
    nozzle_id = generate_random_token()
    nozzle_payload = {"id": nozzle_id, "hose_id": hose_id, "target_x": target_x, "target_y": target_y, "flow_lps": flow_lps}
    nozzles.append(nozzle_payload)
    runtime_state["nozzles"] = nozzles
    scenario_state.runtime_state_json = runtime_state
    record_audit(
        db,
        event_type="nozzle_created",
        session_id=training_session.id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"nozzle_id": nozzle_id, "hose_id": hose_id},
    )
    db.commit()
    db.refresh(scenario_state)
    return nozzle_payload


def update_nozzle(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    nozzle_id: str,
    target_x: float | None,
    target_y: float | None,
    flow_lps: float | None,
    ip_address: str | None,
) -> dict[str, object]:
    _require_session_roles(auth_session, {"rtp", "instructor"})
    scenario_state = _scenario(training_session)
    runtime_state = dict(scenario_state.runtime_state_json or {})
    nozzles = list(runtime_state.get("nozzles", []))
    nozzle = next((item for item in nozzles if item.get("id") == nozzle_id), None)
    if nozzle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ствол не найден.")
    if target_x is not None:
        nozzle["target_x"] = target_x
    if target_y is not None:
        nozzle["target_y"] = target_y
    if flow_lps is not None:
        nozzle["flow_lps"] = flow_lps
    runtime_state["nozzles"] = nozzles
    scenario_state.runtime_state_json = runtime_state
    record_audit(
        db,
        event_type="nozzle_updated",
        session_id=training_session.id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"nozzle_id": nozzle_id},
    )
    db.commit()
    db.refresh(scenario_state)
    return nozzle
