"""Explicit permissions; roles are eligibility, never unrestricted access."""

from ..contracts import Actor, ResourceRef, TenantContext
from ..identity.public import IdentityDirectory, InstitutionRole
from ..institution.public import InstitutionDirectory

ADMIN_ROLES = frozenset({"institution_owner", "institution_admin"})

INSTITUTION_ADMIN_ACTIONS = frozenset(
    {
        "institution.read",
        "institution.update_metadata",
        "membership.create",
        "membership.list",
        "guardian_link.create",
        "guardian_link.list",
        "campus.create",
        "campus.list",
        "department.create",
        "department.list",
        "programme.create",
        "programme.list",
        "academic_year.create",
        "academic_year.list",
        "semester.list",
        "section.list",
        "subject.create",
        "subject.list",
        "offering.list",
    }
)

ENTITY_ADMIN_ACTIONS: dict[str, frozenset[str]] = {
    "campus": frozenset(
        {"campus.read", "campus.update", "campus.lifecycle", "campus.department.create"}
    ),
    "department": frozenset(
        {
            "department.read",
            "department.update_metadata",
            "department.update",
            "department.lifecycle",
            "department.programme.create",
            "department.subject.create",
            "department.head.assign",
            "department.head.list",
            "department.head.revoke",
        }
    ),
    "programme": frozenset(
        {"programme.read", "programme.update", "programme.lifecycle"}
    ),
    "academic_year": frozenset(
        {
            "academic_year.read",
            "academic_year.update",
            "academic_year.lifecycle",
            "academic_year.semester.create",
        }
    ),
    "semester": frozenset(
        {
            "semester.read",
            "semester.update",
            "semester.lifecycle",
            "semester.section.create",
        }
    ),
    "section": frozenset(
        {
            "section.read",
            "section.update_metadata",
            "section.update",
            "section.lifecycle",
            "section.offering.create",
            "section.coordinator.assign",
            "section.coordinator.list",
            "section.coordinator.revoke",
        }
    ),
    "subject": frozenset({"subject.read", "subject.update", "subject.lifecycle"}),
    "offering": frozenset(
        {
            "offering.catalog.read",
            "offering.update",
            "offering.lifecycle",
            "offering.teacher.assign",
            "offering.teacher.list",
            "offering.teacher.revoke",
            "offering.enrolment.create",
            "offering.enrolment.list",
            "offering.enrolment.withdraw",
        }
    ),
}

ACTIVE_PARENT_REQUIRED_ACTIONS = frozenset(
    {
        "campus.department.create",
        "department.programme.create",
        "department.subject.create",
        "department.head.assign",
        "academic_year.semester.create",
        "semester.section.create",
        "section.offering.create",
    }
)


class Policy:
    def __init__(
        self, identities: IdentityDirectory, institutions: InstitutionDirectory
    ) -> None:
        self.identities = identities
        self.institutions = institutions

    def permits(
        self, context: TenantContext, action: str, resource: ResourceRef
    ) -> bool:
        if context.tenant_id != resource.tenant_id:
            return False
        current = self.identities.active(
            Actor(actor_id=context.actor_id), resource.tenant_id
        )
        if current is None:
            return False
        tenant, actor, target = (
            resource.tenant_id,
            context.actor_id,
            resource.resource_id,
        )
        roles = current.roles
        is_owner = InstitutionRole.institution_owner.value in roles
        is_admin = bool(roles & ADMIN_ROLES)

        if resource.kind == "institution" and target == tenant:
            if is_admin and action in INSTITUTION_ADMIN_ACTIONS:
                return True
            if action in {"department.list", "programme.list", "subject.list"}:
                return "department_head" in roles
            if action == "section.list":
                return "class_coordinator" in roles
            if action == "offering.list":
                return bool(
                    roles & {"class_coordinator", "subject_teacher", "student"}
                )
            if action == "guardian_link.list":
                return bool(roles & {"student", "guardian"})
            return False

        if resource.kind == "membership":
            if action in {"membership.read", "membership.lifecycle"}:
                if not is_admin:
                    return False
                if action == "membership.lifecycle" and not is_owner:
                    target_roles = self.identities.unrevoked_roles(target, tenant)
                    if {
                        InstitutionRole.institution_owner,
                        InstitutionRole.institution_admin,
                    } & target_roles:
                        return False
                return True
            if action in {"role.admin.grant", "role.admin.revoke"}:
                if not is_owner:
                    return False
                return not (action == "role.admin.grant" and actor == target)
            if action in {"role.grant", "role.revoke"}:
                if not is_admin:
                    return False
                if action == "role.grant" and actor == target:
                    return False
                if not is_owner:
                    target_roles = self.identities.unrevoked_roles(target, tenant)
                    if {
                        InstitutionRole.institution_owner,
                        InstitutionRole.institution_admin,
                    } & target_roles:
                        return False
                return True
            return False

        if resource.kind == "guardian_link":
            if action == "guardian_link.revoke" and is_admin:
                return True
            if action == "guardian_link.consent":
                return actor == target and InstitutionRole.student.value in roles
            if action == "guardian_link.revoke":
                return (
                    actor == target and InstitutionRole.student.value in roles
                ) or (
                    InstitutionRole.guardian.value in roles
                    and self.institutions.has_guardian_link_record(
                        tenant, actor, target
                    )
                )
            return False

        admin_actions = ENTITY_ADMIN_ACTIONS.get(resource.kind)
        if is_admin and admin_actions and action in admin_actions:
            status = self.institutions.entity_active(tenant, resource.kind, target)
            if status is None:
                return False
            if action == "section.coordinator.assign":
                return self.institutions.section_active(tenant, target)
            if action in {"offering.teacher.assign", "offering.enrolment.create"}:
                return self.institutions.academic_context(tenant, target) is not None
            return not (action in ACTIVE_PARENT_REQUIRED_ACTIONS and not status)

        if resource.kind == "department" and action in {
            "department.read",
            "department.update_metadata",
            "department.head.list",
        }:
            return self.institutions.heads_department(tenant, actor, target)

        if resource.kind in {"programme", "subject"} and action == f"{resource.kind}.read":
            if self.institutions.entity_active(tenant, resource.kind, target) is not True:
                return False
            dept_id = self.institutions.entity_department(tenant, resource.kind, target)
            return dept_id is not None and self.institutions.heads_department(
                tenant, actor, dept_id
            )

        if resource.kind == "section" and action in {
            "section.read",
            "section.update_metadata",
            "section.coordinator.list",
        }:
            return self.institutions.coordinates_section(tenant, actor, target)

        if resource.kind == "offering" and action in {
            "offering.read",
            "offering.teach",
            "offering.catalog.read",
            "offering.teacher.list",
            "offering.enrolment.list",
        }:
            if self.institutions.academic_context(tenant, target) is None:
                return False
            if self.institutions.teacher_assignment(tenant, actor, target) is not None:
                return True
            if (
                action in {"offering.read", "offering.catalog.read"}
                and self.institutions.student_enrolment(tenant, actor, target)
                is not None
            ):
                return True
            if action in {"offering.catalog.read", "offering.teacher.list"}:
                section_id = self.institutions.offering_section(tenant, target)
                return (
                    section_id is not None
                    and self.institutions.coordinates_section(tenant, actor, section_id)
                )
            return False

        if resource.kind == "student_summary" and action == "student.summary.read":
            return (
                actor == target and "student" in roles
            ) or self.institutions.guardian_link(tenant, actor, target)

        # Sources have no ownership/assignment/visibility scope yet. Unknown actions deny.
        return False
