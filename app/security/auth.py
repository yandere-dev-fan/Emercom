from __future__ import annotations

import secrets
import hmac
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Response

from app.config import Settings
from app.db.models import AuthSession, HostAdminSession


password_hasher = PasswordHasher()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generate_join_key() -> str:
    return secrets.token_urlsafe(32)


def generate_random_token() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(value: str) -> str:
    return password_hasher.hash(value)


def verify_secret(hashed_value: str, plain_value: str) -> bool:
    try:
        if not hashed_value:
            return False
        return password_hasher.verify(hashed_value, plain_value)
    except Exception:
        return False


def build_auth_cookie_value(auth_session_id: str, raw_token: str) -> str:
    return f"{auth_session_id}.{raw_token}"


def parse_auth_cookie_value(raw_value: str | None) -> tuple[str, str] | None:
    if not raw_value or "." not in raw_value:
        return None
    auth_session_id, token = raw_value.split(".", 1)
    if not auth_session_id or not token:
        return None
    return auth_session_id, token


def make_expiry(hours: int = 8) -> datetime:
    return utc_now() + timedelta(hours=hours)


def set_auth_cookies(
    response: Response,
    auth_session: AuthSession,
    raw_token: str,
    settings: Settings,
) -> None:
    cookie_value = build_auth_cookie_value(auth_session.id, raw_token)
    response.set_cookie(
        settings.session_cookie_name,
        cookie_value,
        max_age=settings.cookie_max_age_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        auth_session.csrf_token,
        max_age=settings.cookie_max_age_seconds,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


def verify_host_admin_secret(settings: Settings, candidate: str) -> bool:
    return hmac.compare_digest(settings.host_admin_secret, candidate)


def set_host_admin_cookies(
    response: Response,
    host_session: HostAdminSession,
    raw_token: str,
    settings: Settings,
) -> None:
    cookie_value = build_auth_cookie_value(host_session.id, raw_token)
    response.set_cookie(
        settings.host_admin_cookie_name,
        cookie_value,
        max_age=settings.host_admin_cookie_max_age_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    response.set_cookie(
        settings.host_admin_csrf_cookie_name,
        host_session.csrf_token,
        max_age=settings.host_admin_cookie_max_age_seconds,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )


def clear_host_admin_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.host_admin_cookie_name, path="/")
    response.delete_cookie(settings.host_admin_csrf_cookie_name, path="/")
