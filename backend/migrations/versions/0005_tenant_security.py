"""Forced tenant RLS and append-only security audit."""

import sqlalchemy as sa
from alembic import op

revision = "0005_tenant_security"
down_revision = "0004_auth_rate_limits"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "institutions",
    "memberships",
    "role_grants",
    "campuses",
    "departments",
    "programmes",
    "academic_years",
    "semesters",
    "sections",
    "subjects",
    "subject_offerings",
    "teacher_assignments",
    "student_enrolments",
    "department_heads",
    "class_coordinators",
    "guardian_links",
    "sources",
)
PREDICATE = "tenant_id = NULLIF(current_setting('viora.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "security_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("resource_kind", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("event_kind", sa.Text(), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_security_events_tenant_time",
        "security_events",
        ["tenant_id", "occurred_at"],
    )
    for table in (*TENANT_TABLES, "security_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for table in TENANT_TABLES:
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
        )
    op.execute(
        f"CREATE POLICY audit_read ON security_events FOR SELECT USING ({PREDICATE})"
    )
    op.execute(
        f"CREATE POLICY audit_append ON security_events FOR INSERT WITH CHECK ({PREDICATE})"
    )


def downgrade() -> None:
    op.drop_table("security_events")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
