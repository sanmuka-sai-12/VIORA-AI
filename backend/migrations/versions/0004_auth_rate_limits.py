"""Add PostgreSQL-backed rate limiting table for authentication endpoints."""

import sqlalchemy as sa
from alembic import op

revision = "0004_auth_rate_limits"
down_revision = "0003_auth_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_rate_limits",
        sa.Column("scope", sa.Text(), primary_key=True),
        sa.Column("bucket_key", sa.Text(), primary_key=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("request_count >= 1", name="ck_auth_rate_limit_count"),
    )
    op.create_index(
        "ix_auth_rate_limits_window_started_at",
        "auth_rate_limits",
        ["window_started_at"],
    )


def downgrade() -> None:
    op.drop_table("auth_rate_limits")
