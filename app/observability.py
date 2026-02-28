from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

from fastapi import FastAPI, Request


REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")
APP_LOGGER_NAME = "tp_simulator"


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event_name = getattr(record, "event_name", None)
        if event_name:
            payload["event"] = event_name
        payload["request_id"] = getattr(record, "request_id", REQUEST_ID.get())
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def get_logger() -> logging.Logger:
    return logging.getLogger(APP_LOGGER_NAME)


def configure_logging() -> logging.Logger:
    logger = get_logger()
    if getattr(logger, "_tp_configured", False):
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    setattr(logger, "_tp_configured", True)
    return logger


def log_event(event_name: str, /, level: int = logging.INFO, **fields: object) -> None:
    logger = configure_logging()
    logger.log(
        level,
        event_name,
        extra={"event_name": event_name, "fields": fields, "request_id": REQUEST_ID.get()},
    )


def install_observability(app: FastAPI) -> None:
    configure_logging()

    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = REQUEST_ID.set(request_id)
        started_at = time.perf_counter()
        client_ip = request.client.host if request.client else None
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            log_event(
                "request_failed",
                level=logging.ERROR,
                method=request.method,
                path=request.url.path,
                query=str(request.url.query),
                client_ip=client_ip,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
            )
            raise
        else:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            response.headers["X-Request-ID"] = request_id
            log_event(
                "request_completed",
                method=request.method,
                path=request.url.path,
                query=str(request.url.query),
                client_ip=client_ip,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
            return response
        finally:
            REQUEST_ID.reset(token)
