"""Explicit permissions; roles are eligibility, never unrestricted access."""

from ..contracts import Actor, ResourceRef, TenantContext
from ..identity.public import IdentityDirectory
from ..institution.public import InstitutionDirectory


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
        if resource.kind == "institution" and target == tenant:
            return action in {
                "institution.read",
                "institution.update_metadata",
            } and bool(roles & {"institution_owner", "institution_admin"})
        if resource.kind == "department" and action in {
            "department.read",
            "department.update_metadata",
        }:
            return self.institutions.heads_department(tenant, actor, target)
        if resource.kind == "section" and action in {
            "section.read",
            "section.update_metadata",
        }:
            return self.institutions.coordinates_section(tenant, actor, target)
        if resource.kind == "offering" and action in {
            "offering.read",
            "offering.teach",
        }:
            if self.institutions.academic_context(tenant, target) is None:
                return False
            if self.institutions.teacher_assignment(tenant, actor, target) is not None:
                return True
            return (
                action == "offering.read"
                and self.institutions.student_enrolment(tenant, actor, target)
                is not None
            )
        if resource.kind == "student_summary" and action == "student.summary.read":
            return (
                actor == target and "student" in roles
            ) or self.institutions.guardian_link(tenant, actor, target)
        # Sources have no ownership/assignment/visibility scope yet. Unknown actions deny.
        return False
