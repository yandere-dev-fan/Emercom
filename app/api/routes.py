from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.schemas import ImportMapRequest, LayerReorderRequest, MapMetadataUpdateRequest, MapPatchRequest, ObjectMapCreateRequest, SnapshotCreateRequest
from app.config import Settings, get_settings
from app.db.session import get_db
from app.domain.session_maps_v2 import build_runtime_overlay, serialize_map_for_role
from app.domain.services import (
    apply_patch,
    create_import_job,
    create_object_map_from_existing,
    create_snapshot,
    export_map_document,
    get_session_map,
    get_training_session,
    import_map_document,
    list_snapshots,
    load_auth_session,
    reorder_map_layer,
    update_map_metadata,
)
from app.domain.tile_catalog_v3 import serialize_catalog
from app.security.csrf import validate_csrf


router = APIRouter(prefix="/api")
DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def _require_auth(request: Request, db: Session):
    settings = get_settings()
    auth_session = load_auth_session(db, request.cookies.get(settings.session_cookie_name))
    if auth_session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    return auth_session


@router.get("/catalog")
def catalog() -> dict[str, object]:
    return {"layers": serialize_catalog()}


@router.get("/maps/{map_id}")
def get_map(map_id: str, request: Request, db: DbSession) -> dict[str, object]:
    auth_session = _require_auth(request, db)
    map_document = get_session_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Map not found.")
    if map_document.scope != "session":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template maps use /api/templates.")
    if map_document.session_id != auth_session.training_session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    scenario_state = map_document.training_session.scenario_state if map_document.training_session is not None else None
    payload = serialize_map_for_role(map_document, auth_session.role, scenario_state)
    runtime_overlay = build_runtime_overlay(map_document, viewer_role=auth_session.role, scenario_state=scenario_state)
    if runtime_overlay is not None:
        payload["runtime_overlay"] = runtime_overlay
    return payload


@router.put("/maps/{map_id}/metadata")
async def put_map_metadata(
    map_id: str,
    payload: MapMetadataUpdateRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session = _require_auth(request, db)
    await validate_csrf(request, auth_session, settings)
    map_document = get_session_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Map not found.")
    if map_document.session_id != auth_session.training_session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    updated_map = update_map_metadata(
        db,
        map_document=map_document,
        auth_session=auth_session,
        payload=payload,
        ip_address=request.client.host if request.client else None,
    )
    await request.app.state.ws_manager.broadcast(
        map_document.training_session.session_code,
        {"type": "map_metadata_updated", "map_id": map_document.id, "title": updated_map.title, "version": updated_map.version},
    )
    return {"ok": True, "version": updated_map.version, "title": updated_map.title}


@router.post("/maps/{map_id}/patches")
async def post_map_patch(
    map_id: str,
    payload: MapPatchRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session = _require_auth(request, db)
    await validate_csrf(request, auth_session, settings)
    map_document = get_session_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Map not found.")
    if map_document.session_id != auth_session.training_session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    result = apply_patch(
        db,
        map_document=map_document,
        auth_session=auth_session,
        payload=payload,
        ip_address=request.client.host if request.client else None,
    )
    await request.app.state.ws_manager.broadcast(
        result["session_code"],
        {"type": "map_patch_applied", "map_id": map_document.id, "version": result["version"], "changes": result["changes"]},
    )
    return {"ok": True, **result}


@router.post("/maps/{map_id}/snapshots")
async def post_map_snapshot(
    map_id: str,
    payload: SnapshotCreateRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session = _require_auth(request, db)
    await validate_csrf(request, auth_session, settings)
    map_document = get_session_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Map not found.")
    if map_document.session_id != auth_session.training_session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    snapshot = create_snapshot(
        db,
        map_document=map_document,
        auth_session=auth_session,
        label=payload.label,
        ip_address=request.client.host if request.client else None,
    )
    await request.app.state.ws_manager.broadcast(
        map_document.training_session.session_code,
        {"type": "snapshot_created", "map_id": map_document.id, "snapshot_id": snapshot.id, "label": snapshot.label, "version": snapshot.version},
    )
    return {"id": snapshot.id, "label": snapshot.label, "version": snapshot.version, "created_at": snapshot.created_at.isoformat()}


@router.get("/maps/{map_id}/snapshots")
def get_map_snapshots(map_id: str, request: Request, db: DbSession) -> dict[str, object]:
    auth_session = _require_auth(request, db)
    map_document = get_session_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Map not found.")
    if map_document.session_id != auth_session.training_session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    return {"items": list_snapshots(map_document)}


@router.get("/maps/{map_id}/export")
def export_map(map_id: str, request: Request, db: DbSession) -> JSONResponse:
    auth_session = _require_auth(request, db)
    map_document = get_session_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Map not found.")
    if map_document.session_id != auth_session.training_session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    payload = export_map_document(map_document)
    return JSONResponse(payload, headers={"Content-Disposition": f'attachment; filename="map-{map_document.id}.json"'})


@router.post("/maps/import")
async def import_map(
    payload: ImportMapRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session = _require_auth(request, db)
    await validate_csrf(request, auth_session, settings)
    training_session = get_training_session(db, auth_session.training_session_id)
    if training_session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training session not found.")
    map_document = import_map_document(
        db,
        training_session=training_session,
        auth_session=auth_session,
        payload=payload,
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True, "map_id": map_document.id}


@router.post("/maps/{map_id}/object-maps")
async def create_object_map(
    map_id: str,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    payload: Annotated[ObjectMapCreateRequest | None, Body()] = None,
) -> dict[str, object]:
    auth_session = _require_auth(request, db)
    await validate_csrf(request, auth_session, settings)
    parent_map = get_session_map(db, map_id)
    if parent_map is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Map not found.")
    if parent_map.session_id != auth_session.training_session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    object_map = create_object_map_from_existing(
        db,
        parent_map=parent_map,
        auth_session=auth_session,
        settings=settings,
        ip_address=request.client.host if request.client else None,
        source_level_id=payload.source_level_id if payload else None,
        source_index=payload.source_index if payload else None,
    )
    return {"ok": True, "map_id": object_map.id}


@router.post("/maps/{map_id}/layers/reorder")
async def reorder_layer(
    map_id: str,
    payload: LayerReorderRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    auth_session = _require_auth(request, db)
    await validate_csrf(request, auth_session, settings)
    map_document = get_session_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Map not found.")
    if map_document.session_id != auth_session.training_session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    result = reorder_map_layer(
        db,
        map_document=map_document,
        auth_session=auth_session,
        level_id=payload.level_id,
        layer_key=payload.layer_key,
        direction=payload.direction,
        ip_address=request.client.host if request.client else None,
    )
    await request.app.state.ws_manager.broadcast(
        result["session_code"],
        {
            "type": "layer_order_updated",
            "map_id": map_document.id,
            "version": result["version"],
            "level_id": result["level_id"],
            "layer_order": result["layer_order"],
        },
    )
    return {"ok": True, **result}


@router.post("/maps/{map_id}/imports/image")
async def queue_image_import(
    map_id: str,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    image: UploadFile | None = File(default=None),
) -> dict[str, object]:
    auth_session = _require_auth(request, db)
    await validate_csrf(request, auth_session, settings)
    map_document = get_session_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Map not found.")
    if map_document.session_id != auth_session.training_session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    if image is not None and image.content_type not in {"image/png", "image/jpeg"}:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Only PNG and JPEG are allowed.")
    job = create_import_job(
        db,
        map_document=map_document,
        auth_session=auth_session,
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True, "job_id": job.id, "status": job.status, "message": "Image import queued for stage 1.1."}
