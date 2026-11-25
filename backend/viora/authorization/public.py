"""Public authorization and transaction composition for APIs and future workers."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from ..contracts import Actor, ResourceRef, TenantContext
from ..db import Database
from ..identity.public import identity_directory
from ..institution.public import (
    InstitutionDiscovery,
    discover_institutions,
    institution_directory,
    institution_status,
)
from ..security import Access, AccessError, Authentication, ChangeAudit
from .audit import SqlAudit
from .policy import Policy
from .tenant import bind_actor, bind_tenant, require_application_role

__all__ = [
    "AuthorizationService",
    "AuthorizedTransaction",
    "bind_tenant",
    "change_audit",
    "register_models",
]


@dataclass(frozen=True)
class AuthorizedTransaction:
    """Use this session for the authorized operation; never retain it after the block."""

    session: Session
    context: TenantContext
    audit: ChangeAudit
    _access: Access

    def recheck(self, action: str, resource: ResourceRef) -> TenantContext:
        """Re-read committed grants before another protected effect within this transaction."""
        context = self._access.require("verified-internal-actor", action, resource)
        self.audit.record(context, action, resource, True)
        return context


class _VerifiedActor:
    def __init__(self, actor: Actor) -> None:
        self.actor = actor

    def verify(self, bearer: str) -> Actor:
        return self.actor


class AuthorizationService:
    def __init__(
        self, database: Database, authentication: Authentication | None
    ) -> None:
        self.authentication = authentication
        self.sessions: sessionmaker[Session] = sessionmaker(
            database.engine.execution_options(isolation_level="READ COMMITTED"),
            expire_on_commit=False,
        )

    def validate_database(self) -> None:
        """Fail application startup rather than serving requests with migration privileges."""
        with self.sessions.begin() as session:
            require_application_role(session)

    @contextmanager
    def transaction(
        self, bearer: str | None, action: str, resource: ResourceRef
    ) -> Iterator[AuthorizedTransaction]:
        actor = (
            self.authentication.verify(bearer)
            if bearer and self.authentication
            else None
        )
        if actor is None:
            raise AccessError(
                401, "unauthenticated", "Valid bearer credentials required"
            )
        with self._actor_transaction(actor, action, resource) as scope:
            yield scope

    @contextmanager
    def _actor_transaction(
        self, actor: Actor, action: str, resource: ResourceRef
    ) -> Iterator[AuthorizedTransaction]:
        denied: AccessError | None = None
        with self.sessions.begin() as session:
            require_application_role(session)
            bind_tenant(session, resource.tenant_id)
            identities = identity_directory(session, institution_status(session))
            policy = Policy(identities, institution_directory(session, identities))
            audit = SqlAudit(session)
            access = Access(_VerifiedActor(actor), identities, policy)
            try:
                context = access.require("verified-internal-actor", action, resource)
            except AccessError as error:
                denied = error
                audit.record(
                    TenantContext(
                        actor_id=actor.actor_id,
                        tenant_id=resource.tenant_id,
                        roles=frozenset(),
                    ),
                    action,
                    resource,
                    False,
                )
            else:
                audit.record(context, action, resource, True)
                yield AuthorizedTransaction(session, context, audit, access)
        # A rejected decision is committed before the HTTP error is raised.
        if denied is not None:
            raise denied

    def require(
        self, bearer: str | None, action: str, resource: ResourceRef
    ) -> TenantContext:
        """Decision only. Protected SQL must instead run inside transaction()."""
        with self.transaction(bearer, action, resource) as scope:
            return scope.context

    def discover_institutions(
        self, bearer: str | None
    ) -> tuple[InstitutionDiscovery, ...]:
        """Return actor-owned active memberships through actor-scoped RLS."""
        actor = (
            self.authentication.verify(bearer)
            if bearer and self.authentication
            else None
        )
        if actor is None:
            raise AccessError(
                401, "unauthenticated", "Valid bearer credentials required"
            )
        from ..identity.public import discover_memberships

        with self.sessions.begin() as session:
            require_application_role(session)
            bind_actor(session, actor.actor_id)
            memberships = discover_memberships(session, actor.actor_id)
            return discover_institutions(session, memberships)

    @contextmanager
    def reauthorize(
        self, actor: Actor, action: str, resource: ResourceRef
    ) -> Iterator[AuthorizedTransaction]:
        """Trusted worker input: reload persisted initiating actor and resource before each effect."""
        with self._actor_transaction(actor, action, resource) as scope:
            yield scope


def change_audit(session: Session) -> ChangeAudit:
    """Bind append-only audit writer to an operator bootstrap transaction."""
    return SqlAudit(session)


def register_models() -> None:
    from . import models  # noqa: F401
