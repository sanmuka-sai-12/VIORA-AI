"""Track origin_source_id on personal_learning_materials to enforce revocation on reuse."""

import sqlalchemy as sa
from alembic import op

revision = "0021_pl_origin_source_id"
down_revision = "0020_classroom_announcements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "personal_learning_materials",
        sa.Column("origin_source_id", sa.Uuid(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("personal_learning_materials", "origin_source_id")
