from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .schemas import AccountStatus, InstitutionRole, MembershipStatus


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("btrim(display_name) <> ''", name="ck_users_display_name"),
    )

    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(Text)
    status: Mapped[AccountStatus] = mapped_column(
        Enum(
            AccountStatus,
            native_enum=False,
            create_constraint=True,
            name="ck_users_status",
        ),
        server_default=AccountStatus.pending.value,
    )


class VerifiedIdentityAttribute(Base):
    """Email values are case-insensitive login identifiers; pending claims live outside this table."""

    __tablename__ = "verified_identity_attributes"
    __table_args__ = (
        UniqueConstraint("kind", "normalized_value", name="uq_verified_identifier"),
        CheckConstraint("kind IN ('email', 'phone')", name="ck_verified_kind"),
        CheckConstraint(
            "normalized_value = btrim(normalized_value) AND normalized_value <> ''",
            name="ck_verified_normalized",
        ),
        CheckConstraint(
            "kind <> 'email' OR normalized_value = lower(normalized_value)",
            name="ck_verified_email_case",
        ),
        CheckConstraint(
            "kind <> 'phone' OR normalized_value ~ '^\\+[1-9][0-9]{1,14}$'",
            name="ck_verified_phone_e164",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.user_id", ondelete="RESTRICT"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    normalized_value: Mapped[str] = mapped_column(Text)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PasswordCredential(Base):
    """Only the credential adapter reads encoded verifier material."""

    __tablename__ = "password_credentials"
    __table_args__ = (
        CheckConstraint("btrim(password_hash) <> ''", name="ck_password_hash_present"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.user_id", ondelete="RESTRICT"), primary_key=True
    )
    password_hash: Mapped[str] = mapped_column(Text)


class MembershipRecord(Base):
    __tablename__ = "memberships"
    __table_args__ = (Index("ix_memberships_user_status", "user_id", "status"),)

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("institutions.tenant_id", ondelete="RESTRICT"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.user_id", ondelete="RESTRICT"), primary_key=True
    )
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(
            MembershipStatus,
            native_enum=False,
            create_constraint=True,
            name="ck_memberships_status",
        ),
        server_default=MembershipStatus.invited.value,
    )


class RoleGrant(Base):
    __tablename__ = "role_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            ondelete="RESTRICT",
            name="fk_role_grants_membership",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    role: Mapped[InstitutionRole] = mapped_column(
        Enum(
            InstitutionRole,
            native_enum=False,
            create_constraint=True,
            name="ck_role_grants_role",
        ),
        primary_key=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
