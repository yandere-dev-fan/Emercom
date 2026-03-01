from __future__ import annotations

import asyncio

from app.db.models import ChatMessage, TrainingSession
from app.db.session import SessionLocal
from app.domain.fire_sim import apply_fire_tick
from app.domain.map_codec import decode_cells, encode_cells
from app.domain.tile_catalog_v3 import area_travel_cost
from app.domain.vehicle_catalog_v2 import VEHICLE_CATALOG
from app.runtime.state import ManagedRuntimeSession


def _message_payload(message: ChatMessage) -> dict[str, object]:
    return {
        "id": message.id,
        "thread_key": message.thread_key,
        "body": message.body,
        "participant_id": message.participant_id,
        "created_at": message.created_at.isoformat(),
    }


async def run_runtime_loop(
    runtime_session: ManagedRuntimeSession,
    *,
    ws_manager,
    tick_rate_hz: float = 1.0,
) -> None:
    delay_seconds = 1.0 / tick_rate_hz
    while runtime_session.running:
        await asyncio.sleep(delay_seconds)
        with SessionLocal() as db:
            training_session = db.get(TrainingSession, runtime_session.session_id)
            if training_session is None:
                runtime_session.running = False
                return
            scenario_state = training_session.scenario_state
            if scenario_state is None:
                runtime_session.running = False
                return
            if scenario_state.status not in {"dispatch_call", "enroute", "tactical"}:
                continue

            scenario_state.time_elapsed_minutes += 1
            system_messages: list[ChatMessage] = []
            vehicle_updates: list[dict[str, object]] = []
            vehicle_arrivals: list[dict[str, object]] = []
            fire_patch_events: list[dict[str, object]] = []
            fire_levels_changed: list[str] = []
            object_phase_unlocked = False

            if scenario_state.status in {"enroute", "tactical"}:
                area_map = next((item for item in training_session.maps if item.id == scenario_state.area_map_id), None)
                if area_map is not None:
                    level = next((item for item in area_map.levels if item.code == "AREA_MAIN"), area_map.levels[0])
                    decoded = {layer.layer_key: decode_cells(layer.cells_blob, area_map.width * area_map.height) for layer in level.layers}
                    ground = decoded.get("ground", [0] * (area_map.width * area_map.height))
                    objects = decoded.get("objects", [0] * (area_map.width * area_map.height))
                    buildings = decoded.get("buildings", [0] * (area_map.width * area_map.height))
                    costs = [area_travel_cost(ground[i], objects[i], buildings[i]) for i in range(area_map.width * area_map.height)]
                    for vehicle in training_session.vehicles:
                        if vehicle.status != "enroute":
                            continue
                        route_json = dict(vehicle.route_json or {})
                        path = list(route_json.get("path", []))
                        cursor = int(route_json.get("cursor", 0))
                        budget = float(route_json.get("movement_budget", 0.0))
                        speed = float(VEHICLE_CATALOG.get(vehicle.vehicle_type, {}).get("cells_per_minute", 1.0))
                        budget += speed
                        moved = False
                        while cursor < len(path) - 1:
                            next_index = path[cursor + 1]
                            step_cost = costs[next_index]
                            if step_cost is None or budget < step_cost:
                                break
                            budget -= step_cost
                            cursor += 1
                            moved = True
                            vehicle.position_x = float(next_index % area_map.width)
                            vehicle.position_y = float(next_index // area_map.width)
                        route_json["cursor"] = cursor
                        route_json["movement_budget"] = budget
                        vehicle.route_json = route_json
                        if moved:
                            vehicle_updates.append(
                                {
                                    "id": vehicle.id,
                                    "status": vehicle.status,
                                    "current_map_id": vehicle.current_map_id,
                                    "position_x": vehicle.position_x,
                                    "position_y": vehicle.position_y,
                                    "heading_deg": vehicle.heading_deg,
                                }
                            )
                        if path and cursor >= len(path) - 1:
                            vehicle.status = "arrived"
                            result = "confirmed" if scenario_state.dispatcher_guess_correct else "false_arrival"
                            vehicle_arrivals.append({"vehicle_id": vehicle.id, "result": result})
                            system_messages.append(
                                ChatMessage(
                                    training_session=training_session,
                                    participant=None,
                                    thread_key="system",
                                    body=(
                                        f"{vehicle.display_name} прибыл в зону происшествия."
                                        if scenario_state.dispatcher_guess_correct
                                        else f"{vehicle.display_name} прибыл в указанную точку. Пожар не обнаружен."
                                    ),
                                )
                            )
                            db.add(system_messages[-1])
                            runtime_state = dict(scenario_state.runtime_state_json or {})
                            arrival_state = dict(runtime_state.get("arrival_state", {}))
                            arrival_state["last_result"] = result
                            runtime_state["arrival_state"] = arrival_state
                            if scenario_state.dispatcher_guess_correct and not scenario_state.incident_revealed:
                                scenario_state.incident_revealed = True
                                scenario_state.status = "tactical"
                                object_phase_unlocked = True
                                vehicle.current_map_id = scenario_state.object_map_id
                                vehicle.current_level_code = scenario_state.incident_object_level_code
                                vehicle.status = "object_control"
                                runtime_state["active_object_vehicle_id"] = vehicle.id
                            elif not scenario_state.dispatcher_guess_correct and not scenario_state.incident_revealed:
                                scenario_state.status = "dispatch_call"
                                for other_vehicle in training_session.vehicles:
                                    if other_vehicle.status in {"enroute", "arrived"}:
                                        other_vehicle.status = "staged"
                                        other_vehicle.current_map_id = scenario_state.area_map_id
                                        other_vehicle.current_level_code = "AREA_MAIN"
                                        other_vehicle.route_json = {
                                            "path": [],
                                            "cursor": 0,
                                            "movement_budget": 0.0,
                                            "target_index": None,
                                            "object_path": [],
                                        }
                                system_messages.append(
                                    ChatMessage(
                                        training_session=training_session,
                                        participant=None,
                                        thread_key="system",
                                        body="Пожар в указанной точке не обнаружен. Техника возвращена для нового вызова.",
                                    )
                                )
                                db.add(system_messages[-1])
                            scenario_state.runtime_state_json = runtime_state

            if scenario_state.status == "tactical":
                object_map = next((item for item in training_session.maps if item.id == scenario_state.object_map_id), None)
                if object_map is not None:
                    runtime_state = dict(scenario_state.runtime_state_json or {})
                    fire_state = dict(runtime_state.get("fire_state", {}))
                    hose_state = dict(runtime_state.get("hose_state", {}))
                    nozzles = list(hose_state.get("nozzles", []))
                    map_changed = False
                    for level in object_map.levels:
                        layer_map = {layer.layer_key: decode_cells(layer.cells_blob, object_map.width * object_map.height) for layer in level.layers}
                        heat_by_level = dict(fire_state.get("heat_by_level", {}))
                        state_by_level = dict(fire_state.get("state_by_level", {}))
                        smoke_by_level = dict(fire_state.get("smoke_by_level", {}))
                        heat = list(heat_by_level.get(level.id, [0] * (object_map.width * object_map.height)))
                        cell_state = list(state_by_level.get(level.id, [0] * (object_map.width * object_map.height)))
                        nozzle_targets = [
                            int(round(item["target_y"])) * object_map.width + int(round(item["target_x"]))
                            for item in nozzles
                            if 0 <= int(round(item["target_x"])) < object_map.width
                            and 0 <= int(round(item["target_y"])) < object_map.height
                        ]
                        next_heat, next_state, next_smoke = apply_fire_tick(
                            width=object_map.width,
                            height=object_map.height,
                            floor_cells=layer_map.get("floor", [0] * (object_map.width * object_map.height)),
                            wall_cells=layer_map.get("walls", [0] * (object_map.width * object_map.height)),
                            openings_cells=layer_map.get("openings", [0] * (object_map.width * object_map.height)),
                            interior_cells=layer_map.get("interior", [0] * (object_map.width * object_map.height)),
                            heat=heat,
                            state=cell_state,
                            nozzle_targets=nozzle_targets,
                        )
                        heat_by_level[level.id] = next_heat
                        state_by_level[level.id] = next_state
                        smoke_by_level[level.id] = next_smoke
                        fire_state["heat_by_level"] = heat_by_level
                        fire_state["state_by_level"] = state_by_level
                        fire_state["smoke_by_level"] = smoke_by_level

                        fire_writes = [{"index": index, "value": value} for index, value in enumerate(next_state) if value != cell_state[index]]
                        smoke_writes = [
                            {"index": index, "value": value}
                            for index, value in enumerate(next_smoke)
                            if value != layer_map.get("effects_smoke", [0] * (object_map.width * object_map.height))[index]
                        ]
                        if fire_writes or smoke_writes:
                            map_changed = True
                            fire_levels_changed.append(level.id)
                        for layer in level.layers:
                            if layer.layer_key == "effects_fire":
                                layer.cells_blob = encode_cells(next_state)
                                if fire_writes:
                                    fire_patch_events.append(
                                        {"level_id": level.id, "layer_key": "effects_fire", "writes": fire_writes}
                                    )
                            elif layer.layer_key == "effects_smoke":
                                layer.cells_blob = encode_cells(next_smoke)
                                if smoke_writes:
                                    fire_patch_events.append(
                                        {"level_id": level.id, "layer_key": "effects_smoke", "writes": smoke_writes}
                                    )
                    if map_changed:
                        object_map.version += 1
                    runtime_state["fire_state"] = fire_state
                    scenario_state.runtime_state_json = runtime_state

            for message in system_messages:
                db.flush()
            db.commit()
            db.refresh(scenario_state)

            for message in system_messages:
                payload = _message_payload(message)
                await ws_manager.broadcast(training_session.session_code, {"type": "chat_message_created", "message": payload})
                await ws_manager.broadcast(training_session.session_code, {"type": "system_notice", "message": payload["body"]})
            if vehicle_updates:
                await ws_manager.broadcast(training_session.session_code, {"type": "vehicle_path_updated", "vehicles": vehicle_updates})
            for arrival in vehicle_arrivals:
                await ws_manager.broadcast(training_session.session_code, {"type": "vehicle_arrived", **arrival})
            await ws_manager.broadcast(
                training_session.session_code,
                {
                    "type": "scenario_tick",
                    "session_code": training_session.session_code,
                    "status": scenario_state.status,
                    "time_elapsed_minutes": scenario_state.time_elapsed_minutes,
                    "incident_revealed": scenario_state.incident_revealed,
                },
            )
            if object_phase_unlocked:
                await ws_manager.broadcast(training_session.session_code, {"type": "object_phase_unlocked", "status": scenario_state.status})
            if fire_patch_events:
                await ws_manager.broadcast(
                    training_session.session_code,
                    {
                        "type": "map_patch_applied",
                        "map_id": scenario_state.object_map_id,
                        "version": next((item for item in training_session.maps if item.id == scenario_state.object_map_id), None).version,
                        "changes": fire_patch_events,
                    },
                )
                await ws_manager.broadcast(
                    training_session.session_code,
                    {
                        "type": "fire_tick_applied",
                        "time_elapsed_minutes": scenario_state.time_elapsed_minutes,
                        "level_ids": sorted(set(fire_levels_changed)),
                    },
                )
