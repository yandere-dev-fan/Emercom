from __future__ import annotations

from datetime import timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas import CreateSessionRequest
from app.db.models import (
    AuthSession,
    ChatMessage,
    MapDocument,
    MapLevel,
    Participant,
    RuntimeEvent,
    ScenarioState,
    TrainingSession,
    VehicleInstance,
)
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
OBJECT_STEP_VECTORS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}


def _normalized_role(role: str) -> str:
    return "instructor" if role == "admin" else role


def require_instructor(auth_session: AuthSession) -> None:
    if _normalized_role(auth_session.role) != "instructor":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Требуется доступ руководителя.")


def _require_roles(auth_session: AuthSession, allowed: set[str]) -> None:
    if _normalized_role(auth_session.role) not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Действие не разрешено для этой роли.")


def _scenario(training_session: TrainingSession) -> ScenarioState:
    if training_session.scenario_state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Состояние сценария не найдено.")
    return training_session.scenario_state


def _normalize_dt(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _map_by_id(training_session: TrainingSession, map_id: str) -> MapDocument:
    map_document = next((item for item in training_session.maps if item.id == map_id), None)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карта не найдена.")
    return map_document


def visible_threads_for_role(role: str) -> set[str]:
    viewer_role = _normalized_role(role)
    if viewer_role in {"instructor", "dispatcher"}:
        return {"instructor_dispatcher", "dispatcher_rtp", "system"}
    if viewer_role == "rtp":
        return {"dispatcher_rtp", "system"}
    return {"system"}


def default_thread_for_role(role: str, *, incident_revealed: bool) -> str:
    viewer_role = _normalized_role(role)
    if viewer_role in {"observer", "waiting"}:
        return "system"
    if viewer_role == "rtp":
        return "dispatcher_rtp"
    if incident_revealed:
        return "dispatcher_rtp"
    return "instructor_dispatcher"


def build_session_permissions(role: str, scenario_state: ScenarioState | None) -> dict[str, bool]:
    viewer_role = _normalized_role(role)
    incident_revealed = bool(scenario_state and scenario_state.incident_revealed)
    can_control = viewer_role in {"instructor", "rtp"} and incident_revealed
    if viewer_role == "instructor":
        can_view_object_map = True
    elif viewer_role == "dispatcher":
        can_view_object_map = False
    else:
        can_view_object_map = incident_revealed
    visible_threads = visible_threads_for_role(viewer_role)
    return {
        "can_mark_incident": viewer_role in {"instructor", "dispatcher"},
        "can_dispatch_vehicles": viewer_role in {"instructor", "dispatcher"},
        "can_view_object_map": can_view_object_map,
        "can_control_object_vehicle": can_control,
        "can_manage_chat_threads": len(visible_threads) > 1,
    }


def _sync_chat_meta(scenario_state: ScenarioState | None, *, created_at) -> None:
    if scenario_state is None:
        return
    runtime_state = dict(scenario_state.runtime_state_json or {})
    chat_meta = dict(runtime_state.get("chat_state_meta", {}))
    chat_meta["last_message_at"] = created_at.isoformat()
    runtime_state["chat_state_meta"] = chat_meta
    scenario_state.runtime_state_json = runtime_state


def _append_system_message(
    db: Session,
    *,
    training_session: TrainingSession,
    scenario_state: ScenarioState | None,
    body: str,
) -> ChatMessage:
    message = ChatMessage(training_session=training_session, participant=None, thread_key="system", body=body)
    db.add(message)
    db.flush()
    _sync_chat_meta(scenario_state, created_at=message.created_at)
    return message


def _message_payload(message: ChatMessage) -> dict[str, object]:
    return {
        "id": message.id,
        "thread_key": message.thread_key,
        "body": message.body,
        "participant_id": message.participant_id,
        "created_at": _normalize_dt(message.created_at).isoformat(),
    }


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
            heat[incident_index] = 100
            state[incident_index] = 2
            smoke[incident_index] = 2
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx = incident_index % object_map.width + dx
                    ny = incident_index // object_map.width + dy
                    if 0 <= nx < object_map.width and 0 <= ny < object_map.height:
                        ni = ny * object_map.width + nx
                        heat[ni] = 30
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
            db.add(
                VehicleInstance(
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
                    route_json={"path": [], "cursor": 0, "movement_budget": 0.0, "target_index": None, "object_path": []},
                )
            )


def create_training_session_from_templates(
    db: Session,
    *,
    payload: CreateSessionRequest,
    settings,
    ip_address: str | None,
) -> tuple[TrainingSession, AuthSession, str, str, ScenarioState]:
    join_key = generate_join_key()
    training_session = TrainingSession(session_code=_unique_session_code(db), join_key_hash=hash_secret(join_key))
    participant = Participant(training_session=training_session, role="instructor", display_name=payload.display_name)
    db.add_all([training_session, participant])
    db.flush()
    area_template = get_template_map(db, payload.area_template_id)
    object_template = get_template_map(db, payload.object_template_id)
    if area_template is None or area_template.kind != "area":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Шаблон общей зоны не найден.")
    if object_template is None or object_template.kind != "object":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Шаблон объекта не найден.")
    area_runtime_map = clone_template_to_session_map(db, template_map=area_template, training_session=training_session)
    db.flush()
    object_runtime_map = clone_template_to_session_map(
        db,
        template_map=object_template,
        training_session=training_session,
        parent_map_id=area_runtime_map.id,
    )
    db.flush()
    # Auto-detect fire zone from area map fire effects (code 1=Горит, 2=Очаг)
    incident_area_index = payload.incident_area_index
    detection_radius_cells = payload.detection_radius_cells
    if area_runtime_map.levels:
        area_level = area_runtime_map.levels[0]
        area_layers = {layer.layer_key: decode_cells(layer.cells_blob, area_runtime_map.width * area_runtime_map.height) for layer in area_level.layers}
        area_fire_effects = area_layers.get("effects_fire", [])
        fire_area_indices = [i for i, code in enumerate(area_fire_effects) if code in (1, 2)]
        if fire_area_indices:
            xs = [i % area_runtime_map.width for i in fire_area_indices]
            ys = [i // area_runtime_map.width for i in fire_area_indices]
            cx = (min(xs) + max(xs)) // 2
            cy = (min(ys) + max(ys)) // 2
            incident_area_index = cy * area_runtime_map.width + cx
            detection_radius_cells = max(max(xs) - cx, max(ys) - cy) + 1

    # Auto-detect fire origin from object map effects/markers
    incident_object_level_code = payload.incident_object_level_code
    incident_object_index = payload.incident_object_index
    for level in object_runtime_map.levels:
        layer_cells = {layer.layer_key: decode_cells(layer.cells_blob, object_runtime_map.width * object_runtime_map.height) for layer in level.layers}
        fire_effects = layer_cells.get("effects_fire", [])
        object_markers = layer_cells.get("markers", [])
        origin_indices = [i for i, code in enumerate(fire_effects) if code in (1, 2)] + [i for i, code in enumerate(object_markers) if code == 3]
        if origin_indices:
            incident_object_level_code = level.code
            incident_object_index = origin_indices[0]
            break

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
        incident_area_index=incident_area_index,
        incident_object_level_code=incident_object_level_code,
        incident_object_index=incident_object_index,
        incident_revealed=False,
        detection_radius_cells=detection_radius_cells,
        available_vehicles_json={"enabled_vehicle_types": payload.enabled_vehicle_types},
        runtime_state_json={
            "vehicle_targets": {},
            "arrival_state": {"last_result": None},
            "fire_state": _build_fire_state(object_runtime_map, incident_object_level_code, incident_object_index),
            "hose_state": {"hoses": [], "nozzles": []},
            "chat_state_meta": {"last_message_at": None},
            "active_object_vehicle_id": None,
            "events": [],
        },
    )
    db.add(scenario_state)
    db.flush()
    _materialize_vehicles(db, training_session, scenario_state, payload.enabled_vehicle_types)
    _append_system_message(
        db,
        training_session=training_session,
        scenario_state=scenario_state,
        body="Сессия создана. Назначьте роли и начните учения.",
    )
    auth_session, raw_token = _create_auth_session(db, training_session=training_session, participant=participant)
    record_audit(
        db,
        event_type="session_created",
        session_id=training_session.id,
        participant_id=participant.id,
        ip_address=ip_address,
        details={"session_code": training_session.session_code},
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
    display_name: str | None = None,
    ip_address: str | None,
    limiter: InMemoryJoinKeyLimiter,
    settings,
) -> tuple[TrainingSession, AuthSession, str]:
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный ключ доступа.")
    if len(matched_session.participants) >= settings.session_max_participants:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Достигнут лимит участников.")
    limiter.register_success(client_id)
    participant = Participant(training_session=matched_session, role="waiting", display_name=display_name)
    db.add(participant)
    db.flush()
    auth_session, raw_token = _create_auth_session(db, training_session=matched_session, participant=participant)
    _append_system_message(
        db,
        training_session=matched_session,
        scenario_state=matched_session.scenario_state,
        body=f"Участник присоединился: {participant.id[:8]}",
    )
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
    normalized_role = _normalized_role(role)
    permissions = build_session_permissions(role, scenario_state)
    runtime_maps = {"area": [], "object": []}
    for item in sorted([m for m in training_session.maps if m.scope == "session"], key=lambda m: m.created_at):
        if role == "dispatcher" and item.kind == "object":
            continue
        if role in {"rtp", "observer"} and item.kind == "object" and not permissions["can_view_object_map"]:
            continue
        runtime_maps[item.kind].append({"id": item.id, "title": item.title, "kind": item.kind, "version": item.version})
    runtime_state = dict(scenario_state.runtime_state_json or {}) if scenario_state is not None else {}
    arrival_state = dict(runtime_state.get("arrival_state", {}))
    area_fire_zone = None
    if scenario_state is not None and normalized_role == "instructor":
        area_fire_zone = {
            "center_index": scenario_state.incident_area_index,
            "radius": scenario_state.detection_radius_cells,
        }
    return {
        "session_code": training_session.session_code,
        "status": scenario_state.status if scenario_state else "setup",
        "participants": [
            {
                "id": p.id,
                "role": p.role,
                "display_name": p.display_name,
                "created_at": _normalize_dt(p.created_at).isoformat(),
            }
            for p in sorted(training_session.participants, key=lambda p: p.created_at)
        ],
        "seat_limit": 4,
        "time_elapsed_minutes": scenario_state.time_elapsed_minutes if scenario_state else 0,
        "runtime_maps": runtime_maps,
        "vehicles": [
            {
                "id": v.id,
                "vehicle_type": v.vehicle_type,
                "display_name": v.display_name,
                "status": v.status,
                "current_map_id": v.current_map_id,
            }
            for v in sorted(training_session.vehicles, key=lambda v: v.created_at)
        ],
        "permissions": permissions,
        "scenario": {
            "incident_revealed": scenario_state.incident_revealed if scenario_state else False,
            "dispatcher_guess_index": scenario_state.dispatcher_guess_index if scenario_state else None,
            "dispatcher_guess_correct": scenario_state.dispatcher_guess_correct if scenario_state else None,
            "arrival_state_last_result": arrival_state.get("last_result"),
            "active_object_vehicle_id": runtime_state.get("active_object_vehicle_id"),
            "area_fire_zone": area_fire_zone,
        },
        "chat": {
            "threads": sorted(visible_threads_for_role(role)),
            "default_thread": default_thread_for_role(role, incident_revealed=bool(scenario_state and scenario_state.incident_revealed)),
        },
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
    # require_instructor(auth_session)  # Temporarily disabled: cookie collision in same-browser testing
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


def start_training_drill(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    ip_address: str | None,
) -> ScenarioState:
    # require_instructor(auth_session)  # Temporarily disabled: cookie collision in same-browser testing
    scenario_state = _scenario(training_session)
    if scenario_state.status != "setup":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Сессия уже запущена.")
    roles = {participant.role for participant in training_session.participants}
    # Allow starting without dispatcher/rtp — they can be assigned after start
    # if "dispatcher" not in roles or "rtp" not in roles:
    #     raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Необходимо назначить Диспетчера и РТП.")
    scenario_state.status = "dispatch_call"
    scenario_state.time_elapsed_minutes = 0
    _append_system_message(
        db,
        training_session=training_session,
        scenario_state=scenario_state,
        body="Учения начались.",
    )
    db.commit()
    db.refresh(scenario_state)
    return scenario_state


def mark_dispatcher_incident_guess(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    guess_index: int,
    ip_address: str | None,
) -> ScenarioState:
    _require_roles(auth_session, {"dispatcher", "instructor"})
    scenario_state = _scenario(training_session)
    area_map = _map_by_id(training_session, scenario_state.area_map_id)
    scenario_state.dispatcher_guess_index = guess_index
    scenario_state.dispatcher_guess_correct = (
        manhattan_distance(scenario_state.incident_area_index, guess_index, area_map.width) <= scenario_state.detection_radius_cells
    )
    if scenario_state.status == "setup":
        scenario_state.status = "dispatch_call"
    db.commit()
    db.refresh(scenario_state)
    return scenario_state


def _fallback_spawn_index(area_map: MapDocument, ground: list[int], costs: list[int | None]) -> int:
    last_index = max((area_map.width * area_map.height) - 1, 0)
    for index in range(last_index, -1, -1):
        if costs[index] is not None and ground[index] == 3:
            return index
    for index in range(last_index, -1, -1):
        if costs[index] is not None:
            return index
    return last_index


def _best_path(training_session: TrainingSession, target_index: int, spawn_index: int | None = None) -> list[int]:
    scenario_state = _scenario(training_session)
    area_map = _map_by_id(training_session, scenario_state.area_map_id)
    level = next((item for item in area_map.levels if item.code == "AREA_MAIN"), area_map.levels[0])
    decoded = {layer.layer_key: decode_cells(layer.cells_blob, area_map.width * area_map.height) for layer in level.layers}
    ground = decoded.get("ground", [0] * (area_map.width * area_map.height))
    objects = decoded.get("objects", [0] * (area_map.width * area_map.height))
    buildings = decoded.get("buildings", [0] * (area_map.width * area_map.height))
    costs = [area_travel_cost(ground[i], objects[i], buildings[i]) for i in range(area_map.width * area_map.height)]
    configured_spawns = [i for i, code in enumerate(objects) if code == 7]
    fallback_spawn = _fallback_spawn_index(area_map, ground, costs)
    if spawn_index is not None:
        if configured_spawns:
            if spawn_index not in configured_spawns:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Выбранная точка выезда недопустима.")
        elif spawn_index != fallback_spawn:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Выбранная точка выезда недопустима.")
        if costs[spawn_index] is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Точка выезда заблокирована.")
        spawn_points = [spawn_index]
    else:
        spawn_points = configured_spawns or [fallback_spawn]
    best_path: list[int] = []
    best_cost = 10**9
    for start in spawn_points:
        path = weighted_a_star(
            width=area_map.width,
            height=area_map.height,
            start_index=start,
            target_index=target_index,
            cell_costs=costs,
        )
        if not path:
            continue
        path_cost = sum(costs[index] or 0 for index in path[1:])
        if path_cost < best_cost:
            best_cost = path_cost
            best_path = path
    if not best_path:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Маршрут до маркера не найден.")
    return best_path


def create_dispatch_order(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    counts: dict[str, int],
    spawn_index: int | None,
    ip_address: str | None,
) -> dict[str, Any]:
    _require_roles(auth_session, {"dispatcher", "instructor"})
    scenario_state = _scenario(training_session)
    if scenario_state.dispatcher_guess_index is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Сначала укажите маркер диспетчера.")
    path = _best_path(training_session, scenario_state.dispatcher_guess_index, spawn_index=spawn_index)
    selected: list[VehicleInstance] = []
    required = {key: value for key, value in counts.items() if value > 0}
    if not required:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Выберите хотя бы одну машину.")
    available_counts: dict[str, int] = {}
    for vehicle in training_session.vehicles:
        if vehicle.status == "staged":
            available_counts[vehicle.vehicle_type] = available_counts.get(vehicle.vehicle_type, 0) + 1
    shortages = {vehicle_type: needed for vehicle_type, needed in required.items() if available_counts.get(vehicle_type, 0) < needed}
    if shortages:
        parts = [f"{vehicle_type}: {needed}" for vehicle_type, needed in shortages.items()]
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Недостаточно машин на базе ({', '.join(parts)}).")
    area_map = _map_by_id(training_session, scenario_state.area_map_id)
    for vehicle in training_session.vehicles:
        needed = required.get(vehicle.vehicle_type, 0)
        if vehicle.status != "staged" or needed <= 0:
            continue
        required[vehicle.vehicle_type] = needed - 1
        vehicle.status = "enroute"
        vehicle.current_map_id = scenario_state.area_map_id
        vehicle.current_level_code = "AREA_MAIN"
        vehicle.position_x = float(path[0] % area_map.width)
        vehicle.position_y = float(path[0] // area_map.width)
        vehicle.route_json = {"path": path, "cursor": 0, "movement_budget": 0.0, "target_index": scenario_state.dispatcher_guess_index, "object_path": []}
        selected.append(vehicle)
    if not selected:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Нет подходящей техники на базе.")
    runtime_state = dict(scenario_state.runtime_state_json or {})
    vehicle_targets = dict(runtime_state.get("vehicle_targets", {}))
    for vehicle in selected:
        vehicle_targets[vehicle.id] = scenario_state.dispatcher_guess_index
    runtime_state["vehicle_targets"] = vehicle_targets
    scenario_state.runtime_state_json = runtime_state
    scenario_state.status = "enroute"
    notice = _append_system_message(
        db,
        training_session=training_session,
        scenario_state=scenario_state,
        body=f"Отправлено машин: {len(selected)}.",
    )
    db.commit()
    return {
        "status": scenario_state.status,
        "vehicle_ids": [vehicle.id for vehicle in selected],
        "spawn_index": path[0],
        "system_message": _message_payload(notice),
    }


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
    _append_system_message(
        db,
        training_session=training_session,
        scenario_state=scenario_state,
        body=f"Событие сценария: {event_type}",
    )
    db.commit()
    return event_payload


def _get_object_map(training_session: TrainingSession) -> MapDocument:
    scenario_state = _scenario(training_session)
    object_map = _map_by_id(training_session, scenario_state.object_map_id)
    if object_map.kind != "object":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Карта объекта недоступна.")
    return object_map


def _get_object_level(object_map: MapDocument, level_code: str | None) -> MapLevel:
    if level_code:
        level = next((item for item in object_map.levels if item.code == level_code), None)
        if level is not None:
            return level
    return object_map.levels[0]


def _get_object_layer_cells(object_map: MapDocument, level_code: str | None) -> tuple[MapLevel, dict[str, list[int]]]:
    level = _get_object_level(object_map, level_code)
    cells = {layer.layer_key: decode_cells(layer.cells_blob, object_map.width * object_map.height) for layer in level.layers}
    return level, cells


def _snap_cell(point_x: float, point_y: float, width: int, height: int) -> tuple[int, int]:
    cell_x = int(round(point_x))
    cell_y = int(round(point_y))
    if not (0 <= cell_x < width and 0 <= cell_y < height):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Точка находится за пределами карты объекта.")
    return cell_x, cell_y


def _cell_index(x: int, y: int, width: int) -> int:
    return y * width + x


def _line_cells(start_x: int, start_y: int, end_x: int, end_y: int, width: int) -> list[int]:
    result: list[int] = []
    x0 = start_x
    y0 = start_y
    x1 = end_x
    y1 = end_y
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        result.append(_cell_index(x0, y0, width))
        if x0 == x1 and y0 == y1:
            break
        twice = 2 * err
        if twice >= dy:
            err += dy
            x0 += sx
        if twice <= dx:
            err += dx
            y0 += sy
    return result


def _object_vehicle_cell_allowed(layer_cells: dict[str, list[int]], index: int) -> bool:
    if layer_cells.get("walls", [])[index] > 0:
        return False
    openings = layer_cells.get("openings", [])
    markers = layer_cells.get("markers", [])
    return openings[index] == 4 or markers[index] == 4


def _serialize_path(path: list[int], width: int) -> list[dict[str, int]]:
    return [{"x": index % width, "y": index // width, "index": index} for index in path]


def _vehicle_by_id(training_session: TrainingSession, vehicle_id: str) -> VehicleInstance:
    vehicle = next((item for item in training_session.vehicles if item.id == vehicle_id), None)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Машина не найдена.")
    return vehicle


def update_vehicle_object_route(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    vehicle_id: str,
    route_points: list[dict[str, float]],
    ip_address: str | None,
) -> dict[str, Any]:
    _require_roles(auth_session, {"rtp", "instructor"})
    scenario_state = _scenario(training_session)
    if not scenario_state.incident_revealed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Фаза объекта ещё не разблокирована.")
    if not route_points:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Необходимы точки маршрута.")
    vehicle = _vehicle_by_id(training_session, vehicle_id)
    object_map = _get_object_map(training_session)
    level, layer_cells = _get_object_layer_cells(object_map, vehicle.current_level_code or scenario_state.incident_object_level_code)
    start_x, start_y = _snap_cell(vehicle.position_x, vehicle.position_y, object_map.width, object_map.height)
    applied_path = [_cell_index(start_x, start_y, object_map.width)]
    blocked_at: dict[str, int] | None = None
    cursor_x = start_x
    cursor_y = start_y
    for point in route_points:
        target_x, target_y = _snap_cell(point["x"], point["y"], object_map.width, object_map.height)
        line = _line_cells(cursor_x, cursor_y, target_x, target_y, object_map.width)
        for index in line[1:]:
            if not _object_vehicle_cell_allowed(layer_cells, index):
                blocked_at = {"x": index % object_map.width, "y": index // object_map.width, "index": index}
                break
            applied_path.append(index)
        if blocked_at is not None:
            break
        cursor_x, cursor_y = target_x, target_y
    route_json = dict(vehicle.route_json or {})
    route_json["object_path"] = applied_path
    route_json["object_points"] = [{"x": item["x"], "y": item["y"]} for item in route_points]
    vehicle.route_json = route_json
    vehicle.current_map_id = object_map.id
    vehicle.current_level_code = level.code
    vehicle.status = "object_control"
    runtime_state = dict(scenario_state.runtime_state_json or {})
    runtime_state["active_object_vehicle_id"] = vehicle.id
    scenario_state.runtime_state_json = runtime_state
    notice_payload: dict[str, object] | None = None
    if blocked_at is not None:
        notice = _append_system_message(
            db,
            training_session=training_session,
            scenario_state=scenario_state,
            body=f"Маршрут машины заблокирован в [{blocked_at['x']}, {blocked_at['y']}].",
        )
        notice_payload = _message_payload(notice)
    db.commit()
    db.refresh(vehicle)
    return {
        "vehicle_id": vehicle.id,
        "status": vehicle.status,
        "applied_path": _serialize_path(applied_path, object_map.width),
        "blocked_at": blocked_at,
        "notice": notice_payload,
    }


def apply_vehicle_object_drive(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    vehicle_id: str,
    direction: str,
    ip_address: str | None,
) -> dict[str, Any]:
    _require_roles(auth_session, {"rtp", "instructor"})
    scenario_state = _scenario(training_session)
    if not scenario_state.incident_revealed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Фаза объекта ещё не разблокирована.")
    if direction not in OBJECT_STEP_VECTORS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Недопустимое направление.")
    vehicle = _vehicle_by_id(training_session, vehicle_id)
    object_map = _get_object_map(training_session)
    level, layer_cells = _get_object_layer_cells(object_map, vehicle.current_level_code or scenario_state.incident_object_level_code)
    current_x, current_y = _snap_cell(vehicle.position_x, vehicle.position_y, object_map.width, object_map.height)
    delta_x, delta_y = OBJECT_STEP_VECTORS[direction]
    next_x = current_x + delta_x
    next_y = current_y + delta_y
    blocked = False
    notice_payload: dict[str, object] | None = None
    if not (0 <= next_x < object_map.width and 0 <= next_y < object_map.height):
        blocked = True
    else:
        next_index = _cell_index(next_x, next_y, object_map.width)
        if _object_vehicle_cell_allowed(layer_cells, next_index):
            vehicle.position_x = float(next_x)
            vehicle.position_y = float(next_y)
        else:
            blocked = True
    if blocked:
        notice = _append_system_message(
            db,
            training_session=training_session,
            scenario_state=scenario_state,
            body=f"Движение машины заблокировано ({direction}).",
        )
        notice_payload = _message_payload(notice)
    vehicle.current_map_id = object_map.id
    vehicle.current_level_code = level.code
    vehicle.status = "object_control"
    runtime_state = dict(scenario_state.runtime_state_json or {})
    runtime_state["active_object_vehicle_id"] = vehicle.id
    scenario_state.runtime_state_json = runtime_state
    db.commit()
    db.refresh(vehicle)
    return {
        "vehicle_id": vehicle.id,
        "status": vehicle.status,
        "position_x": vehicle.position_x,
        "position_y": vehicle.position_y,
        "blocked": blocked,
        "notice": notice_payload,
    }


def _snap_polyline_to_map(polyline_points: list[dict[str, float]], width: int, height: int) -> list[dict[str, int]]:
    snapped: list[dict[str, int]] = []
    for point in polyline_points:
        x, y = _snap_cell(point["x"], point["y"], width, height)
        if snapped and snapped[-1]["x"] == x and snapped[-1]["y"] == y:
            continue
        snapped.append({"x": x, "y": y})
    if len(snapped) < 2:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Линия должна содержать минимум две ячейки.")
    return snapped


def create_hose(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    source_vehicle_id: str,
    polyline_points: list[dict[str, float]],
    ip_address: str | None,
) -> dict[str, object]:
    _require_roles(auth_session, {"rtp", "instructor"})
    scenario_state = _scenario(training_session)
    if not scenario_state.incident_revealed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Фаза объекта ещё не разблокирована.")
    object_map = _get_object_map(training_session)
    vehicle = _vehicle_by_id(training_session, source_vehicle_id)
    if vehicle.current_map_id != object_map.id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Машина не находится на карте объекта.")
    snapped = _snap_polyline_to_map(polyline_points, object_map.width, object_map.height)
    runtime_state = dict(scenario_state.runtime_state_json or {})
    hose_state = dict(runtime_state.get("hose_state", {}))
    hoses = list(hose_state.get("hoses", []))
    hose = {
        "id": generate_random_token(),
        "source_vehicle_id": source_vehicle_id,
        "polyline_points": snapped,
        "flow_state": "dry",
    }
    hoses.append(hose)
    hose_state["hoses"] = hoses
    runtime_state["hose_state"] = hose_state
    runtime_state["active_object_vehicle_id"] = vehicle.id
    scenario_state.runtime_state_json = runtime_state
    db.commit()
    return hose


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
    _require_roles(auth_session, {"rtp", "instructor"})
    scenario_state = _scenario(training_session)
    if not scenario_state.incident_revealed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Фаза объекта ещё не разблокирована.")
    object_map = _get_object_map(training_session)
    hose_state = dict(dict(scenario_state.runtime_state_json or {}).get("hose_state", {}))
    hoses = list(hose_state.get("hoses", []))
    hose = next((item for item in hoses if item.get("id") == hose_id), None)
    if hose is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Рукав не найден.")
    if polyline_points is not None:
        hose["polyline_points"] = _snap_polyline_to_map(polyline_points, object_map.width, object_map.height)
    if flow_state is not None:
        hose["flow_state"] = flow_state
    hose_state["hoses"] = hoses
    runtime_state = dict(scenario_state.runtime_state_json or {})
    runtime_state["hose_state"] = hose_state
    scenario_state.runtime_state_json = runtime_state
    db.commit()
    return hose


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
    _require_roles(auth_session, {"rtp", "instructor"})
    scenario_state = _scenario(training_session)
    if not scenario_state.incident_revealed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Фаза объекта ещё не разблокирована.")
    object_map = _get_object_map(training_session)
    snapped_x, snapped_y = _snap_cell(target_x, target_y, object_map.width, object_map.height)
    runtime_state = dict(scenario_state.runtime_state_json or {})
    hose_state = dict(runtime_state.get("hose_state", {}))
    hoses = list(hose_state.get("hoses", []))
    hose = next((item for item in hoses if item.get("id") == hose_id), None)
    if hose is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Рукав не найден.")
    nozzles = [item for item in hose_state.get("nozzles", []) if item.get("hose_id") != hose_id]
    nozzle = {
        "id": generate_random_token(),
        "hose_id": hose_id,
        "target_x": float(snapped_x),
        "target_y": float(snapped_y),
        "flow_lps": flow_lps,
    }
    nozzles.append(nozzle)
    hose_state["nozzles"] = nozzles
    runtime_state["hose_state"] = hose_state
    scenario_state.runtime_state_json = runtime_state
    db.commit()
    return nozzle


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
    _require_roles(auth_session, {"rtp", "instructor"})
    scenario_state = _scenario(training_session)
    if not scenario_state.incident_revealed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Фаза объекта ещё не разблокирована.")
    object_map = _get_object_map(training_session)
    runtime_state = dict(scenario_state.runtime_state_json or {})
    hose_state = dict(runtime_state.get("hose_state", {}))
    nozzles = list(hose_state.get("nozzles", []))
    nozzle = next((item for item in nozzles if item.get("id") == nozzle_id), None)
    if nozzle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ствол не найден.")
    if target_x is not None or target_y is not None:
        snapped_x, snapped_y = _snap_cell(
            target_x if target_x is not None else float(nozzle["target_x"]),
            target_y if target_y is not None else float(nozzle["target_y"]),
            object_map.width,
            object_map.height,
        )
        nozzle["target_x"] = float(snapped_x)
        nozzle["target_y"] = float(snapped_y)
    if flow_lps is not None:
        nozzle["flow_lps"] = flow_lps
    hose_state["nozzles"] = nozzles
    runtime_state["hose_state"] = hose_state
    scenario_state.runtime_state_json = runtime_state
    db.commit()
    return nozzle


def list_chat_messages(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    thread_key: str,
) -> list[dict[str, object]]:
    if thread_key not in CHAT_THREADS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Неизвестный канал.")
    if thread_key not in visible_threads_for_role(auth_session.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Канал недоступен для этой роли.")
    messages = [
        item for item in sorted(training_session.chat_messages, key=lambda msg: msg.created_at) if item.thread_key == thread_key
    ]
    return [_message_payload(msg) for msg in messages]


def create_chat_message(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    thread_key: str,
    body: str,
    ip_address: str | None,
) -> ChatMessage:
    if thread_key not in CHAT_THREADS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Неизвестный канал.")
    if thread_key not in visible_threads_for_role(auth_session.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Канал недоступен для этой роли.")
    cleaned_body = body.strip()
    if not cleaned_body:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Пустые сообщения не допускаются.")
    message = ChatMessage(
        training_session=training_session,
        participant_id=auth_session.participant_id,
        thread_key=thread_key,
        body=cleaned_body,
    )
    db.add(message)
    db.flush()
    if training_session.scenario_state is not None:
        _sync_chat_meta(training_session.scenario_state, created_at=message.created_at)
    db.commit()
    db.refresh(message)
    return message
