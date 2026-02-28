from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.config import Settings


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RateBucket:
    failures: deque[datetime] = field(default_factory=deque)
    blocked_until: datetime | None = None


class InMemoryJoinKeyLimiter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._buckets: dict[str, RateBucket] = {}

    def _bucket(self, client_id: str) -> RateBucket:
        bucket = self._buckets.get(client_id)
        if bucket is None:
            bucket = RateBucket()
            self._buckets[client_id] = bucket
        return bucket

    def _prune(self, bucket: RateBucket, now: datetime) -> None:
        window_start = now - timedelta(seconds=self.settings.rate_limit_window_seconds)
        while bucket.failures and bucket.failures[0] < window_start:
            bucket.failures.popleft()
        if bucket.blocked_until and bucket.blocked_until <= now:
            bucket.blocked_until = None

    def is_blocked(self, client_id: str) -> bool:
        now = utc_now()
        bucket = self._bucket(client_id)
        self._prune(bucket, now)
        return bucket.blocked_until is not None

    def register_failure(self, client_id: str) -> int:
        now = utc_now()
        bucket = self._bucket(client_id)
        self._prune(bucket, now)
        bucket.failures.append(now)
        if len(bucket.failures) >= self.settings.rate_limit_hard_limit:
            bucket.blocked_until = now + timedelta(seconds=self.settings.rate_limit_block_seconds)
        return len(bucket.failures)

    def register_success(self, client_id: str) -> None:
        if client_id in self._buckets:
            del self._buckets[client_id]

    def soft_limit_reached(self, client_id: str) -> bool:
        now = utc_now()
        bucket = self._bucket(client_id)
        self._prune(bucket, now)
        return len(bucket.failures) >= self.settings.rate_limit_soft_limit
