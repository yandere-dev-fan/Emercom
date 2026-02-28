import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw is not None else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw is not None else default


def _env_samesite(name: str, default: Literal["lax", "strict", "none"]) -> Literal["lax", "strict", "none"]:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized not in {"lax", "strict", "none"}:
        return default
    return normalized


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_host: str
    app_port: int
    secret_key: str
    database_url: str
    redis_url: str
    session_cookie_name: str
    csrf_cookie_name: str
    host_admin_cookie_name: str
    host_admin_csrf_cookie_name: str
    cookie_secure: bool
    cookie_samesite: Literal["lax", "strict", "none"]
    cookie_max_age_seconds: int
    host_admin_secret: str
    host_admin_cookie_max_age_seconds: int
    rate_limit_window_seconds: int
    rate_limit_soft_limit: int
    rate_limit_hard_limit: int
    rate_limit_block_seconds: int
    failed_login_delay_seconds: float
    map_max_width: int
    map_max_height: int
    map_max_levels: int
    ws_ping_interval_seconds: int
    session_max_participants: int


@lru_cache
def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "TP Simulator"),
        app_host=os.getenv("APP_HOST", "0.0.0.0"),
        app_port=_env_int("APP_PORT", 8000),
        secret_key=os.getenv("SECRET_KEY", "change-me-in-production"),
        database_url=os.getenv("DATABASE_URL", "sqlite+pysqlite:///:memory:"),
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        session_cookie_name=os.getenv("SESSION_COOKIE_NAME", "trainer_auth"),
        csrf_cookie_name=os.getenv("CSRF_COOKIE_NAME", "trainer_csrf"),
        host_admin_cookie_name=os.getenv("HOST_ADMIN_COOKIE_NAME", "host_admin_auth"),
        host_admin_csrf_cookie_name=os.getenv("HOST_ADMIN_CSRF_COOKIE_NAME", "host_admin_csrf"),
        cookie_secure=_env_bool("COOKIE_SECURE", True),
        cookie_samesite=_env_samesite("COOKIE_SAMESITE", "lax"),
        cookie_max_age_seconds=_env_int("COOKIE_MAX_AGE_SECONDS", 8 * 60 * 60),
        host_admin_secret=os.getenv("HOST_ADMIN_SECRET", "host-admin-change-me"),
        host_admin_cookie_max_age_seconds=_env_int("HOST_ADMIN_COOKIE_MAX_AGE_SECONDS", 12 * 60 * 60),
        rate_limit_window_seconds=_env_int("RATE_LIMIT_WINDOW_SECONDS", 15 * 60),
        rate_limit_soft_limit=_env_int("RATE_LIMIT_SOFT_LIMIT", 5),
        rate_limit_hard_limit=_env_int("RATE_LIMIT_HARD_LIMIT", 10),
        rate_limit_block_seconds=_env_int("RATE_LIMIT_BLOCK_SECONDS", 30 * 60),
        failed_login_delay_seconds=_env_float("FAILED_LOGIN_DELAY_SECONDS", 1.0),
        map_max_width=_env_int("MAP_MAX_WIDTH", 256),
        map_max_height=_env_int("MAP_MAX_HEIGHT", 256),
        map_max_levels=_env_int("MAP_MAX_LEVELS", 8),
        ws_ping_interval_seconds=_env_int("WS_PING_INTERVAL_SECONDS", 25),
        session_max_participants=_env_int("SESSION_MAX_PARTICIPANTS", 4),
    )
