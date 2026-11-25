"""Transaction-local tenant context and non-owner role checks, without RLS bypass."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def require_application_role(session: Session) -> None:
    """Reject privileged/owner roles and unsafe schemas before opening a protected scope."""
    unsafe = session.scalar(
        text("""
        SELECT EXISTS (
            SELECT 1 FROM pg_roles r WHERE pg_has_role(current_user, r.oid, 'MEMBER')
            AND (r.rolsuper OR r.rolbypassrls OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication)
        ) OR EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema() AND c.relkind IN ('r','p')
            AND (pg_has_role(current_user, c.relowner, 'MEMBER')
                 OR has_table_privilege(current_user, c.oid, 'TRUNCATE')
                 OR has_table_privilege(current_user, c.oid, 'TRIGGER'))
        ) OR has_schema_privilege(current_user, current_schema(), 'CREATE')
        OR EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attname='tenant_id' AND NOT a.attisdropped
            WHERE n.nspname=current_schema() AND c.relkind IN ('r','p')
            AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)
        )
    """)
    )
    if unsafe is not False:
        raise RuntimeError(
            "Application database role must be non-owner, unprivileged and subject to forced RLS"
        )


def bind_tenant(session: Session, tenant_id: UUID) -> None:
    """Internal candidate scope for membership lookup; never expose it before authorization."""
    previous = session.info.get("viora_tenant")
    if previous is not None and previous != tenant_id:
        raise RuntimeError("Cannot switch tenants inside a transaction")
    if not session.in_transaction():
        raise RuntimeError("Tenant context requires an explicit transaction")
    session.execute(
        text("SELECT set_config('viora.tenant_id', :tenant, true)"),
        {"tenant": str(tenant_id)},
    )
    session.info["viora_tenant"] = tenant_id


def bind_actor(session: Session, actor_id: UUID) -> None:
    """Bind an authenticated actor for actor-scoped discovery SELECT policies."""
    if not session.in_transaction():
        raise RuntimeError("Actor context requires an explicit transaction")
    previous = session.info.get("viora_actor")
    if previous is not None and previous != actor_id:
        raise RuntimeError("Cannot switch actors inside a transaction")
    session.execute(
        text("SELECT set_config('viora.actor_id', :actor, true)"),
        {"actor": str(actor_id)},
    )
    session.info["viora_actor"] = actor_id
