from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class Source(Base):
    __tablename__ = "sources"
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "institutions.tenant_id", name="fk_sources_institution", ondelete="RESTRICT"
        ),
        primary_key=True,
    )
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    title: Mapped[str]
