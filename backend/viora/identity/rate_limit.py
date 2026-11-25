"""PostgreSQL-backed rate limiter for authentication endpoints across workers."""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import case
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from .models import AuthRateLimit


class RateLimiter(Protocol):
    def check(
        self, scope: str, key: str, limit: int, window_seconds: int
    ) -> int | None:
        """Record an attempt and return Retry-After seconds if rate-limited."""
        ...


def client_bucket_key(client_host: str | None) -> str:
    """Hash client network address so raw IPs are not persisted."""
    raw = (client_host or "unknown").strip() or "unknown"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PostgresRateLimiter:
    """Atomic, multi-worker rate-limit counter backed by PostgreSQL."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def check(
        self, scope: str, key: str, limit: int, window_seconds: int
    ) -> int | None:
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=window_seconds)
        table = AuthRateLimit.__table__
        expired = table.c.window_started_at <= cutoff
        stmt = (
            insert(AuthRateLimit)
            .values(
                scope=scope,
                bucket_key=key,
                window_started_at=now,
                request_count=1,
            )
            .on_conflict_do_update(
                index_elements=[AuthRateLimit.scope, AuthRateLimit.bucket_key],
                set_={
                    "request_count": case(
                        (expired, 1),
                        else_=table.c.request_count + 1,
                    ),
                    "window_started_at": case(
                        (expired, now),
                        else_=table.c.window_started_at,
                    ),
                },
            )
            .returning(AuthRateLimit.request_count, AuthRateLimit.window_started_at)
        )
        with self.sessions.begin() as db:
            count, window_started_at = db.execute(stmt).one()
        if count > limit:
            remaining = (
                window_started_at + timedelta(seconds=window_seconds) - now
            ).total_seconds()
            return max(1, int(remaining) + (1 if remaining % 1 > 0 else 0))
        return None
