"""Add classroom_announcements table and source folder_label organization."""

import sqlalchemy as sa
from alembic import op

revision = "0020_classroom_announcements"
down_revision = "0019_profiles_and_onboarding"
branch_labels = None
depends_on = None

PREDICATE = "tenant_id = NULLIF(current_setting('viora.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "classroom_announcements",
        sa.Column("tenant_id", sa.Uuid(), primary_key=True),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("offering_id", sa.Uuid(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "pinned",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "offering_id"],
            ["subject_offerings.tenant_id", "subject_offerings.id"],
            ondelete="RESTRICT",
            name="fk_classroom_announcements_offering",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "author_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            ondelete="RESTRICT",
            name="fk_classroom_announcements_author",
        ),
        sa.CheckConstraint(
            "btrim(title) <> ''",
            name="ck_classroom_announcements_title",
        ),
        sa.CheckConstraint(
            "btrim(body) <> ''",
            name="ck_classroom_announcements_body",
        ),
    )
    op.create_index(
        "ix_classroom_announcements_offering_created",
        "classroom_announcements",
        ["tenant_id", "offering_id", "created_at"],
    )
    op.execute("ALTER TABLE classroom_announcements ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE classroom_announcements FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON classroom_announcements USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
    )

    op.add_column(
        "sources",
        sa.Column("folder_label", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sources", "folder_label")
    op.execute("DROP POLICY tenant_isolation ON classroom_announcements")
    op.execute("ALTER TABLE classroom_announcements NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE classroom_announcements DISABLE ROW LEVEL SECURITY")
    op.drop_table("classroom_announcements")
