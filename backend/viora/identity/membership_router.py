"""HTTP endpoints for institutional membership and role management."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError

from ..contracts import Page, ResourceRef
from ..security import AccessError, TransactionalAccess
from .repository import SqlMembershipRepository
from .schemas import (
    InstitutionRole,
    MemberCreate,
    MemberOnboardCreate,
    MemberOnboardResponse,
    MembershipLifecycleUpdate,
    MembershipStatus,
    MemberSummary,
    RoleGrantCreate,
)

ADMIN_ROLES = frozenset(
    {InstitutionRole.institution_owner, InstitutionRole.institution_admin}
)


def _token(bearer: HTTPAuthorizationCredentials | None) -> str | None:
    return bearer.credentials if bearer else None


def build_membership_router(access: TransactionalAccess) -> APIRouter:
    router = APIRouter(tags=["memberships"])
    bearer_dep = Security(HTTPBearer(auto_error=False))

    @router.post(
        "/tenants/{tenant_id}/memberships/onboard",
        operation_id="onboardMember",
        status_code=201,
    )
    def post_onboard_member(
        tenant_id: UUID,
        body: MemberOnboardCreate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> MemberOnboardResponse:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        try:
            with access.transaction(_token(bearer), "membership.create", ref) as scope:
                if (
                    body.role in ADMIN_ROLES
                    and InstitutionRole.institution_owner.value not in scope.context.roles
                ):
                    raise AccessError(
                        403,
                        "forbidden",
                        "Only an institution owner can onboard an admin role",
                    )
                result = SqlMembershipRepository(scope.session).onboard_member(
                    tenant_id,
                    email=body.email,
                    display_name=body.display_name,
                    role=body.role,
                    initial_password=body.initial_password,
                )
                scope.audit.change(
                    scope.context,
                    "membership.onboard",
                    ResourceRef(
                        tenant_id=tenant_id,
                        kind="membership",
                        resource_id=result.member.user_id,
                    ),
                )
                return result
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23505":
                raise AccessError(
                    409, "duplicate_record", "User already has a membership record"
                ) from None
            raise

    @router.post(
        "/tenants/{tenant_id}/memberships",
        operation_id="addMember",
        status_code=201,
    )
    def post_member(
        tenant_id: UUID,
        body: MemberCreate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> MemberSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        try:
            with access.transaction(_token(bearer), "membership.create", ref) as scope:
                member = SqlMembershipRepository(scope.session).add_member(
                    tenant_id, body.user_id, body.status
                )
                scope.audit.change(
                    scope.context,
                    "membership.create",
                    ResourceRef(
                        tenant_id=tenant_id, kind="membership", resource_id=body.user_id
                    ),
                )
                return member
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23505":
                raise AccessError(
                    409, "duplicate_record", "User already has a membership record"
                ) from None
            raise

    @router.get(
        "/tenants/{tenant_id}/memberships",
        operation_id="listMembers",
    )
    def get_members(
        tenant_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        status: MembershipStatus | None = None,
        cursor: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> Page[MemberSummary]:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        with access.transaction(_token(bearer), "membership.list", ref) as scope:
            return SqlMembershipRepository(scope.session).list_members(
                tenant_id, status=status, cursor=cursor, limit=limit
            )

    @router.get(
        "/tenants/{tenant_id}/memberships/{user_id}",
        operation_id="readMember",
    )
    def get_member(
        tenant_id: UUID,
        user_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> MemberSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="membership", resource_id=user_id)
        with access.transaction(_token(bearer), "membership.read", ref) as scope:
            member = SqlMembershipRepository(scope.session).get_member(
                tenant_id, user_id
            )
            if member is None:
                raise AccessError(404, "membership_missing", "Membership unavailable")
            return member

    @router.post(
        "/tenants/{tenant_id}/memberships/{user_id}/lifecycle",
        operation_id="setMembershipLifecycle",
    )
    def post_membership_lifecycle(
        tenant_id: UUID,
        user_id: UUID,
        body: MembershipLifecycleUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> MemberSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="membership", resource_id=user_id)
        with access.transaction(_token(bearer), "membership.lifecycle", ref) as scope:
            repository = SqlMembershipRepository(scope.session)
            if not repository.lock_member(tenant_id, user_id):
                raise AccessError(404, "membership_missing", "Membership unavailable")
            scope.recheck("membership.lifecycle", ref)
            member = repository.set_membership_status(tenant_id, user_id, body.status)
            if member is None:
                raise AccessError(404, "membership_missing", "Membership unavailable")
            scope.audit.change(scope.context, "membership.lifecycle", ref)
            return member

    @router.post(
        "/tenants/{tenant_id}/memberships/{user_id}/roles",
        operation_id="grantRole",
        status_code=201,
    )
    def post_grant_role(
        tenant_id: UUID,
        user_id: UUID,
        body: RoleGrantCreate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> MemberSummary:
        action = "role.admin.grant" if body.role in ADMIN_ROLES else "role.grant"
        ref = ResourceRef(tenant_id=tenant_id, kind="membership", resource_id=user_id)
        with access.transaction(_token(bearer), action, ref) as scope:
            repository = SqlMembershipRepository(scope.session)
            if not repository.lock_member(tenant_id, user_id):
                raise AccessError(404, "membership_missing", "Membership unavailable")
            scope.recheck(action, ref)
            member = repository.grant_role(tenant_id, user_id, body.role)
            if member is None:
                raise AccessError(404, "membership_missing", "Membership unavailable")
            scope.audit.change(scope.context, action, ref)
            return member

    @router.post(
        "/tenants/{tenant_id}/memberships/{user_id}/roles/{role}/revoke",
        operation_id="revokeRole",
    )
    def post_revoke_role(
        tenant_id: UUID,
        user_id: UUID,
        role: InstitutionRole,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> MemberSummary:
        action = "role.admin.revoke" if role in ADMIN_ROLES else "role.revoke"
        ref = ResourceRef(tenant_id=tenant_id, kind="membership", resource_id=user_id)
        with access.transaction(_token(bearer), action, ref) as scope:
            repository = SqlMembershipRepository(scope.session)
            if not repository.lock_member(tenant_id, user_id):
                raise AccessError(404, "membership_missing", "Membership unavailable")
            scope.recheck(action, ref)
            member = repository.revoke_role(tenant_id, user_id, role)
            if member is None:
                raise AccessError(404, "role_missing", "Active role grant unavailable")
            scope.audit.change(scope.context, action, ref)
            return member

    return router
