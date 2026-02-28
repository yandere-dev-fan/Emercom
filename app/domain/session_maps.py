from __future__ import annotations

from typing import Any

from app.db.models import MapDocument
from app.domain.map_codec import decode_cells
from app.domain.tile_catalog_v2 import serialize_catalog


HIDDEN_LAYERS_FOR_DISPATCHER = {"effects_fire", "effects_smoke"}


def serialize_map_for_role(
    map_document: MapDocument,
    role: str,
    scenario_state: object | None = None,
) -> dict[str, Any]:
    expected_count = map_document.width * map_document.height
    hidden_layers: set[str] = set()
    if role == "dispatcher" and scenario_state is not None and getattr(scenario_state, "status", None) in {"setup", "dispatch_call", "enroute"}:
        hidden_layers = HIDDEN_LAYERS_FOR_DISPATCHER

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
        "palette_manifest": serialize_catalog(),
    }
