"""Identity, membership and academic relationships."""

import sqlalchemy as sa
from alembic import op

revision = "0002_identity_academics"
down_revision = "0001_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "institutions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_institutions_name"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.create_table(
        "users",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "active",
                "disabled",
                name="ck_users_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.CheckConstraint("btrim(display_name) <> ''", name="ck_users_display_name"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "academic_years",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_academic_years_name"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id"),
    )
    op.create_table(
        "campuses",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_campuses_name"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id"),
    )
    op.create_table(
        "memberships",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "invited",
                "active",
                "disabled",
                "revoked",
                name="ck_memberships_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="invited",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("tenant_id", "user_id"),
    )
    op.create_index(
        "ix_memberships_user_status", "memberships", ["user_id", "status"], unique=False
    )
    op.create_table(
        "password_credentials",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "btrim(password_hash) <> ''", name="ck_password_hash_present"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "verified_identity_attributes",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("normalized_value", sa.Text(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind <> 'email' OR normalized_value = lower(normalized_value)",
            name="ck_verified_email_case",
        ),
        sa.CheckConstraint(
            "kind <> 'phone' OR normalized_value ~ '^\\+[1-9][0-9]{1,14}$'",
            name="ck_verified_phone_e164",
        ),
        sa.CheckConstraint("kind IN ('email', 'phone')", name="ck_verified_kind"),
        sa.CheckConstraint(
            "normalized_value = btrim(normalized_value) AND normalized_value <> ''",
            name="ck_verified_normalized",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id", "kind"),
        sa.UniqueConstraint("kind", "normalized_value", name="uq_verified_identifier"),
    )
    op.create_table(
        "departments",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campus_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_departments_name"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campus_id"],
            ["campuses.tenant_id", "campuses.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id"),
    )
    op.create_index(
        "ix_departments_campus_id",
        "departments",
        ["tenant_id", "campus_id"],
        unique=False,
    )
    op.create_table(
        "guardian_links",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "NOT active OR (consent_at IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_guardian_consent",
        ),
        sa.CheckConstraint("user_id <> student_id", name="ck_guardian_distinct"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", "student_id"),
    )
    op.create_index(
        "ix_guardian_links_student_id",
        "guardian_links",
        ["tenant_id", "student_id"],
        unique=False,
    )
    op.create_table(
        "role_grants",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "institution_owner",
                "institution_admin",
                "department_head",
                "class_coordinator",
                "subject_teacher",
                "student",
                "guardian",
                name="ck_role_grants_role",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_role_grants_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", "role"),
    )
    op.create_table(
        "semesters",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("academic_year_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_semesters_name"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "academic_year_id"],
            ["academic_years.tenant_id", "academic_years.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id"),
    )
    op.create_index(
        "ix_semesters_academic_year_id",
        "semesters",
        ["tenant_id", "academic_year_id"],
        unique=False,
    )
    op.create_table(
        "department_heads",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", "department_id"),
    )
    op.create_index(
        "ix_department_heads_department_id",
        "department_heads",
        ["tenant_id", "department_id"],
        unique=False,
    )
    op.create_table(
        "programmes",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_programmes_name"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id"),
    )
    op.create_index(
        "ix_programmes_department_id",
        "programmes",
        ["tenant_id", "department_id"],
        unique=False,
    )
    op.create_table(
        "subjects",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_subjects_name"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id"),
    )
    op.create_index(
        "ix_subjects_department_id",
        "subjects",
        ["tenant_id", "department_id"],
        unique=False,
    )
    op.create_table(
        "sections",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("programme_id", sa.Uuid(), nullable=True),
        sa.Column("semester_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_sections_name"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "programme_id"],
            ["programmes.tenant_id", "programmes.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "semester_id"],
            ["semesters.tenant_id", "semesters.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id"),
    )
    op.create_index(
        "ix_sections_programme_id",
        "sections",
        ["tenant_id", "programme_id"],
        unique=False,
    )
    op.create_index(
        "ix_sections_semester_id",
        "sections",
        ["tenant_id", "semester_id"],
        unique=False,
    )
    op.create_table(
        "class_coordinators",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "section_id"],
            ["sections.tenant_id", "sections.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", "section_id"),
    )
    op.create_index(
        "ix_class_coordinators_section_id",
        "class_coordinators",
        ["tenant_id", "section_id"],
        unique=False,
    )
    op.create_table(
        "subject_offerings",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_subject_offerings_name"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "section_id"],
            ["sections.tenant_id", "sections.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "subject_id"],
            ["subjects.tenant_id", "subjects.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id"),
    )
    op.create_index(
        "ix_subject_offerings_section_id",
        "subject_offerings",
        ["tenant_id", "section_id"],
        unique=False,
    )
    op.create_index(
        "ix_subject_offerings_subject_id",
        "subject_offerings",
        ["tenant_id", "subject_id"],
        unique=False,
    )
    op.create_table(
        "student_enrolments",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("offering_id", sa.Uuid(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "offering_id"],
            ["subject_offerings.tenant_id", "subject_offerings.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", "offering_id"),
    )
    op.create_index(
        "ix_student_enrolments_offering_id",
        "student_enrolments",
        ["tenant_id", "offering_id"],
        unique=False,
    )
    op.create_table(
        "teacher_assignments",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("offering_id", sa.Uuid(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "offering_id"],
            ["subject_offerings.tenant_id", "subject_offerings.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", "offering_id"),
    )
    op.create_index(
        "ix_teacher_assignments_offering_id",
        "teacher_assignments",
        ["tenant_id", "offering_id"],
        unique=False,
    )

    op.execute("LOCK TABLE sources IN SHARE ROW EXCLUSIVE MODE")
    # Preserve tenant references from Phase 0 without enabling access to legacy data.
    op.execute(
        "INSERT INTO institutions (tenant_id, name, active) "
        "SELECT DISTINCT tenant_id, 'Legacy institution ' || tenant_id::text, false FROM sources"
    )
    op.create_foreign_key(
        "fk_sources_institution",
        "sources",
        "institutions",
        ["tenant_id"],
        ["tenant_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_sources_institution", "sources", type_="foreignkey")
    op.drop_table("teacher_assignments")
    op.drop_table("student_enrolments")
    op.drop_table("subject_offerings")
    op.drop_table("class_coordinators")
    op.drop_table("sections")
    op.drop_table("subjects")
    op.drop_table("programmes")
    op.drop_table("department_heads")
    op.drop_table("semesters")
    op.drop_table("role_grants")
    op.drop_table("guardian_links")
    op.drop_table("departments")
    op.drop_table("verified_identity_attributes")
    op.drop_table("password_credentials")
    op.drop_table("memberships")
    op.drop_table("campuses")
    op.drop_table("academic_years")
    op.drop_table("users")
    op.drop_table("institutions")
