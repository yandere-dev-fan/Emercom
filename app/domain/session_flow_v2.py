from __future__ import annotations

from datetime import timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas import CreateSessionRequest
from app.db.models import AuthSession, ChatMessage, MapDocument, MapLevel, Participant, RuntimeEvent, ScenarioState, TrainingSession, VehicleInstance
from app.domain.map_codec import decode_cells
from app.domain.pathfinding import manhattan_distance, weighted_a_star
from app.domain.template_maps_v2 import clone_template_to_session_map, get_template_map
from app.domain.tile_catalog_v3 import area_travel_cost
from app.domain.vehicle_catalog_v2 import VEHICLE_CATALOG
from app.domain.services import _client_id, _create_auth_session, _unique_session_code, load_auth_session, record_audit
from app.security.auth import generate_join_key, generate_random_token, hash_secret, utc_now, verify_secret
from app.security.rate_limit import InMemoryJoinKeyLimiter

ASSIGNABLE_ROLES = {"dispatcher", "rtp", "observer", "waiting"}
CHAT_THREADS = {"instructor_dispatcher", "dispatcher_rtp", "system"}
DEFAULT_VEHICLE_RESERVE = {"FIRE_ENGINE": 4, "LADDER_ENGINE": 2}


def require_instructor(auth_session: AuthSession) -> None:
    if auth_session.role not in {"admin", "instructor"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нужны права создателя сессии.")


def _require_roles(auth_session: AuthSession, allowed: set[str]) -> None:
    role = "instructor" if auth_session.role == "admin" else auth_session.role
    if role not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Действие недоступно для этой роли.")


def _scenario(training_session: TrainingSession) -> ScenarioState:
    if training_session.scenario_state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сценарий не найден.")
    return training_session.scenario_state


def _normalize_dt(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def visible_threads_for_role(role: str) -> set[str]:
    role = "instructor" if role == "admin" else role
    if role in {"instructor", "dispatcher"}:
        return {"instructor_dispatcher", "dispatcher_rtp", "system"}
    if role == "rtp":
        return {"dispatcher_rtp", "system"}
    return {"system"}


def _build_fire_state(object_map: MapDocument, level_code: str, incident_index: int) -> dict[str, object]:
    size = object_map.width * object_map.height
    heat_by_level: dict[str, list[int]] = {}
    state_by_level: dict[str, list[int]] = {}
    smoke_by_level: dict[str, list[int]] = {}
    for level in object_map.levels:
        heat = [0] * size
        state = [0] * size
        smoke = [0] * size
        if level.code == level_code and 0 <= incident_index < size:
            heat[incident_index] = 4
            state[incident_index] = 2
            smoke[incident_index] = 2
        heat_by_level[level.id] = heat
        state_by_level[level.id] = state
        smoke_by_level[level.id] = smoke
    return {"heat_by_level": heat_by_level, "state_by_level": state_by_level, "smoke_by_level": smoke_by_level}


def _materialize_vehicles(db: Session, training_session: TrainingSession, scenario_state: ScenarioState, enabled: list[str]) -> None:
    for vehicle_type in enabled:
        spec = VEHICLE_CATALOG.get(vehicle_type)
        if spec is None:
            continue
        reserve_count = int(DEFAULT_VEHICLE_RESERVE.get(vehicle_type, 1))
        for index in range(1, reserve_count + 1):
            db.add(VehicleInstance(
                training_session=training_session,
                scenario_state=scenario_state,
                vehicle_type=spec["vehicle_type"],
                display_name=f"{spec['display_name']} #{index}",
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
                route_json={"path": [], "cursor": 0, "movement_budget": 0.0, "target_index": None},
            ))


def create_training_session_from_templates(db: Session, *, payload: CreateSessionRequest, settings, ip_address: str | None) -> tuple[TrainingSession, AuthSession, str, str, ScenarioState]:
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
    object_runtime_map = clone_template_to_session_map(db, template_map=object_template, training_session=training_session, parent_map_id=area_runtime_map.id)
    db.flush()
    scenario_state = ScenarioState(
        training_session=training_session,
        status="setup",
        area_map_id=area_runtime_map.id,
        object_map_id=object_runtime_map.id,
        time_elapsed_minutes=0,
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
        runtime_state_json={"vehicle_targets": {}, "arrival_state": {"last_result": None}, "fire_state": _build_fire_state(object_runtime_map, payload.incident_object_level_code, payload.incident_object_index), "hose_state": {"hoses": [], "nozzles": []}, "chat_state_meta": {"last_message_at": None}, "active_object_vehicle_id": None, "events": []},
    )
    db.add(scenario_state)
    db.flush()
    _materialize_vehicles(db, training_session, scenario_state, payload.enabled_vehicle_types)
    db.add(ChatMessage(training_session=training_session, participant=None, thread_key="system", body="Сессия создана. Назначьте роли и запустите тренировку."))
    auth_session, raw_token = _create_auth_session(db, training_session=training_session, participant=participant)
    record_audit(db, event_type="session_created", session_id=training_session.id, participant_id=participant.id, ip_address=ip_address, details={"session_code": training_session.session_code})
    db.commit()
    db.refresh(training_session)
    db.refresh(auth_session)
    db.refresh(scenario_state)
    return training_session, auth_session, raw_token, join_key, scenario_state


async def join_training_session(db: Session, *, join_key: str, ip_address: str | None, limiter: InMemoryJoinKeyLimiter, settings) -> tuple[TrainingSession, AuthSession, str]:
    client_id = _client_id(ip_address)
    if limiter.is_blocked(client_id):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Слишком много неудачных попыток.")
    matched_session: TrainingSession | None = None
    sessions = db.scalars(select(TrainingSession).order_by(TrainingSession.created_at.desc())).all()
    for item in sessions:
        if verify_secret(item.join_key_hash, join_key):
            matched_session = item
            break
    if matched_session is None:
        limiter.register_failure(client_id)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный ключ сессии.")
    if len(matched_session.participants) >= settings.session_max_participants:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Достигнут лимит участников.")
    limiter.register_success(client_id)
    participant = Participant(training_session=matched_session, role="waiting")
    db.add(participant)
    db.flush()
    auth_session, raw_token = _create_auth_session(db, training_session=matched_session, participant=participant)
    db.add(ChatMessage(training_session=matched_session, participant=None, thread_key="system", body=f"Новый участник: {participant.id[:8]}"))
    db.commit()
    db.refresh(matched_session)
    db.refresh(auth_session)
    return matched_session, auth_session, raw_token


def get_session_for_code(db: Session, session_code: str) -> TrainingSession | None:
    return db.scalar(select(TrainingSession).where(TrainingSession.session_code == session_code))


def get_session_with_related(db: Session, session_code: str) -> TrainingSession | None:
    stmt = (
        select(TrainingSession)
        .where(TrainingSession.session_code == session_code)
        .options(
            selectinload(TrainingSession.participants).selectinload(Participant.auth_sessions),
            selectinload(TrainingSession.maps).selectinload(MapDocument.levels).selectinload(MapLevel.layers),
            selectinload(TrainingSession.scenario_state),
            selectinload(TrainingSession.vehicles),
            selectinload(TrainingSession.chat_messages),
        )
    )
    return db.scalar(stmt)


def get_current_auth_session(db: Session, raw_cookie: str | None) -> AuthSession | None:
    return load_auth_session(db, raw_cookie)


def get_session_state_payload(training_session: TrainingSession, *, viewer_role: str | None = None) -> dict[str, Any]:
    scenario_state = training_session.scenario_state
    role = viewer_role or "observer"
    runtime_maps = {"area": [], "object": []}
    for item in sorted([m for m in training_session.maps if m.scope == "session"], key=lambda m: m.created_at):
        if role == "dispatcher" and item.kind == "object":
            continue
        if role in {"rtp", "observer"} and item.kind == "object" and not (scenario_state and scenario_state.incident_revealed):
            continue
        runtime_maps[item.kind].append({"id": item.id, "title": item.title, "kind": item.kind, "version": item.version})
    return {
        "session_code": training_session.session_code,
        "status": scenario_state.status if scenario_state else "setup",
        "participants": [{"id": p.id, "role": p.role, "display_name": p.display_name, "created_at": _normalize_dt(p.created_at).isoformat()} for p in sorted(training_session.participants, key=lambda p: p.created_at)],
        "seat_limit": 4,
        "time_elapsed_minutes": scenario_state.time_elapsed_minutes if scenario_state else 0,
        "runtime_maps": runtime_maps,
        "vehicles": [{"id": v.id, "vehicle_type": v.vehicle_type, "display_name": v.display_name, "status": v.status, "current_map_id": v.current_map_id} for v in sorted(training_session.vehicles, key=lambda v: v.created_at)],
        "chat": {"threads": sorted(visible_threads_for_role(role))},
    }


def assign_participant_role(db: Session, *, training_session: TrainingSession, auth_session: AuthSession, participant_id: str, role: str, ip_address: str | None) -> Participant:
    require_instructor(auth_session)
    if role not in ASSIGNABLE_ROLES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Недопустимая роль.")
    participant = next((item for item in training_session.participants if item.id == participant_id), None)
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Участник не найден.")
    if role != "waiting":
        existing = next((item for item in training_session.participants if item.role == role and item.id != participant_id), None)
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Роль {role} уже занята.")
    participant.role = role
    for item in participant.auth_sessions:
        item.role = role
    db.commit()
    db.refresh(participant)
    return participant


def start_training_drill(db: Session, *, training_session: TrainingSession, auth_session: AuthSession, ip_address: str | None) -> ScenarioState:
    require_instructor(auth_session)
    scenario_state = _scenario(training_session)
    if scenario_state.status != "setup":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Сессия уже запущена.")
    roles = {participant.role for participant in training_session.participants}
    if "dispatcher" not in roles or "rtp" not in roles:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Нужно назначить диспетчера и РТП.")
    scenario_state.status = "dispatch_call"
    scenario_state.time_elapsed_minutes = 0
    db.add(ChatMessage(training_session=training_session, participant=None, thread_key="system", body="Тренировка запущена."))
    db.commit()
    db.refresh(scenario_state)
    return scenario_state


def mark_dispatcher_incident_guess(db: Session, *, training_session: TrainingSession, auth_session: AuthSession, guess_index: int, ip_address: str | None) -> ScenarioState:
    _require_roles(auth_session, {"dispatcher", "instructor"})
    scenario_state = _scenario(training_session)
    area_map = next((item for item in training_session.maps if item.id == scenario_state.area_map_id), None)
    if area_map is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карта местности не найдена.")
    scenario_state.dispatcher_guess_index = guess_index
    scenario_state.dispatcher_guess_correct = manhattan_distance(scenario_state.incident_area_index, guess_index, area_map.width) <= scenario_state.detection_radius_cells
    if scenario_state.status == "setup":
        scenario_state.status = "dispatch_call"
    db.commit()
    db.refresh(scenario_state)
    return scenario_state


def _best_path(training_session: TrainingSession, target_index: int) -> list[int]:
    area_map = next((item for item in training_session.maps if item.id == _scenario(training_session).area_map_id), None)
    if area_map is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карта местности не найдена.")
    level = next((item for item in area_map.levels if item.code == "AREA_MAIN"), area_map.levels[0])
    decoded = {layer.layer_key: decode_cells(layer.cells_blob, area_map.width * area_map.height) for layer in level.layers}
    ground = decoded.get("ground", [0] * (area_map.width * area_map.height))
    objects = decoded.get("objects", [0] * (area_map.width * area_map.height))
    buildings = decoded.get("buildings", [0] * (area_map.width * area_map.height))
    costs = [area_travel_cost(ground[i], objects[i], buildings[i]) for i in range(area_map.width * area_map.height)]
    spawn_points = [i for i, code in enumerate(objects) if code == 7] or [0]
    best_path: list[int] = []
    best_cost = 10**9
    for start in spawn_points:
        path = weighted_a_star(width=area_map.width, height=area_map.height, start_index=start, target_index=target_index, cell_costs=costs)
        if not path:
            continue
        path_cost = sum(costs[index] or 0 for index in path[1:])
        if path_cost < best_cost:
            best_cost = path_cost
            best_path = path
    if not best_path:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Маршрут до метки не найден.")
    return best_path


def create_dispatch_order(db: Session, *, training_session: TrainingSession, auth_session: AuthSession, counts: dict[str, int], ip_address: str | None) -> dict[str, Any]:
    _require_roles(auth_session, {"dispatcher", "instructor"})
    scenario_state = _scenario(training_session)
    if scenario_state.dispatcher_guess_index is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Сначала поставьте метку.")
    path = _best_path(training_session, scenario_state.dispatcher_guess_index)
    selected: list[VehicleInstance] = []
    required = {key: value for key, value in counts.items() if value > 0}
    if not required:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Нужно выбрать хотя бы одну машину.")
    available_counts: dict[str, int] = {}
    for vehicle in training_session.vehicles:
        if vehicle.status == "staged":
            available_counts[vehicle.vehicle_type] = available_counts.get(vehicle.vehicle_type, 0) + 1
    shortages = {vehicle_type: needed for vehicle_type, needed in required.items() if available_counts.get(vehicle_type, 0) < needed}
    if shortages:
        parts = [f"{vehicle_type}: {needed}" for vehicle_type, needed in shortages.items()]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Недостаточно свободной техники ({', '.join(parts)}).",
        )
    for vehicle in training_session.vehicles:
        needed = required.get(vehicle.vehicle_type, 0)
        if vehicle.status != "staged" or needed <= 0:
            continue
        required[vehicle.vehicle_type] = needed - 1
        vehicle.status = "enroute"
        vehicle.position_x = float(path[0] % next(m for m in training_session.maps if m.id == scenario_state.area_map_id).width)
        vehicle.position_y = float(path[0] // next(m for m in training_session.maps if m.id == scenario_state.area_map_id).width)
        vehicle.route_json = {"path": path, "cursor": 0, "movement_budget": 0.0, "target_index": scenario_state.dispatcher_guess_index}
        selected.append(vehicle)
    if not selected:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Нет свободной техники под этот запрос.")
    scenario_state.status = "enroute"
    db.add(ChatMessage(training_session=training_session, participant=None, thread_key="system", body=f"Отправлено техники: {len(selected)}"))
    db.commit()
    return {"status": scenario_state.status, "vehicle_ids": [vehicle.id for vehicle in selected]}


def create_runtime_event(db: Session, *, training_session: TrainingSession, auth_session: AuthSession, event_type: str, payload: dict[str, object], ip_address: str | None) -> dict[str, object]:
    require_instructor(auth_session)
    scenario_state = _scenario(training_session)
    runtime_state = dict(scenario_state.runtime_state_json or {})
    events = list(runtime_state.get("events", []))
    event_payload = {"event_type": event_type, "payload": payload, "created_at": utc_now().isoformat()}
    events.append(event_payload)
    runtime_state["events"] = events
    scenario_state.runtime_state_json = runtime_state
    db.add(RuntimeEvent(training_session=training_session, scenario_state=scenario_state, event_type=event_type, payload_json=payload, created_by_participant_id=auth_session.participant_id))
    db.add(ChatMessage(training_session=training_session, participant=None, thread_key="system", body=f"Событие: {event_type}"))
    db.commit()
    return event_payload


def update_vehicle_object_route(db: Session, *, training_session: TrainingSession, auth_session: AuthSession, vehicle_id: str, route_points: list[dict[str, float]], ip_address: str | None) -> VehicleInstance:
    _require_roles(auth_session, {"rtp", "instructor"})
    scenario_state = _scenario(training_session)
    if not scenario_state.incident_revealed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Фаза объекта ещё не открыта.")
    vehicle = next((item for item in training_session.vehicles if item.id == vehicle_id), None)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Техника не найдена.")
    vehicle.route_json = {**(vehicle.route_json or {}), "object_points": route_points}
    vehicle.status = "object_control"
    db.commit()
    db.refresh(vehicle)
    return vehicle


def apply_vehicle_object_drive(db: Session, *, training_session: TrainingSession, auth_session: AuthSession, vehicle_id: str, heading_deg: int, speed_mps: float, ip_address: str | None) -> VehicleInstance:
    _require_roles(auth_session, {"rtp", "instructor"})
    scenario_state = _scenario(training_session)
    if not scenario_state.incident_revealed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Фаза объекта ещё не открыта.")
    vehicle = next((item for item in training_session.vehicles if item.id == vehicle_id), None)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Техника не найдена.")
    vehicle.heading_deg = heading_deg
    vehicle.speed_mps = speed_mps
    vehicle.status = "object_control"
    db.commit()
    db.refresh(vehicle)
    return vehicle


def create_hose(db: Session, *, training_session: TrainingSession, auth_session: AuthSession, source_vehicle_id: str, polyline_points: list[dict[str, float]], ip_address: str | None) -> dict[str, object]:
    _require_roles(auth_session, {"rtp", "instructor"})
    scenario_state = _scenario(training_session)
    if not scenario_state.incident_revealed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Фаза объекта ещё не открыта.")
    runtime_state = dict(scenario_state.runtime_state_json or {})
    hose_state = dict(runtime_state.get("hose_state", {}))
    hoses = list(hose_state.get("hoses", []))
    hose = {"id": generate_random_token(), "source_vehicle_id": source_vehicle_id, "polyline_points": polyline_points, "flow_state": "dry"}
    hoses.append(hose)
    hose_state["hoses"] = hoses
    runtime_state["hose_state"] = hose_state
    scenario_state.runtime_state_json = runtime_state
    db.commit()
    return hose


def update_hose(db: Session, *, training_session: TrainingSession, auth_session: AuthSession, hose_id: str, polyline_points: list[dict[str, float]] | None, flow_state: str | None, ip_address: str | None) -> dict[str, object]:
    _require_roles(auth_session, {"rtp", "instructor"})
    scenario_state = _scenario(training_session)
    hose_state = dict(dict(scenario_state.runtime_state_json or {}).get("hose_state", {}))
    hoses = list(hose_state.get("hoses", []))
    hose = next((item for item in hoses if item.get("id") == hose_id), None)
    if hose is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Рукав не найден.")
    if polyline_points is not None:
        hose["polyline_points"] = polyline_points
    if flow_state is not None:
        hose["flow_state"] = flow_state
    hose_state["hoses"] = hoses
    runtime_state = dict(scenario_state.runtime_state_json or {})
    runtime_state["hose_state"] = hose_state
    scenario_state.runtime_state_json = runtime_state
    db.commit()
    return hose


def create_nozzle(db: Session, *, training_session: TrainingSession, auth_session: AuthSession, hose_id: str, target_x: float, target_y: float, flow_lps: float, ip_address: str | None) -> dict[str, object]:
    _require_roles(auth_session, {"rtp", "instructor"})
    scenario_state = _scenario(training_session)
    runtime_state = dict(scenario_state.runtime_state_json or {})
    hose_state = dict(runtime_state.get("hose_state", {}))
    nozzles = list(hose_state.get("nozzles", []))
    nozzle = {"id": generate_random_token(), "hose_id": hose_id, "target_x": target_x, "target_y": target_y, "flow_lps": flow_lps}
    nozzles.append(nozzle)
    hose_state["nozzles"] = nozzles
    runtime_state["hose_state"] = hose_state
    scenario_state.runtime_state_json = runtime_state
    db.commit()
    return nozzle


def update_nozzle(db: Session, *, training_session: TrainingSession, auth_session: AuthSession, nozzle_id: str, target_x: float | None, target_y: float | None, flow_lps: float | None, ip_address: str | None) -> dict[str, object]:
    _require_roles(auth_session, {"rtp", "instructor"})
    scenario_state = _scenario(training_session)
    runtime_state = dict(scenario_state.runtime_state_json or {})
    hose_state = dict(runtime_state.get("hose_state", {}))
    nozzles = list(hose_state.get("nozzles", []))
    nozzle = next((item for item in nozzles if item.get("id") == nozzle_id), None)
    if nozzle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ствол не найден.")
    if target_x is not None:
        nozzle["target_x"] = target_x
    if target_y is not None:
        nozzle["target_y"] = target_y
    if flow_lps is not None:
        nozzle["flow_lps"] = flow_lps
    hose_state["nozzles"] = nozzles
    runtime_state["hose_state"] = hose_state
    scenario_state.runtime_state_json = runtime_state
    db.commit()
    return nozzle


def list_chat_messages(db: Session, *, training_session: TrainingSession, auth_session: AuthSession, thread_key: str) -> list[dict[str, object]]:
    if thread_key not in CHAT_THREADS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Неизвестный поток.")
    if thread_key not in visible_threads_for_role(auth_session.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Поток недоступен.")
    messages = [item for item in sorted(training_session.chat_messages, key=lambda msg: msg.created_at) if item.thread_key == thread_key]
    return [{"id": msg.id, "thread_key": msg.thread_key, "body": msg.body, "participant_id": msg.participant_id, "created_at": _normalize_dt(msg.created_at).isoformat()} for msg in messages]


def create_chat_message(db: Session, *, training_session: TrainingSession, auth_session: AuthSession, thread_key: str, body: str, ip_address: str | None) -> ChatMessage:
    if thread_key not in CHAT_THREADS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Неизвестный поток.")
    if thread_key not in visible_threads_for_role(auth_session.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Поток недоступен.")
    cleaned_body = body.strip()
    if not cleaned_body:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Пустое сообщение не отправляется.")
    message = ChatMessage(training_session=training_session, participant_id=auth_session.participant_id, thread_key=thread_key, body=cleaned_body)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message
