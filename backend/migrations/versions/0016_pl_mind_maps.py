"""Add interactive mind_map support and structured_data column to study resources."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_pl_mind_maps"
down_revision = "0015_pl_study_resources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "personal_learning_study_resources",
        sa.Column(
            "structured_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.drop_constraint(
        "ck_pl_resources_tool_kind",
        "personal_learning_study_resources",
        type_="check",
    )
    op.create_check_constraint(
        "ck_pl_resources_tool_kind",
        "personal_learning_study_resources",
        "tool_kind IN ('study_notes', 'academic_insights', 'mind_map')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_pl_resources_tool_kind",
        "personal_learning_study_resources",
        type_="check",
    )
    op.create_check_constraint(
        "ck_pl_resources_tool_kind",
        "personal_learning_study_resources",
        "tool_kind IN ('study_notes', 'academic_insights')",
    )
    op.drop_column("personal_learning_study_resources", "structured_data")
