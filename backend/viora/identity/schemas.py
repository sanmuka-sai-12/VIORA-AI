from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr

from ..contracts import Contract


class AccountStatus(StrEnum):
    pending = "pending"
    active = "active"
    disabled = "disabled"


class MembershipStatus(StrEnum):
    invited = "invited"
    active = "active"
    disabled = "disabled"
    revoked = "revoked"


class InstitutionRole(StrEnum):
    institution_owner = "institution_owner"
    institution_admin = "institution_admin"
    department_head = "department_head"
    class_coordinator = "class_coordinator"
    subject_teacher = "subject_teacher"
    student = "student"
    guardian = "guardian"


class UserSummary(Contract):
    user_id: UUID
    display_name: str
    status: AccountStatus


class IdentityAttributeKind(StrEnum):
    email = "email"
    phone = "phone"


class MembershipSummary(Contract):
    tenant_id: UUID
    user_id: UUID
    status: MembershipStatus


class DiscoveredMembership(Contract):
    tenant_id: UUID
    roles: tuple[InstitutionRole, ...]


class MemberCreate(Contract):
    user_id: UUID
    status: Literal[MembershipStatus.active, MembershipStatus.invited] = (
        MembershipStatus.active
    )


class MembershipLifecycleUpdate(Contract):
    status: Literal[
        MembershipStatus.active,
        MembershipStatus.disabled,
        MembershipStatus.revoked,
    ]


class RoleGrantCreate(Contract):
    role: InstitutionRole


class MemberSummary(Contract):
    tenant_id: UUID
    user_id: UUID
    display_name: str
    account_status: AccountStatus
    status: MembershipStatus
    roles: tuple[InstitutionRole, ...]


class MemberOnboardCreate(Contract):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=200)
    role: InstitutionRole | None = None
    initial_password: SecretStr | None = Field(default=None, min_length=12, max_length=1024)


class MemberOnboardResponse(Contract):
    member: MemberSummary
    email: str
    account_created: bool
    invite_token: str | None = None
    invite_expires_at: datetime | None = None
