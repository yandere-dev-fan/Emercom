from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.schemas import MapCreateRequest
from app.db.session import get_db
from app.domain.template_maps_v2 import create_template_map, get_template_map, list_template_maps


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/host/login", response_class=HTMLResponse, name="host_login_page")
def host_login_page(request: Request) -> RedirectResponse:
    return RedirectResponse(url=request.url_for("template_library_page"), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/host/login", response_class=HTMLResponse)
def host_login_submit(request: Request) -> RedirectResponse:
    return RedirectResponse(url=request.url_for("template_library_page"), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/host/logout", name="host_logout")
async def host_logout(request: Request) -> RedirectResponse:
    return RedirectResponse(url=request.url_for("landing"), status_code=status.HTTP_303_SEE_OTHER)


@router.get("/templates", response_class=HTMLResponse, name="template_library_page")
def template_library_page(
    request: Request,
    db: DbSession,
) -> HTMLResponse:
    template_maps = list_template_maps(db)
    return templates.TemplateResponse(
        request,
        "template_library_v2.html",
        {
            "auth_session": None,
            "host_session": None,
            "template_maps": template_maps,
            "area_templates": [item for item in template_maps if item.kind == "area"],
            "object_templates": [item for item in template_maps if item.kind == "object"],
        },
    )


@router.get("/templates/new", response_class=HTMLResponse, name="new_template_page")
def new_template_page(
    request: Request,
    db: DbSession,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "new_template_v2.html",
        {"auth_session": None, "host_session": None},
    )


@router.post("/templates")
async def create_template_submit(
    request: Request,
    db: DbSession,
    title: str = Form(...),
    kind: str = Form(...),
    width: int = Form(...),
    height: int = Form(...),
    cell_size_px: int = Form(...),
    meters_per_cell: int = Form(...),
    map_type: str = Form(...),
) -> Response:
    try:
        payload = MapCreateRequest(
            title=title,
            kind=kind,
            width=width,
            height=height,
            cell_size_px=cell_size_px,
            meters_per_cell=meters_per_cell,
            map_type=map_type,
            parent_map_id=None,
        )
    except ValidationError as exc:
        first_error = exc.errors()[0]["msg"] if exc.errors() else "Invalid template payload."
        return templates.TemplateResponse(
            request,
            "new_template_v2.html",
            {"auth_session": None, "host_session": None, "error": first_error},
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    map_document = create_template_map(db, payload=payload)
    return RedirectResponse(url=request.url_for("template_editor_page", map_id=map_document.id), status_code=status.HTTP_303_SEE_OTHER)


@router.get("/templates/{map_id}/edit", response_class=HTMLResponse, name="template_editor_page")
def template_editor_page(
    request: Request,
    map_id: str,
    db: DbSession,
) -> Response:
    map_document = get_template_map(db, map_id)
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карта не найдена.")
    return templates.TemplateResponse(
        request,
        "template_editor_v2.html",
        {"auth_session": None, "host_session": None, "map_document": map_document},
    )
