"""Add active lifecycle flag and scoped uniqueness indexes to academic hierarchy tables."""

import sqlalchemy as sa
from alembic import op

revision = "0006_academic_lifecycle"
down_revision = "0005_tenant_security"
branch_labels = None
depends_on = None

ACADEMIC_TABLES = (
    "campuses",
    "departments",
    "programmes",
    "academic_years",
    "semesters",
    "sections",
    "subjects",
    "subject_offerings",
)

SENTINEL = "'00000000-0000-0000-0000-000000000000'::uuid"


def upgrade() -> None:
    for table in ACADEMIC_TABLES:
        op.add_column(
            table,
            sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        )
    op.create_index(
        "uq_campuses_tenant_name",
        "campuses",
        ["tenant_id", sa.text("lower(btrim(name))")],
        unique=True,
    )
    op.create_index(
        "uq_departments_scope_name",
        "departments",
        [
            "tenant_id",
            sa.text(f"coalesce(campus_id, {SENTINEL})"),
            sa.text("lower(btrim(name))"),
        ],
        unique=True,
    )
    op.create_index(
        "uq_programmes_scope_name",
        "programmes",
        [
            "tenant_id",
            sa.text(f"coalesce(department_id, {SENTINEL})"),
            sa.text("lower(btrim(name))"),
        ],
        unique=True,
    )
    op.create_index(
        "uq_academic_years_tenant_name",
        "academic_years",
        ["tenant_id", sa.text("lower(btrim(name))")],
        unique=True,
    )
    op.create_index(
        "uq_semesters_year_name",
        "semesters",
        ["tenant_id", "academic_year_id", sa.text("lower(btrim(name))")],
        unique=True,
    )
    op.create_index(
        "uq_sections_scope_name",
        "sections",
        [
            "tenant_id",
            sa.text(f"coalesce(programme_id, {SENTINEL})"),
            "semester_id",
            sa.text("lower(btrim(name))"),
        ],
        unique=True,
    )
    op.create_index(
        "uq_subjects_scope_name",
        "subjects",
        [
            "tenant_id",
            sa.text(f"coalesce(department_id, {SENTINEL})"),
            sa.text("lower(btrim(name))"),
        ],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_subjects_scope_name", table_name="subjects")
    op.drop_index("uq_sections_scope_name", table_name="sections")
    op.drop_index("uq_semesters_year_name", table_name="semesters")
    op.drop_index("uq_academic_years_tenant_name", table_name="academic_years")
    op.drop_index("uq_programmes_scope_name", table_name="programmes")
    op.drop_index("uq_departments_scope_name", table_name="departments")
    op.drop_index("uq_campuses_tenant_name", table_name="campuses")
    for table in reversed(ACADEMIC_TABLES):
        op.drop_column(table, "active")
