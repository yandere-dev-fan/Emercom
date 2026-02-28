from __future__ import annotations

import asyncio
import secrets
from collections import defaultdict
from datetime import timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas import ImportMapRequest, MapCreateRequest, MapMetadataUpdateRequest, MapPatchRequest
from app.config import Settings
from app.db.models import AuditEvent, AuthSession, ImportJob, MapDocument, MapLayer, MapLevel, MapSnapshot, Participant, TrainingSession
from app.domain.map_codec import decode_cells, empty_cells, encode_cells
from app.domain.tile_catalog_v2 import DEFAULT_AREA_LEVELS, DEFAULT_LAYER_ORDER, DEFAULT_OBJECT_LEVELS, TILE_CATALOG, max_code_for_layer, serialize_catalog
from app.security.auth import generate_join_key, generate_random_token, hash_secret, make_expiry, utc_now, verify_secret
from app.security.rate_limit import InMemoryJoinKeyLimiter


CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def generate_session_code(length: int = 10) -> str:
    return "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(length))


def _client_id(ip_address: str | None) -> str:
    return ip_address or "unknown"


def record_audit(
    db: Session,
    *,
    event_type: str,
    session_id: str | None = None,
    participant_id: str | None = None,
    ip_address: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            session_id=session_id,
            participant_id=participant_id,
            event_type=event_type,
            ip_address=ip_address,
            details_json=details or {},
        )
    )


def _unique_session_code(db: Session) -> str:
    while True:
        code = generate_session_code()
        if not db.scalar(select(TrainingSession.id).where(TrainingSession.session_code == code)):
            return code


def _create_auth_session(
    db: Session,
    *,
    training_session: TrainingSession,
    participant: Participant,
) -> tuple[AuthSession, str]:
    raw_token = generate_random_token()
    auth_session = AuthSession(
        training_session=training_session,
        participant=participant,
        role=participant.role,
        token_hash=hash_secret(raw_token),
        csrf_token=generate_random_token(),
        expires_at=make_expiry(hours=8),
        last_seen_at=utc_now(),
    )
    db.add(auth_session)
    return auth_session, raw_token


def create_training_session(db: Session, *, ip_address: str | None) -> tuple[TrainingSession, AuthSession, str, str]:
    join_key = generate_join_key()
    training_session = TrainingSession(session_code=_unique_session_code(db), join_key_hash=hash_secret(join_key))
    participant = Participant(training_session=training_session, role="instructor")
    db.add_all([training_session, participant])
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
    return training_session, auth_session, raw_token, join_key


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
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many failed attempts.")

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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid join key.")

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


def load_auth_session(db: Session, raw_cookie: str | None) -> AuthSession | None:
    from app.security.auth import parse_auth_cookie_value

    parsed = parse_auth_cookie_value(raw_cookie)
    if not parsed:
        return None
    auth_session_id, raw_token = parsed
    auth_session = db.get(AuthSession, auth_session_id)
    if auth_session is None:
        return None
    expires_at = auth_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= utc_now():
        return None
    if not verify_secret(auth_session.token_hash, raw_token):
        return None
    auth_session.last_seen_at = utc_now()
    auth_session.expires_at = utc_now() + timedelta(hours=8)
    db.commit()
    db.refresh(auth_session)
    return auth_session


def require_admin(auth_session: AuthSession) -> None:
    if auth_session.role not in {"admin", "instructor"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Instructor access required.")


def get_session_for_code(db: Session, session_code: str) -> TrainingSession | None:
    return db.scalar(select(TrainingSession).where(TrainingSession.session_code == session_code))


def get_training_session(db: Session, session_id: str) -> TrainingSession | None:
    return db.get(TrainingSession, session_id)


def get_session_map(db: Session, map_id: str) -> MapDocument | None:
    stmt = (
        select(MapDocument)
        .where(MapDocument.id == map_id)
        .options(selectinload(MapDocument.training_session), selectinload(MapDocument.levels).selectinload(MapLevel.layers), selectinload(MapDocument.snapshots))
    )
    return db.scalar(stmt)


def get_required_session_map(db: Session, map_id: str) -> MapDocument:
    map_document = get_session_map(db, map_id)
    if map_document is None:
        raise RuntimeError(f"Map {map_id} was expected to exist but was not found.")
    return map_document


def _levels_for_kind(kind: str) -> list[tuple[str, str]]:
    return DEFAULT_AREA_LEVELS if kind == "area" else DEFAULT_OBJECT_LEVELS


def _resolve_object_map_title(
    parent_map: MapDocument,
    source_level_id: str | None,
    source_index: int | None,
) -> str:
    base_title = f"{parent_map.title} / Object plan"
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

    building_label = next((item.label for item in TILE_CATALOG["buildings"] if item.code == building_code), f"b{building_code}")
    x = source_index % parent_map.width
    y = source_index // parent_map.width
    return f"{base_title} [{x},{y}] {building_label}"


def create_map_document(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    payload: MapCreateRequest,
    settings: Settings,
    ip_address: str | None,
) -> MapDocument:
    require_admin(auth_session)
    if payload.width > settings.map_max_width or payload.height > settings.map_max_height:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Map size exceeds configured limits.")
    if payload.kind == "object" and not payload.parent_map_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Object maps require parent_map_id.")
    if payload.parent_map_id:
        parent_map = db.get(MapDocument, payload.parent_map_id)
        if parent_map is None or parent_map.session_id != training_session.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent map not found in this session.")

    map_document = MapDocument(
        scope="session",
        training_session=training_session,
        source_template_id=None,
        parent_map_id=payload.parent_map_id,
        kind=payload.kind,
        title=payload.title,
        width=payload.width,
        height=payload.height,
        cell_size_px=payload.cell_size_px,
        meters_per_cell=payload.meters_per_cell,
        map_type=payload.map_type,
        version=1,
    )
    db.add(map_document)
    for sort_order, (level_code, level_title) in enumerate(_levels_for_kind(payload.kind), start=1):
        level = MapLevel(map_document=map_document, code=level_code, title=level_title, sort_order=sort_order)
        db.add(level)
        for z_index, layer_key in enumerate(DEFAULT_LAYER_ORDER, start=1):
            db.add(
                MapLayer(
                    map_level=level,
                    layer_key=layer_key,
                    z_index=z_index,
                    is_visible_default=True,
                    is_locked_default=False,
                    encoding="uint16-zlib",
                    cells_blob=encode_cells(empty_cells(payload.width, payload.height)),
                    max_code=max_code_for_layer(layer_key),
                )
            )
    record_audit(
        db,
        event_type="map_created",
        session_id=training_session.id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"title": payload.title, "kind": payload.kind},
    )
    db.commit()
    return get_required_session_map(db, map_document.id)


def serialize_map(map_document: MapDocument) -> dict[str, Any]:
    expected_count = map_document.width * map_document.height
    levels_payload: list[dict[str, Any]] = []
    for level in map_document.levels:
        layers_payload: list[dict[str, Any]] = []
        for layer in level.layers:
            layers_payload.append(
                {
                    "id": layer.id,
                    "level_id": level.id,
                    "layer_key": layer.layer_key,
                    "z_index": layer.z_index,
                    "visible": layer.is_visible_default,
                    "locked": layer.is_locked_default,
                    "max_code": layer.max_code,
                    "cells": decode_cells(layer.cells_blob, expected_count),
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
        "title": map_document.title,
        "kind": map_document.kind,
        "width": map_document.width,
        "height": map_document.height,
        "cell_size_px": map_document.cell_size_px,
        "meters_per_cell": map_document.meters_per_cell,
        "map_type": map_document.map_type,
        "parent_map_id": map_document.parent_map_id,
        "version": map_document.version,
        "levels": levels_payload,
        "palette_manifest": serialize_catalog(),
    }


def export_map_document(map_document: MapDocument) -> dict[str, Any]:
    expected_count = map_document.width * map_document.height
    levels_payload: list[dict[str, Any]] = []
    for level in map_document.levels:
        layers = {layer.layer_key: decode_cells(layer.cells_blob, expected_count) for layer in level.layers}
        levels_payload.append(
            {
                "id": level.id,
                "code": level.code,
                "title": level.title,
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
        "parent_map_id": map_document.parent_map_id,
        "version": map_document.version,
        "levels": levels_payload,
    }


def update_map_metadata(
    db: Session,
    *,
    map_document: MapDocument,
    auth_session: AuthSession,
    payload: MapMetadataUpdateRequest,
    ip_address: str | None,
) -> MapDocument:
    require_admin(auth_session)
    if payload.title:
        map_document.title = payload.title
        map_document.version += 1
        record_audit(
            db,
            event_type="map_metadata_updated",
            session_id=map_document.session_id,
            participant_id=auth_session.participant_id,
            ip_address=ip_address,
            details={"title": payload.title},
        )
        db.commit()
    return get_required_session_map(db, map_document.id)


def apply_patch(
    db: Session,
    *,
    map_document: MapDocument,
    auth_session: AuthSession,
    payload: MapPatchRequest,
    ip_address: str | None,
) -> dict[str, Any]:
    require_admin(auth_session)
    if payload.base_version != map_document.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Map version mismatch.", "current_version": map_document.version},
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
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Layer not found.")
        cells = decoded_cache.get(layer.id)
        if cells is None:
            cells = decode_cells(layer.cells_blob, expected_count)
            decoded_cache[layer.id] = cells
        writes_payload: list[dict[str, int]] = []
        for write in change.writes:
            if write.index >= expected_count:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Cell index out of range.")
            if write.value > layer.max_code:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Cell value exceeds layer max.")
            cells[write.index] = write.value
            writes_payload.append({"index": write.index, "value": write.value})
        applied_changes.append({"level_id": change.level_id, "layer_key": change.layer_key, "writes": writes_payload})

    for level in map_document.levels:
        for layer in level.layers:
            if layer.id in decoded_cache:
                layer.cells_blob = encode_cells(decoded_cache[layer.id])

    map_document.version += 1
    record_audit(
        db,
        event_type="map_patch_applied",
        session_id=map_document.session_id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"client_event_id": payload.client_event_id, "change_count": len(applied_changes)},
    )
    db.commit()
    db.refresh(map_document)
    return {
        "map_id": map_document.id,
        "version": map_document.version,
        "changes": applied_changes,
        "session_code": map_document.training_session.session_code,
    }


def create_snapshot(
    db: Session,
    *,
    map_document: MapDocument,
    auth_session: AuthSession,
    label: str,
    ip_address: str | None,
) -> MapSnapshot:
    require_admin(auth_session)
    snapshot = MapSnapshot(map_document=map_document, label=label, version=map_document.version, payload_json=export_map_document(map_document))
    db.add(snapshot)
    record_audit(
        db,
        event_type="map_snapshot_created",
        session_id=map_document.session_id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"label": label},
    )
    db.commit()
    db.refresh(snapshot)
    return snapshot


def list_snapshots(map_document: MapDocument) -> list[dict[str, Any]]:
    return [
        {
            "id": snapshot.id,
            "label": snapshot.label,
            "version": snapshot.version,
            "created_at": snapshot.created_at.isoformat(),
        }
        for snapshot in sorted(map_document.snapshots, key=lambda item: item.created_at, reverse=True)
    ]


def import_map_document(
    db: Session,
    *,
    training_session: TrainingSession,
    auth_session: AuthSession,
    payload: ImportMapRequest,
    ip_address: str | None,
) -> MapDocument:
    require_admin(auth_session)
    exported = payload.payload
    map_document = MapDocument(
        scope="session",
        training_session=training_session,
        source_template_id=None,
        parent_map_id=exported.parent_map_id,
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
    for level_payload in exported.levels:
        level = MapLevel(map_document=map_document, code=level_payload.code, title=level_payload.title, sort_order=level_payload.sort_order)
        db.add(level)
        for z_index, layer_key in enumerate(DEFAULT_LAYER_ORDER, start=1):
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
                    max_code=max_code_for_layer(layer_key),
                )
            )
    record_audit(
        db,
        event_type="map_imported",
        session_id=training_session.id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"title": exported.title},
    )
    db.commit()
    return get_required_session_map(db, map_document.id)


def create_object_map_from_existing(
    db: Session,
    *,
    parent_map: MapDocument,
    auth_session: AuthSession,
    settings: Settings,
    ip_address: str | None,
    source_level_id: str | None = None,
    source_index: int | None = None,
) -> MapDocument:
    if parent_map.kind != "area":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Object maps can only be created from area maps.")
    payload = MapCreateRequest(
        title=f"{parent_map.title} / Object plan",
        kind="object",
        width=parent_map.width,
        height=parent_map.height,
        cell_size_px=parent_map.cell_size_px,
        meters_per_cell=max(parent_map.meters_per_cell // 2, 1),
        map_type=parent_map.map_type,
        parent_map_id=parent_map.id,
    )
    payload = payload.model_copy(update={"title": _resolve_object_map_title(parent_map, source_level_id, source_index)})
    return create_map_document(
        db,
        training_session=parent_map.training_session,
        auth_session=auth_session,
        payload=payload,
        settings=settings,
        ip_address=ip_address,
    )


def reorder_map_layer(
    db: Session,
    *,
    map_document: MapDocument,
    auth_session: AuthSession,
    level_id: str,
    layer_key: str,
    direction: str,
    ip_address: str | None,
) -> dict[str, Any]:
    require_admin(auth_session)
    level = next((item for item in map_document.levels if item.id == level_id), None)
    if level is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Level not found.")

    ordered_layers = sorted(level.layers, key=lambda item: item.z_index)
    current_index = next((index for index, item in enumerate(ordered_layers) if item.layer_key == layer_key), None)
    if current_index is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Layer not found.")

    target_index = current_index - 1 if direction == "up" else current_index + 1
    if target_index < 0 or target_index >= len(ordered_layers):
        return {
            "map_id": map_document.id,
            "version": map_document.version,
            "level_id": level.id,
            "layer_order": [item.layer_key for item in ordered_layers],
            "session_code": map_document.training_session.session_code,
        }

    ordered_layers[current_index].z_index, ordered_layers[target_index].z_index = (
        ordered_layers[target_index].z_index,
        ordered_layers[current_index].z_index,
    )
    ordered_layers = sorted(ordered_layers, key=lambda item: item.z_index)
    level.layers[:] = ordered_layers
    map_document.version += 1
    record_audit(
        db,
        event_type="map_layer_reordered",
        session_id=map_document.session_id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"level_id": level.id, "layer_key": layer_key, "direction": direction},
    )
    db.commit()
    db.refresh(map_document)
    return {
        "map_id": map_document.id,
        "version": map_document.version,
        "level_id": level.id,
        "layer_order": [item.layer_key for item in ordered_layers],
        "session_code": map_document.training_session.session_code,
    }


def create_import_job(
    db: Session,
    *,
    map_document: MapDocument,
    auth_session: AuthSession,
    ip_address: str | None,
) -> ImportJob:
    require_admin(auth_session)
    job = ImportJob(map_document=map_document, status="queued")
    db.add(job)
    record_audit(
        db,
        event_type="image_import_queued",
        session_id=map_document.session_id,
        participant_id=auth_session.participant_id,
        ip_address=ip_address,
        details={"map_id": map_document.id},
    )
    db.commit()
    db.refresh(job)
    return job


def grouped_maps(training_session: TrainingSession) -> dict[str, list[MapDocument]]:
    grouped: dict[str, list[MapDocument]] = defaultdict(list)
    for map_document in sorted(training_session.maps, key=lambda item: item.created_at):
        if map_document.scope != "session":
            continue
        grouped[map_document.kind].append(map_document)
    return grouped
