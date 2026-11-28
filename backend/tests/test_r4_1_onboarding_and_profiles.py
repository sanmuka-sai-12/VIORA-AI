"""Unit and authorization tests for R4.1 onboarding and academic profiles."""

from unittest.mock import create_autospec
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from viora.authorization.policy import Policy
from viora.contracts import ResourceRef, TenantContext
from viora.identity.public import (
    IdentityDirectory,
    InstitutionRole,
    MemberOnboardCreate,
)
from viora.institution.public import InstitutionDirectory
from viora.institution.schemas import FacultyProfileUpdate, StudentProfileUpdate


def _policy_for_roles(
    tenant_id, actor_id, roles: frozenset[str]
) -> Policy:
    identities = create_autospec(IdentityDirectory, instance=True, spec_set=True)
    identities.active.return_value = TenantContext(
        actor_id=actor_id, tenant_id=tenant_id, roles=roles
    )
    institutions = create_autospec(InstitutionDirectory, instance=True, spec_set=True)
    return Policy(identities=identities, institutions=institutions)


def test_r4_1_policy_allows_admin_and_faculty_directory_reads_and_restricts_edits() -> None:
    tenant = uuid4()
    admin = uuid4()
    teacher = uuid4()
    student = uuid4()
    other_student = uuid4()

    inst_ref = ResourceRef(tenant_id=tenant, kind="institution", resource_id=tenant)
    stu_ref = ResourceRef(tenant_id=tenant, kind="student_profile", resource_id=student)
    fac_ref = ResourceRef(tenant_id=tenant, kind="faculty_profile", resource_id=teacher)

    admin_policy = _policy_for_roles(tenant, admin, frozenset({"institution_admin"}))
    admin_ctx = TenantContext(actor_id=admin, tenant_id=tenant, roles=frozenset())
    assert admin_policy.permits(admin_ctx, "student.directory.list", inst_ref)
    assert admin_policy.permits(admin_ctx, "faculty.directory.list", inst_ref)
    assert admin_policy.permits(admin_ctx, "student.profile.read", stu_ref)
    assert admin_policy.permits(admin_ctx, "student.profile.update", stu_ref)
    assert admin_policy.permits(admin_ctx, "faculty.profile.read", fac_ref)
    assert admin_policy.permits(admin_ctx, "faculty.profile.update", fac_ref)

    teacher_policy = _policy_for_roles(tenant, teacher, frozenset({"subject_teacher"}))
    teacher_ctx = TenantContext(actor_id=teacher, tenant_id=tenant, roles=frozenset())
    assert teacher_policy.permits(teacher_ctx, "student.directory.list", inst_ref)
    assert teacher_policy.permits(teacher_ctx, "faculty.directory.list", inst_ref)
    assert teacher_policy.permits(teacher_ctx, "student.profile.read", stu_ref)
    assert teacher_policy.permits(teacher_ctx, "faculty.profile.read", fac_ref)
    assert not teacher_policy.permits(teacher_ctx, "student.profile.update", stu_ref)
    assert not teacher_policy.permits(teacher_ctx, "faculty.profile.update", fac_ref)

    student_policy = _policy_for_roles(tenant, student, frozenset({"student"}))
    student_ctx = TenantContext(actor_id=student, tenant_id=tenant, roles=frozenset())
    assert student_policy.permits(student_ctx, "student.profile.read", stu_ref)
    assert not student_policy.permits(
        student_ctx,
        "student.profile.read",
        ResourceRef(tenant_id=tenant, kind="student_profile", resource_id=other_student),
    )
    assert not student_policy.permits(student_ctx, "student.directory.list", inst_ref)
    assert not student_policy.permits(student_ctx, "faculty.directory.list", inst_ref)
    assert not student_policy.permits(student_ctx, "student.profile.update", stu_ref)
    assert not student_policy.permits(student_ctx, "faculty.profile.read", fac_ref)


def test_r4_1_cross_tenant_profile_access_denied() -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    admin = uuid4()
    policy = _policy_for_roles(tenant_a, admin, frozenset({"institution_owner"}))
    ctx = TenantContext(actor_id=admin, tenant_id=tenant_a, roles=frozenset())
    assert not policy.permits(
        ctx,
        "student.profile.read",
        ResourceRef(tenant_id=tenant_b, kind="student_profile", resource_id=uuid4()),
    )


def test_r4_1_onboarding_and_profile_contract_validation() -> None:
    valid = MemberOnboardCreate(
        email="student@college.edu",
        display_name="Ada Lovelace",
        role=InstitutionRole.student,
        initial_password=SecretStr("correct-horse-battery"),
    )
    assert valid.role == InstitutionRole.student

    invite_only = MemberOnboardCreate(
        email="faculty@college.edu",
        display_name="Grace Hopper",
        role=InstitutionRole.subject_teacher,
    )
    assert invite_only.initial_password is None

    with pytest.raises(ValidationError):
        MemberOnboardCreate(
            email="a",
            display_name="Ada",
        )

    with pytest.raises(ValidationError):
        MemberOnboardCreate(
            email="ada@college.edu",
            display_name="Ada",
            initial_password=SecretStr("short"),
        )

    stu_update = StudentProfileUpdate(roll_number="  CS-101  ", phone=" +91-99999 ")
    assert stu_update.roll_number == "CS-101"
    assert stu_update.phone == "+91-99999"

    fac_update = FacultyProfileUpdate(
        employee_code=" EMP-42 ", designation=" Associate Professor "
    )
    assert fac_update.employee_code == "EMP-42"
    assert fac_update.designation == "Associate Professor"
