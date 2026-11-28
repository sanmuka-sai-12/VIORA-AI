"""Add account invitations and tenant-scoped student and faculty academic profiles."""

import sqlalchemy as sa
from alembic import op

revision = "0019_profiles_and_onboarding"
down_revision = "0018_pl_practice_exams"
branch_labels = None
depends_on = None

PREDICATE = "tenant_id = NULLIF(current_setting('viora.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "account_invitations",
        sa.Column("invitation_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "invited_tenant_id",
            sa.Uuid(),
            sa.ForeignKey("institutions.tenant_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_account_invitations_user_id",
        "account_invitations",
        ["user_id"],
    )

    op.create_table(
        "student_profiles",
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("institutions.tenant_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("roll_number", sa.Text(), nullable=True),
        sa.Column("department_id", sa.Uuid(), nullable=True),
        sa.Column("section_id", sa.Uuid(), nullable=True),
        sa.Column("academic_year_id", sa.Uuid(), nullable=True),
        sa.Column("semester_id", sa.Uuid(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("contact_email", sa.Text(), nullable=True),
        sa.Column("guardian_contact", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            ondelete="RESTRICT",
            name="fk_student_profiles_membership",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            ondelete="RESTRICT",
            name="fk_student_profiles_department",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "section_id"],
            ["sections.tenant_id", "sections.id"],
            ondelete="RESTRICT",
            name="fk_student_profiles_section",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "academic_year_id"],
            ["academic_years.tenant_id", "academic_years.id"],
            ondelete="RESTRICT",
            name="fk_student_profiles_academic_year",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "semester_id"],
            ["semesters.tenant_id", "semesters.id"],
            ondelete="RESTRICT",
            name="fk_student_profiles_semester",
        ),
        sa.CheckConstraint(
            "roll_number IS NULL OR btrim(roll_number) <> ''",
            name="ck_student_profiles_roll_number",
        ),
    )
    op.create_index(
        "ix_student_profiles_department_id",
        "student_profiles",
        ["tenant_id", "department_id"],
    )
    op.create_index(
        "ix_student_profiles_section_id",
        "student_profiles",
        ["tenant_id", "section_id"],
    )
    op.create_index(
        "uq_student_profiles_roll_number",
        "student_profiles",
        ["tenant_id", sa.text("lower(btrim(roll_number))")],
        unique=True,
        postgresql_where=sa.text("roll_number IS NOT NULL"),
    )

    op.create_table(
        "faculty_profiles",
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("institutions.tenant_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("employee_code", sa.Text(), nullable=True),
        sa.Column("department_id", sa.Uuid(), nullable=True),
        sa.Column("designation", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("contact_email", sa.Text(), nullable=True),
        sa.Column("office_location", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            ondelete="RESTRICT",
            name="fk_faculty_profiles_membership",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            ondelete="RESTRICT",
            name="fk_faculty_profiles_department",
        ),
        sa.CheckConstraint(
            "employee_code IS NULL OR btrim(employee_code) <> ''",
            name="ck_faculty_profiles_employee_code",
        ),
    )
    op.create_index(
        "ix_faculty_profiles_department_id",
        "faculty_profiles",
        ["tenant_id", "department_id"],
    )
    op.create_index(
        "uq_faculty_profiles_employee_code",
        "faculty_profiles",
        ["tenant_id", sa.text("lower(btrim(employee_code))")],
        unique=True,
        postgresql_where=sa.text("employee_code IS NOT NULL"),
    )

    for table in ("student_profiles", "faculty_profiles"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
        )


def downgrade() -> None:
    for table in ("faculty_profiles", "student_profiles"):
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("faculty_profiles")
    op.drop_table("student_profiles")
    op.drop_table("account_invitations")
