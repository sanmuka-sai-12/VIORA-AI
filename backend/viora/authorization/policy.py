"""Explicit permissions; roles are eligibility, never unrestricted access."""

from ..contracts import Actor, ResourceRef, TenantContext
from ..identity.public import IdentityDirectory
from ..institution.public import InstitutionDirectory

ADMIN_ROLES = frozenset({"institution_owner", "institution_admin"})

INSTITUTION_ADMIN_ACTIONS = frozenset(
    {
        "institution.read",
        "institution.update_metadata",
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
        }
    ),
    "subject": frozenset({"subject.read", "subject.update", "subject.lifecycle"}),
    "offering": frozenset(
        {"offering.catalog.read", "offering.update", "offering.lifecycle"}
    ),
}

CHILD_CREATION_ACTIONS = frozenset(
    {
        "campus.department.create",
        "department.programme.create",
        "department.subject.create",
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
            return False

        admin_actions = ENTITY_ADMIN_ACTIONS.get(resource.kind)
        if is_admin and admin_actions and action in admin_actions:
            status = self.institutions.entity_active(tenant, resource.kind, target)
            if status is None:
                return False
            return not (action in CHILD_CREATION_ACTIONS and not status)

        if resource.kind == "department" and action in {
            "department.read",
            "department.update_metadata",
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
        }:
            return self.institutions.coordinates_section(tenant, actor, target)

        if resource.kind == "offering" and action in {
            "offering.read",
            "offering.teach",
            "offering.catalog.read",
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
            if action == "offering.catalog.read":
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
