from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.config import Settings
from app.db.models import AuthSession, HostAdminSession


async def validate_csrf(request: Request, auth_session: AuthSession, settings: Settings) -> None:
    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    header_token = request.headers.get("X-CSRF-Token")
    form_token = None
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        form_token = form.get("csrf_token")

    request_token = header_token or form_token
    if not cookie_token or not request_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token is required.")
    if cookie_token != request_token or request_token != auth_session.csrf_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token mismatch.")


async def validate_host_admin_csrf(request: Request, host_session: HostAdminSession, settings: Settings) -> None:
    cookie_token = request.cookies.get(settings.host_admin_csrf_cookie_name)
    header_token = request.headers.get("X-CSRF-Token")
    form_token = None
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        form_token = form.get("csrf_token")

    request_token = header_token or form_token
    if not cookie_token or not request_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token is required.")
    if cookie_token != request_token or request_token != host_session.csrf_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token mismatch.")
