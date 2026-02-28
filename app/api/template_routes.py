from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.schemas import ImportMapRequest, MapCreateRequest, MapMetadataUpdateRequest, MapPatchRequest, SnapshotCreateRequest, ObjectMapCreateRequest
from app.db.session import get_db
from app.domain.session_maps_v2 import serialize_map_for_role
from app.domain.services import list_snapshots
from app.domain.template_maps_v2 import (
    add_template_level,
    apply_template_patch,
    create_template_map,
    create_template_snapshot,
    export_template_map,
    get_template_map,
    import_template_map,
    list_template_maps,
    update_template_map_metadata,
    create_template_object_map_from_existing,
)


router = APIRouter(prefix="/api/templates", tags=["templates"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("")
def get_templates(db: DbSession) -> dict[str, object]:
    template_maps = list_template_maps(db)
    return {
        "items": [
            {
                "id": item.id,
                "title": item.title,
                "kind": item.kind,
                "width": item.width,
                "height": item.height,
                "version": item.version,
            }
            for item in template_maps
        ]
    }


@router.post("")
async def post_template(
    payload: MapCreateRequest,
    db: DbSession,
) -> dict[str, object]:
    map_document = create_template_map(db, payload=payload)
    return {"ok": True, "map_id": map_document.id}


@router.get("/{map_id}")
def get_template(map_id: str, db: DbSession) -> dict[str, object]:
    map_document = get_template_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карта не найдена.")
    return serialize_map_for_role(map_document, "template_editor")


@router.post("/{map_id}/levels")
async def post_template_level(
    map_id: str,
    db: DbSession,
) -> dict[str, object]:
    map_document = get_template_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карта не найдена.")
    level = add_template_level(db, map_document=map_document)
    return {
        "ok": True,
        "level": {
            "id": level.id,
            "code": level.code,
            "title": level.title,
            "floor_number": level.floor_number,
            "sort_order": level.sort_order,
        },
    }


@router.put("/{map_id}/metadata")
async def put_template_metadata(
    map_id: str,
    payload: MapMetadataUpdateRequest,
    db: DbSession,
) -> dict[str, object]:
    map_document = get_template_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карта не найдена.")
    updated_map = update_template_map_metadata(db, map_document=map_document, payload=payload)
    return {"ok": True, "version": updated_map.version, "title": updated_map.title}


@router.post("/{map_id}/patches")
async def post_template_patch(
    map_id: str,
    payload: MapPatchRequest,
    db: DbSession,
) -> dict[str, object]:
    map_document = get_template_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карта не найдена.")
    result = apply_template_patch(db, map_document=map_document, payload=payload)
    return {"ok": True, **result}


@router.post("/{map_id}/snapshots")
async def post_template_snapshot(
    map_id: str,
    payload: SnapshotCreateRequest,
    db: DbSession,
) -> dict[str, object]:
    map_document = get_template_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карта не найдена.")
    snapshot = create_template_snapshot(db, map_document=map_document, label=payload.label)
    return {"id": snapshot.id, "label": snapshot.label, "version": snapshot.version, "created_at": snapshot.created_at.isoformat()}


@router.get("/{map_id}/snapshots")
def get_template_snapshots(map_id: str, db: DbSession) -> dict[str, object]:
    map_document = get_template_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карта не найдена.")
    return {"items": list_snapshots(map_document)}


@router.get("/{map_id}/export")
def get_template_export(map_id: str, db: DbSession) -> JSONResponse:
    map_document = get_template_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карта не найдена.")
    payload = export_template_map(map_document)
    return JSONResponse(payload, headers={"Content-Disposition": f'attachment; filename="template-{map_document.id}.json"'})


@router.post("/import")
async def post_template_import(
    payload: ImportMapRequest,
    db: DbSession,
) -> dict[str, object]:
    map_document = import_template_map(db, payload=payload)
    return {"ok": True, "map_id": map_document.id}


@router.post("/{map_id}/object-maps")
async def post_template_object_template(
    map_id: str,
    db: DbSession,
    payload: Annotated[ObjectMapCreateRequest | None, Body()] = None,
) -> dict[str, object]:
    map_document = get_template_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карта не найдена.")
    object_map = create_template_object_map_from_existing(
        db,
        parent_map=map_document,
        source_level_id=payload.source_level_id if payload else None,
        source_index=payload.source_index if payload else None,
    )
    return {"ok": True, "map_id": object_map.id}
