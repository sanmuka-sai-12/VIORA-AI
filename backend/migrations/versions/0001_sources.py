"""Tenant-scoped source metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0001_sources"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "source_id"),
    )


def downgrade() -> None:
    op.drop_table("sources")
