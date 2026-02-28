from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.schemas import CreateSessionRequest
from app.config import Settings, get_settings
from app.db.models import AuthSession
from app.db.session import get_db
from app.domain.session_flow_v2 import (
    create_training_session_from_templates,
    get_current_auth_session,
    get_session_state_payload,
    get_session_with_related,
    join_training_session,
)
from app.domain.services import get_session_map
from app.domain.template_maps_v2 import list_template_maps
from app.domain.vehicle_catalog_v2 import VEHICLE_CATALOG
from app.security.auth import clear_auth_cookies, set_auth_cookies
from app.security.csrf import validate_csrf
from app.web.qr import build_qr_data_uri, join_link_with_key


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def _current_auth_session(request: Request, db: Session, settings: Settings) -> AuthSession | None:
    raw_cookie = request.cookies.get(settings.session_cookie_name)
    return get_current_auth_session(db, raw_cookie)


@router.get("/", response_class=HTMLResponse, name="landing")
def landing(request: Request, db: DbSession, settings: AppSettings) -> HTMLResponse:
    auth_session = _current_auth_session(request, db, settings)
    return templates.TemplateResponse(
        request,
        "landing_v3.html",
        {"auth_session": auth_session, "host_session": None},
    )

@router.get("/sessions/new", response_class=HTMLResponse, name="new_session_page")
def new_session_page(request: Request, db: DbSession, settings: AppSettings) -> HTMLResponse:
    auth_session = _current_auth_session(request, db, settings)
    template_maps = list_template_maps(db)
    area_templates = [item for item in template_maps if item.kind == "area"]
    object_templates = [item for item in template_maps if item.kind == "object"]
    return templates.TemplateResponse(
        request,
        "new_session_v3.html",
        {
            "auth_session": auth_session,
            "host_session": None,
            "area_templates": area_templates,
            "object_templates": object_templates,
            "vehicle_catalog": VEHICLE_CATALOG,
            "error": None,
        },
    )


@router.post("/sessions", response_class=HTMLResponse)
def create_session(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    area_template_id: str = Form(...),
    object_template_id: str = Form(...),
    weather_kind: str = Form(default="clear"),
    wind_direction_deg: int = Form(default=0),
    wind_speed_level: int = Form(default=1),
    time_of_day: str = Form(default="day"),
    detection_radius_cells: int = Form(default=2),
    incident_area_index: int = Form(default=0),
    incident_object_level_code: str = Form(default="F1"),
    incident_object_index: int = Form(default=0),
    enabled_vehicle_types: list[str] = Form(...),
) -> Response:
    join_page_url = str(request.url_for("join_session_page"))
    try:
        payload = CreateSessionRequest(
            area_template_id=area_template_id,
            object_template_id=object_template_id,
            weather_kind=weather_kind,
            wind_direction_deg=wind_direction_deg,
            wind_speed_level=wind_speed_level,
            time_of_day=time_of_day,
            detection_radius_cells=detection_radius_cells,
            incident_area_index=incident_area_index,
            incident_object_level_code=incident_object_level_code,
            incident_object_index=incident_object_index,
            enabled_vehicle_types=enabled_vehicle_types,
        )
    except ValidationError as exc:
        template_maps = list_template_maps(db)
        first_error = exc.errors()[0]["msg"] if exc.errors() else "Invalid session payload."
        return templates.TemplateResponse(
            request,
            "new_session_v3.html",
            {
                "auth_session": _current_auth_session(request, db, settings),
                "host_session": None,
                "area_templates": [item for item in template_maps if item.kind == "area"],
                "object_templates": [item for item in template_maps if item.kind == "object"],
                "vehicle_catalog": VEHICLE_CATALOG,
                "error": first_error,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    try:
        training_session, auth_session, raw_token, join_key, scenario_state = create_training_session_from_templates(
            db,
            payload=payload,
            settings=settings,
            ip_address=request.client.host if request.client else None,
        )
    except HTTPException as exc:
        db.rollback()
        template_maps = list_template_maps(db)
        return templates.TemplateResponse(
            request,
            "new_session_v3.html",
            {
                "auth_session": _current_auth_session(request, db, settings),
                "host_session": None,
                "area_templates": [item for item in template_maps if item.kind == "area"],
                "object_templates": [item for item in template_maps if item.kind == "object"],
                "vehicle_catalog": VEHICLE_CATALOG,
                "error": exc.detail,
            },
            status_code=exc.status_code,
        )
    join_link = join_link_with_key(join_page_url, join_key)
    response = templates.TemplateResponse(
        request,
        "session_created_v3.html",
        {
            "auth_session": auth_session,
            "host_session": None,
            "session": training_session,
            "join_key": join_key,
            "join_link": join_link,
            "join_qr_data_uri": build_qr_data_uri(join_link),
            "session_url": str(request.url_for("session_page", session_code=training_session.session_code)),
            "scenario_state": scenario_state,
            "current_session_code": training_session.session_code,
        },
    )
    set_auth_cookies(response, auth_session, raw_token, settings)
    return response


@router.get("/sessions/join", response_class=HTMLResponse, name="join_session_page")
def join_session_page(request: Request, db: DbSession, settings: AppSettings, key: str | None = None) -> HTMLResponse:
    auth_session = _current_auth_session(request, db, settings)
    return templates.TemplateResponse(
        request,
        "join_session.html",
        {"auth_session": auth_session, "host_session": None, "error": None, "join_key_prefill": key or ""},
    )


@router.post("/sessions/join", response_class=HTMLResponse)
async def join_session_submit(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    join_key: str = Form(...),
) -> Response:
    try:
        training_session, auth_session, raw_token = await join_training_session(
            db,
            join_key=join_key.strip(),
            ip_address=request.client.host if request.client else None,
            limiter=request.app.state.join_key_limiter,
            settings=settings,
        )
    except HTTPException as exc:
        return templates.TemplateResponse(
            request,
            "join_session.html",
            {
                "auth_session": None,
                "host_session": None,
                "error": exc.detail,
                "join_key_prefill": join_key,
            },
            status_code=exc.status_code,
        )

    response = RedirectResponse(
        url=request.url_for("session_page", session_code=training_session.session_code),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_auth_cookies(response, auth_session, raw_token, settings)
    return response


@router.get("/sessions/{session_code}", response_class=HTMLResponse, name="session_page")
def session_page(
    request: Request,
    session_code: str,
    db: DbSession,
    settings: AppSettings,
) -> Response:
    auth_session = _current_auth_session(request, db, settings)
    if auth_session is None:
        return RedirectResponse(url=request.url_for("join_session_page"), status_code=status.HTTP_303_SEE_OTHER)
    training_session = get_session_with_related(db, session_code)
    if training_session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сессия не найдена.")
    if auth_session.training_session_id != training_session.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Эта сессия относится к другой комнате.")
    state_payload = get_session_state_payload(training_session, viewer_role=auth_session.role)
    template_name = "session_lobby_v2.html" if training_session.scenario_state is None or training_session.scenario_state.status == "setup" else "session_runtime_v2.html"
    return templates.TemplateResponse(
        request,
        template_name,
        {
            "auth_session": auth_session,
            "host_session": None,
            "session": training_session,
            "state_payload": state_payload,
            "current_session_code": training_session.session_code,
        },
    )


@router.post("/sessions/{session_code}/leave", name="leave_session")
async def leave_session(
    request: Request,
    session_code: str,
    db: DbSession,
    settings: AppSettings,
) -> RedirectResponse:
    auth_session = _current_auth_session(request, db, settings)
    if auth_session is not None:
        await validate_csrf(request, auth_session, settings)
    response = RedirectResponse(url=request.url_for("landing"), status_code=status.HTTP_303_SEE_OTHER)
    clear_auth_cookies(response, settings)
    return response


@router.get("/sessions/{session_code}/maps/new")
def legacy_new_map_page(request: Request) -> RedirectResponse:
    return RedirectResponse(url=request.url_for("new_template_page"), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/sessions/{session_code}/maps")
def legacy_create_map_submit(request: Request) -> RedirectResponse:
    return RedirectResponse(url=request.url_for("new_template_page"), status_code=status.HTTP_303_SEE_OTHER)


@router.get("/maps/{map_id}", response_class=HTMLResponse, name="map_editor_page")
def map_editor_page(
    request: Request,
    map_id: str,
    db: DbSession,
    settings: AppSettings,
) -> Response:
    auth_session = _current_auth_session(request, db, settings)
    if auth_session is None:
        return RedirectResponse(url=request.url_for("join_session_page"), status_code=status.HTTP_303_SEE_OTHER)
    map_document = get_session_map(db, map_id)
    if map_document is not None and map_document.kind == "object":
        scenario_state = map_document.training_session.scenario_state if map_document.training_session is not None else None
        if auth_session.role == "dispatcher":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Диспетчер не видит карту объекта.")
        if auth_session.role in {"rtp", "observer"} and scenario_state is not None and not scenario_state.incident_revealed:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Фаза объекта ещё не открыта.")
    if map_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карта не найдена.")
    if map_document.scope != "session":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Для шаблонных карт используйте редактор библиотеки карт.")
    if map_document.session_id != auth_session.training_session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Карта относится к другой сессии.")
    return templates.TemplateResponse(
        request,
        "editor_v2.html",
        {
            "auth_session": auth_session,
            "host_session": None,
            "map_document": map_document,
            "session_code": map_document.training_session.session_code,
            "current_session_code": map_document.training_session.session_code,
        },
    )
