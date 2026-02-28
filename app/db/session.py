from collections.abc import Generator
import time

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db.base import Base


settings = get_settings()

connect_args: dict[str, object] = {}
engine_kwargs: dict[str, object] = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False
    if settings.database_url.endswith(":memory:"):
        engine_kwargs["poolclass"] = StaticPool

engine = create_engine(settings.database_url, future=True, connect_args=connect_args, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db(max_attempts: int = 30, delay_seconds: float = 1.0) -> None:
    from app.db import models  # noqa: F401

    last_error: OperationalError | None = None
    for _ in range(max_attempts):
        try:
            Base.metadata.create_all(bind=engine)
            return
        except OperationalError as exc:
            last_error = exc
            time.sleep(delay_seconds)
    if last_error is not None:
        raise last_error


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
