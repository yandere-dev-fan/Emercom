from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas import ImportMapRequest, MapCreateRequest, MapMetadataUpdateRequest, MapPatchRequest
from app.db.models import AuditEvent, MapDocument, MapLayer, MapLevel, MapSnapshot, TrainingSession
from app.domain.map_codec import decode_cells, empty_cells, encode_cells
from app.domain.tile_catalog_v3 import (
    MAX_OBJECT_FLOORS,
    AREA_TILE_CATALOG,
    default_levels_for_kind,
    layer_order_for_kind,
    max_code_for_layer,
    object_level_code,
    object_level_title,
)


def _record_template_audit(db: Session, *, event_type: str, details: dict[str, Any] | None = None) -> None:
    db.add(AuditEvent(event_type=event_type, details_json=details or {}))


def _seed_level(*, map_document: MapDocument, level_code: str, level_title: str, floor_number: int, sort_order: int) -> MapLevel:
    level = MapLevel(
        map_document=map_document,
        code=level_code,
        title=level_title,
        floor_number=floor_number,
        sort_order=sort_order,
    )
    for z_index, layer_key in enumerate(layer_order_for_kind(map_document.kind), start=1):
        level.layers.append(
            MapLayer(
                layer_key=layer_key,
                z_index=z_index,
                is_visible_default=True,
                is_locked_default=False,
                encoding="uint16-zlib",
                cells_blob=encode_cells(empty_cells(map_document.width, map_document.height)),
                max_code=max_code_for_layer(layer_key, map_document.kind),
            )
        )
    map_document.levels.append(level)
    return level


def _seed_map_layers(map_document: MapDocument) -> None:
    for sort_order, (level_code, level_title, floor_number) in enumerate(default_levels_for_kind(map_document.kind), start=1):
        _seed_level(
            map_document=map_document,
            level_code=level_code,
            level_title=level_title,
            floor_number=floor_number,
            sort_order=sort_order,
        )


def get_template_map(db: Session, map_id: str) -> MapDocument | None:
    stmt = (
        select(MapDocument)
        .where(MapDocument.id == map_id, MapDocument.scope == "template")
        .options(selectinload(MapDocument.levels).selectinload(MapLevel.layers), selectinload(MapDocument.snapshots))
    )
    return db.scalar(stmt)


def list_template_maps(db: Session) -> list[MapDocument]:
    stmt = (
        select(MapDocument)
        .where(MapDocument.scope == "template")
        .options(selectinload(MapDocument.levels), selectinload(MapDocument.snapshots))
        .order_by(MapDocument.kind.asc(), MapDocument.created_at.asc())
    )
    return list(db.scalars(stmt).all())


def create_template_map(db: Session, *, payload: MapCreateRequest) -> MapDocument:
    if payload.kind == "object" and payload.parent_map_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="У шаблонной карты объекта не используется parent_map_id.")
    map_document = MapDocument(
        scope="template",
        session_id=None,
        parent_map_id=None,
        source_template_id=None,
        kind=payload.kind,
        title=payload.title,
        width=payload.width,
        height=payload.height,
        cell_size_px=payload.cell_size_px,
        meters_per_cell=payload.meters_per_cell,
        map_type=payload.map_type,
        version=1,
    )
    _seed_map_layers(map_document)
    db.add(map_document)
    _record_template_audit(db, event_type="template_map_created", details={"title": payload.title, "kind": payload.kind})
    db.commit()
    return get_template_map(db, map_document.id) or map_document


def add_template_level(db: Session, *, map_document: MapDocument) -> MapLevel:
    if map_document.kind != "object":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Этажи можно добавлять только у карты объекта.")
    if len(map_document.levels) >= MAX_OBJECT_FLOORS:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Достигнут лимит этажей.")
    next_floor = max((level.floor_number for level in map_document.levels), default=0) + 1
    level = _seed_level(
        map_document=map_document,
        level_code=object_level_code(next_floor),
        level_title=object_level_title(next_floor),
        floor_number=next_floor,
        sort_order=len(map_document.levels) + 1,
    )
    map_document.version += 1
    _record_template_audit(db, event_type="template_level_added", details={"map_id": map_document.id, "floor_number": next_floor})
    db.commit()
    db.refresh(level)
    return level


def update_template_map_metadata(db: Session, *, map_document: MapDocument, payload: MapMetadataUpdateRequest) -> MapDocument:
    if payload.title:
        map_document.title = payload.title
        map_document.version += 1
        _record_template_audit(db, event_type="template_map_metadata_updated", details={"map_id": map_document.id, "title": payload.title})
        db.commit()
    return get_template_map(db, map_document.id) or map_document


def apply_template_patch(db: Session, *, map_document: MapDocument, payload: MapPatchRequest) -> dict[str, Any]:
    if payload.base_version != map_document.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Версия карты не совпадает.", "current_version": map_document.version},
        )

    expected_count = map_document.width * map_document.height
    layers_by_key: dict[tuple[str, str], MapLayer] = {}
    decoded_cache: dict[str, list[int]] = {}
    for level in map_document.levels:
        for layer in level.layers:
            layers_by_key[(level.id, layer.layer_key)] = layer

    applied_changes: list[dict[str, Any]] = []
    for change in payload.changes:
        layer = layers_by_key.get((change.level_id, change.layer_key))
        if layer is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Слой не найден.")
        cells = decoded_cache.get(layer.id)
        if cells is None:
            cells = decode_cells(layer.cells_blob, expected_count)
            decoded_cache[layer.id] = cells
        writes_payload: list[dict[str, int]] = []
        for write in change.writes:
            if write.index >= expected_count:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Индекс клетки вне диапазона.")
            if write.value > layer.max_code:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Значение превышает допустимый максимум слоя.")
            cells[write.index] = write.value
            writes_payload.append({"index": write.index, "value": write.value})
        applied_changes.append({"level_id": change.level_id, "layer_key": change.layer_key, "writes": writes_payload})

    for level in map_document.levels:
        for layer in level.layers:
            if layer.id in decoded_cache:
                layer.cells_blob = encode_cells(decoded_cache[layer.id])

    map_document.version += 1
    _record_template_audit(
        db,
        event_type="template_map_patch_applied",
        details={"map_id": map_document.id, "client_event_id": payload.client_event_id, "change_count": len(applied_changes)},
    )
    db.commit()
    db.refresh(map_document)
    return {"map_id": map_document.id, "version": map_document.version, "changes": applied_changes}


def create_template_snapshot(db: Session, *, map_document: MapDocument, label: str) -> MapSnapshot:
    snapshot = MapSnapshot(map_document=map_document, label=label, version=map_document.version, payload_json=export_template_map(map_document))
    db.add(snapshot)
    _record_template_audit(db, event_type="template_map_snapshot_created", details={"map_id": map_document.id, "label": label})
    db.commit()
    db.refresh(snapshot)
    return snapshot


def export_template_map(map_document: MapDocument) -> dict[str, Any]:
    expected_count = map_document.width * map_document.height
    levels_payload: list[dict[str, Any]] = []
    for level in map_document.levels:
        layers = {layer.layer_key: decode_cells(layer.cells_blob, expected_count) for layer in level.layers}
        levels_payload.append(
            {
                "id": level.id,
                "code": level.code,
                "title": level.title,
                "floor_number": level.floor_number,
                "sort_order": level.sort_order,
                "layers": layers,
            }
        )
    return {
        "title": map_document.title,
        "kind": map_document.kind,
        "width": map_document.width,
        "height": map_document.height,
        "cell_size_px": map_document.cell_size_px,
        "meters_per_cell": map_document.meters_per_cell,
        "map_type": map_document.map_type,
        "parent_map_id": None,
        "version": map_document.version,
        "levels": levels_payload,
    }


def import_template_map(db: Session, *, payload: ImportMapRequest) -> MapDocument:
    exported = payload.payload
    map_document = MapDocument(
        scope="template",
        session_id=None,
        parent_map_id=None,
        source_template_id=None,
        kind=exported.kind,
        title=exported.title,
        width=exported.width,
        height=exported.height,
        cell_size_px=exported.cell_size_px,
        meters_per_cell=exported.meters_per_cell,
        map_type=exported.map_type,
        version=max(exported.version, 1),
    )
    db.add(map_document)
    expected_count = exported.width * exported.height
    layer_order = layer_order_for_kind(exported.kind)
    for level_payload in exported.levels:
        level = MapLevel(
            map_document=map_document,
            code=level_payload.code,
            title=level_payload.title,
            floor_number=level_payload.floor_number,
            sort_order=level_payload.sort_order,
        )
        db.add(level)
        for z_index, layer_key in enumerate(layer_order, start=1):
            values = level_payload.layers.get(layer_key, [0] * expected_count)
            db.add(
                MapLayer(
                    map_level=level,
                    layer_key=layer_key,
                    z_index=z_index,
                    is_visible_default=True,
                    is_locked_default=False,
                    encoding="uint16-zlib",
                    cells_blob=encode_cells(values),
                    max_code=max_code_for_layer(layer_key, exported.kind),
                )
            )
    _record_template_audit(db, event_type="template_map_imported", details={"title": exported.title})
    db.commit()
    return get_template_map(db, map_document.id) or map_document


def clone_template_to_session_map(
    db: Session,
    *,
    template_map: MapDocument,
    training_session: TrainingSession,
    parent_map_id: str | None = None,
) -> MapDocument:
    expected_count = template_map.width * template_map.height
    runtime_map = MapDocument(
        scope="session",
        session_id=training_session.id,
        parent_map_id=parent_map_id,
        source_template_id=template_map.id,
        kind=template_map.kind,
        title=template_map.title,
        width=template_map.width,
        height=template_map.height,
        cell_size_px=template_map.cell_size_px,
        meters_per_cell=template_map.meters_per_cell,
        map_type=template_map.map_type,
        version=1,
    )
    db.add(runtime_map)
    for level_payload in template_map.levels:
        level = MapLevel(
            map_document=runtime_map,
            code=level_payload.code,
            title=level_payload.title,
            floor_number=level_payload.floor_number,
            sort_order=level_payload.sort_order,
        )
        db.add(level)
        for template_layer in level_payload.layers:
            db.add(
                MapLayer(
                    map_level=level,
                    layer_key=template_layer.layer_key,
                    z_index=template_layer.z_index,
                    is_visible_default=template_layer.is_visible_default,
                    is_locked_default=template_layer.is_locked_default,
                    encoding=template_layer.encoding,
                    cells_blob=encode_cells(decode_cells(template_layer.cells_blob, expected_count)),
                    max_code=template_layer.max_code,
                )
            )
    return runtime_map


def _resolve_template_object_map_title(
    parent_map: MapDocument,
    source_level_id: str | None,
    source_index: int | None,
) -> str:
    base_title = f"{parent_map.title} / Объектовая карта"
    if source_level_id is None or source_index is None:
        return base_title

    expected_count = parent_map.width * parent_map.height
    if source_index >= expected_count:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Selected building index is out of range.")

    level = next((item for item in parent_map.levels if item.id == source_level_id), None)
    if level is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Selected level was not found.")

    building_layer = next((item for item in level.layers if item.layer_key == "buildings"), None)
    if building_layer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Buildings layer was not found.")

    building_cells = decode_cells(building_layer.cells_blob, expected_count)
    building_code = building_cells[source_index]
    if building_code == 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Selected cell does not contain a building.")

    building_label = next((item.label for item in AREA_TILE_CATALOG["buildings"] if item.code == building_code), f"b{building_code}")
    x = source_index % parent_map.width
    y = source_index // parent_map.width
    return f"{base_title} [{x},{y}] {building_label}"


def create_template_object_map_from_existing(
    db: Session,
    *,
    parent_map: MapDocument,
    source_level_id: str | None = None,
    source_index: int | None = None,
) -> MapDocument:
    if parent_map.kind != "area":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Object maps can only be created from area maps.")
    payload = MapCreateRequest(
        title=_resolve_template_object_map_title(parent_map, source_level_id, source_index),
        kind="object",
        width=parent_map.width,
        height=parent_map.height,
        cell_size_px=parent_map.cell_size_px,
        meters_per_cell=max(parent_map.meters_per_cell // 2, 1),
        map_type=parent_map.map_type,
        parent_map_id=parent_map.id,
    )
    return create_template_map(db, payload=payload)
