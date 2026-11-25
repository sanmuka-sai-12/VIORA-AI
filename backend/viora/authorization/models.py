from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class SecurityEvent(Base):
    """Append-only tenant audit; no FK so denied nonexistent-tenant probes can be recorded."""

    __tablename__ = "security_events"
    __table_args__ = (
        Index("ix_security_events_tenant_time", "tenant_id", "occurred_at"),
    )
    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    actor_id: Mapped[UUID]
    resource_id: Mapped[UUID]
    resource_kind: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    event_kind: Mapped[str] = mapped_column(Text)
    allowed: Mapped[bool]
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
