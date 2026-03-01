from __future__ import annotations

from typing import Any

from app.db.models import MapDocument, ScenarioState
from app.domain.map_codec import decode_cells
from app.domain.tile_catalog_v3 import serialize_catalog


HIDDEN_LAYERS_FOR_DISPATCHER = {"effects_fire", "effects_smoke"}


def _normalized_role(role: str) -> str:
    return "instructor" if role == "admin" else role


def serialize_map_for_role(
    map_document: MapDocument,
    role: str,
    scenario_state: ScenarioState | None = None,
) -> dict[str, Any]:
    expected_count = map_document.width * map_document.height
    viewer_role = _normalized_role(role)
    hidden_layers: set[str] = set()

    if viewer_role == "dispatcher":
        hidden_layers |= HIDDEN_LAYERS_FOR_DISPATCHER
    if map_document.kind == "object" and viewer_role in {"dispatcher", "observer"} and not (scenario_state and scenario_state.incident_revealed):
        hidden_layers = {layer.layer_key for level in map_document.levels for layer in level.layers}
    if map_document.kind == "object" and viewer_role == "rtp" and scenario_state is not None and not scenario_state.incident_revealed:
        hidden_layers = {layer.layer_key for level in map_document.levels for layer in level.layers}

    levels_payload: list[dict[str, Any]] = []
    for level in map_document.levels:
        layers_payload: list[dict[str, Any]] = []
        for layer in level.layers:
            cells = decode_cells(layer.cells_blob, expected_count)
            if layer.layer_key in hidden_layers:
                cells = [0] * expected_count
            layers_payload.append(
                {
                    "id": layer.id,
                    "level_id": level.id,
                    "layer_key": layer.layer_key,
                    "z_index": layer.z_index,
                    "visible": layer.is_visible_default,
                    "locked": layer.is_locked_default,
                    "max_code": layer.max_code,
                    "cells": cells,
                }
            )
        levels_payload.append(
            {
                "id": level.id,
                "code": level.code,
                "title": level.title,
                "floor_number": level.floor_number,
                "sort_order": level.sort_order,
                "layers": layers_payload,
            }
        )

    return {
        "id": map_document.id,
        "scope": map_document.scope,
        "title": map_document.title,
        "kind": map_document.kind,
        "width": map_document.width,
        "height": map_document.height,
        "cell_size_px": map_document.cell_size_px,
        "meters_per_cell": map_document.meters_per_cell,
        "map_type": map_document.map_type,
        "parent_map_id": map_document.parent_map_id,
        "source_template_id": map_document.source_template_id,
        "version": map_document.version,
        "levels": levels_payload,
        "palette_manifest": serialize_catalog(map_document.kind),
    }


def build_runtime_overlay(
    map_document: MapDocument,
    *,
    viewer_role: str,
    scenario_state: ScenarioState | None,
) -> dict[str, Any] | None:
    if map_document.scope != "session" or map_document.training_session is None:
        return None

    role = _normalized_role(viewer_role)
    runtime_state = dict(scenario_state.runtime_state_json or {}) if scenario_state is not None else {}
    hose_state = dict(runtime_state.get("hose_state", {}))
    can_view_object_map = (
        map_document.kind != "object"
        or role == "instructor"
        or (scenario_state is not None and scenario_state.incident_revealed and role in {"rtp", "observer"})
    )
    vehicles = [
        {
            "id": vehicle.id,
            "display_name": vehicle.display_name,
            "vehicle_type": vehicle.vehicle_type,
            "status": vehicle.status,
            "current_map_id": vehicle.current_map_id,
            "current_level_code": vehicle.current_level_code,
            "position_x": vehicle.position_x,
            "position_y": vehicle.position_y,
            "heading_deg": vehicle.heading_deg,
        }
        for vehicle in map_document.training_session.vehicles
        if vehicle.current_map_id == map_document.id
    ]
    can_control_object_vehicle = (
        map_document.kind == "object"
        and role in {"rtp", "instructor"}
        and scenario_state is not None
        and scenario_state.incident_revealed
    )
    return {
        "vehicles": vehicles if can_view_object_map else [],
        "active_object_vehicle_id": runtime_state.get("active_object_vehicle_id"),
        "hoses": list(hose_state.get("hoses", [])) if map_document.kind == "object" and can_view_object_map else [],
        "nozzles": list(hose_state.get("nozzles", [])) if map_document.kind == "object" and can_view_object_map else [],
        "tactical_permissions": {
            "can_place_vehicle": can_control_object_vehicle,
            "can_route_vehicle": can_control_object_vehicle,
            "can_step_vehicle": can_control_object_vehicle,
            "can_create_hose": can_control_object_vehicle,
            "can_create_nozzle": can_control_object_vehicle,
        },
    }
