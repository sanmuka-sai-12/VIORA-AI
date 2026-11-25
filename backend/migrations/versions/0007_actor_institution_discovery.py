"""Actor-scoped, SELECT-only institution discovery through forced RLS."""

from alembic import op

revision = "0007_actor_institution_discovery"
down_revision = "0006_academic_lifecycle"
branch_labels = None
depends_on = None

ACTOR = "NULLIF(current_setting('viora.actor_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        f"CREATE POLICY membership_actor_discovery ON memberships FOR SELECT "
        f"USING (user_id = {ACTOR} AND status = 'active')"
    )
    op.execute(
        f"CREATE POLICY role_actor_discovery ON role_grants FOR SELECT "
        f"USING (user_id = {ACTOR} AND revoked_at IS NULL)"
    )
    op.execute(
        f"CREATE POLICY institution_actor_discovery ON institutions FOR SELECT "
        f"USING (active AND EXISTS (SELECT 1 FROM memberships m "
        f"WHERE m.tenant_id = institutions.tenant_id AND m.user_id = {ACTOR} "
        f"AND m.status = 'active'))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY institution_actor_discovery ON institutions")
    op.execute("DROP POLICY role_actor_discovery ON role_grants")
    op.execute("DROP POLICY membership_actor_discovery ON memberships")
