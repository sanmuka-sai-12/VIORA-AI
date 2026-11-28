"""Persistent student Study Notes & Academic Insights per Personal Learning session."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_pl_study_resources"
down_revision = "0014_personal_learning_history"
branch_labels = None
depends_on = None

STUDENT_PRIVATE_PREDICATE = (
    "tenant_id = NULLIF(current_setting('viora.tenant_id', true), '')::uuid "
    "AND owner_user_id = NULLIF(current_setting('viora.actor_id', true), '')::uuid"
)


def upgrade() -> None:
    op.create_table(
        "personal_learning_study_resources",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("tool_kind", sa.Text(), nullable=False),
        sa.Column("source_scope", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("focus_topic", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "sources",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("usage_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("truncated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "tool_kind IN ('study_notes', 'academic_insights')",
            name="ck_pl_resources_tool_kind",
        ),
        sa.CheckConstraint(
            "source_scope IN ('topic', 'conversation', 'materials', 'combined')",
            name="ck_pl_resources_scope",
        ),
        sa.CheckConstraint("usage_tokens >= 0", name="ck_pl_resources_tokens"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.user_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            [
                "personal_learning_sessions.tenant_id",
                "personal_learning_sessions.session_id",
            ],
            name="fk_pl_resources_session",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "resource_id"),
    )
    op.create_index(
        "ix_pl_resources_session_recent",
        "personal_learning_study_resources",
        ["tenant_id", "owner_user_id", "session_id", "updated_at"],
    )
    op.execute(
        "ALTER TABLE personal_learning_study_resources ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE personal_learning_study_resources FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        f"CREATE POLICY student_private_isolation ON personal_learning_study_resources "
        f"USING ({STUDENT_PRIVATE_PREDICATE}) WITH CHECK ({STUDENT_PRIVATE_PREDICATE})"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY student_private_isolation ON personal_learning_study_resources"
    )
    op.execute(
        "ALTER TABLE personal_learning_study_resources NO FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE personal_learning_study_resources DISABLE ROW LEVEL SECURITY"
    )
    op.drop_index(
        "ix_pl_resources_session_recent",
        table_name="personal_learning_study_resources",
    )
    op.drop_table("personal_learning_study_resources")
