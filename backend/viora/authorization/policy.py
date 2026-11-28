"""Explicit permissions; roles are eligibility, never unrestricted access."""

from typing import TYPE_CHECKING
from uuid import UUID

from ..contracts import Actor, ResourceRef, TenantContext
from ..identity.public import IdentityDirectory, InstitutionRole
from ..institution.public import InstitutionDirectory
from ..sources.public import SourceDirectory

if TYPE_CHECKING:
    from ..workflow.public import SqlWorkflowDirectory

ADMIN_ROLES = frozenset({"institution_owner", "institution_admin"})
INSTITUTION_ADMIN_ACTIONS = frozenset({
    "institution.read", "institution.update_metadata", "membership.create", "membership.list",
    "guardian_link.create", "guardian_link.list", "campus.create", "campus.list",
    "department.create", "department.list", "programme.create", "programme.list",
    "academic_year.create", "academic_year.list", "semester.list", "section.list",
    "subject.create", "subject.list", "offering.list",
    "student.directory.list", "faculty.directory.list",
})
ENTITY_ADMIN_ACTIONS: dict[str, frozenset[str]] = {
    "campus": frozenset({"campus.read", "campus.update", "campus.lifecycle", "campus.department.create"}),
    "department": frozenset({
        "department.read", "department.update_metadata", "department.update", "department.lifecycle",
        "department.programme.create", "department.subject.create", "department.head.assign",
        "department.head.list", "department.head.revoke",
    }),
    "programme": frozenset({"programme.read", "programme.update", "programme.lifecycle"}),
    "academic_year": frozenset({
        "academic_year.read", "academic_year.update", "academic_year.lifecycle", "academic_year.semester.create",
    }),
    "semester": frozenset({"semester.read", "semester.update", "semester.lifecycle", "semester.section.create"}),
    "section": frozenset({
        "section.read", "section.update_metadata", "section.update", "section.lifecycle",
        "section.offering.create", "section.coordinator.assign", "section.coordinator.list",
        "section.coordinator.revoke", "section.roster.read",
    }),
    "subject": frozenset({"subject.read", "subject.update", "subject.lifecycle"}),
    "offering": frozenset({
        "offering.catalog.read", "offering.update", "offering.lifecycle", "offering.teacher.assign",
        "offering.teacher.list", "offering.teacher.revoke", "offering.enrolment.create",
        "offering.enrolment.list", "offering.enrolment.withdraw",
    }),
}
ACTIVE_PARENT_REQUIRED_ACTIONS = frozenset({
    "campus.department.create", "department.programme.create", "department.subject.create",
    "department.head.assign", "academic_year.semester.create", "semester.section.create",
    "section.offering.create",
})
STUDENT_PUBLISHED_READ_ACTIONS = frozenset({
    "source.read", "source.version.read", "source.job.read", "source.published_knowledge.read",
})
TEACHER_RESOURCE_ACTIONS = frozenset({
    "source.read", "source.version.read", "source.job.read", "source.concepts.read",
    "source.update", "source.lifecycle", "source.version.create", "source.extract",
    "source.job.cancel", "source.job.retry",
})
TEACHER_ACTIVE_RESOURCE_ACTIONS = frozenset({
    "source.process", "source.concepts.review", "source.concepts.publish", "source.published_knowledge.read",
})
STUDENT_OWNER_ACTIONS = frozenset({
    "source.read", "source.version.read", "source.job.read", "source.concepts.read",
    "source.lifecycle", "source.job.cancel",
})
STUDENT_MUTABLE_SOURCE_ACTIONS = frozenset({
    "source.update", "source.version.create", "source.extract", "source.job.retry",
})
TEACHER_REVIEW_ACTIONS = frozenset({
    "source.read", "source.version.read", "source.job.read", "source.concepts.read",
})
OWNER_OR_ADMIN_ROLES = frozenset({InstitutionRole.institution_owner, InstitutionRole.institution_admin})


class Policy:
    def __init__(
        self,
        identities: IdentityDirectory,
        institutions: InstitutionDirectory,
        sources: SourceDirectory | None = None,
        workflows: "SqlWorkflowDirectory | None" = None,
    ) -> None:
        self.identities = identities
        self.institutions = institutions
        self.sources = sources
        self.workflows = workflows

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
        return self._permits_resolved(context, action, resource, current.roles)

    def _offering_access_state(
        self, tenant: UUID, actor: UUID, offering: UUID, *, need_teacher: bool, need_student: bool,
    ) -> tuple[bool, bool, bool]:
        fast_fn = getattr(self.institutions, "offering_access_state", None)
        if callable(fast_fn):
            return fast_fn(tenant, actor, offering, need_teacher=need_teacher, need_student=need_student)
        if self.institutions.academic_context(tenant, offering) is None:
            return False, False, False
        t_ok = need_teacher and self.institutions.teacher_assignment(tenant, actor, offering) is not None
        s_ok = need_student and self.institutions.student_enrolment(tenant, actor, offering) is not None
        return True, t_ok, s_ok

    def _permits_resolved(
        self, context: TenantContext, action: str, resource: ResourceRef, roles: frozenset[str],
    ) -> bool:
        tenant, actor, target = resource.tenant_id, context.actor_id, resource.resource_id
        is_owner = InstitutionRole.institution_owner.value in roles
        is_admin = bool(roles & ADMIN_ROLES)

        if resource.kind == "institution" and target == tenant:
            return self._permits_institution(action, roles, is_admin)
        if resource.kind == "membership":
            return self._permits_membership(tenant, actor, target, action, is_owner, is_admin)
        if resource.kind == "guardian_link":
            if action == "guardian_link.revoke" and is_admin:
                return True
            if action not in {"guardian_link.consent", "guardian_link.revoke"}:
                return False
            if actor == target and InstitutionRole.student.value in roles:
                return True
            return (
                action == "guardian_link.revoke"
                and InstitutionRole.guardian.value in roles
                and self.institutions.has_guardian_link_record(tenant, actor, target)
            )

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

        if resource.kind == "department" and "department_head" in roles and action in {
            "department.read", "department.update_metadata", "department.head.list",
        }:
            return self.institutions.heads_department(tenant, actor, target)
        if resource.kind in {"programme", "subject"} and "department_head" in roles and action == f"{resource.kind}.read":
            if self.institutions.entity_active(tenant, resource.kind, target) is not True:
                return False
            dept_id = self.institutions.entity_department(tenant, resource.kind, target)
            return dept_id is not None and self.institutions.heads_department(tenant, actor, dept_id)
        if resource.kind == "section" and "class_coordinator" in roles and action in {
            "section.read", "section.update_metadata", "section.coordinator.list", "section.roster.read",
        }:
            return self.institutions.coordinates_section(tenant, actor, target)
        if resource.kind == "offering":
            return self._permits_offering(tenant, actor, target, action, roles, is_admin)
        if resource.kind == "student_summary" and action == "student.summary.read":
            return (actor == target and "student" in roles) or (
                "guardian" in roles and self.institutions.guardian_link(tenant, actor, target)
            )
        if resource.kind == "student_profile":
            if action == "student.profile.read":
                if is_admin or "department_head" in roles:
                    return True
                if "class_coordinator" in roles and self.institutions.coordinator_has_student(tenant, actor, target):
                    return True
                if "subject_teacher" in roles and self.institutions.teacher_has_student(tenant, actor, target):
                    return True
                return actor == target and "student" in roles
            if action == "student.profile.update":
                return is_admin
            return False
        if resource.kind == "faculty_profile":
            if action == "faculty.profile.read":
                return is_admin or bool(roles & {"department_head", "class_coordinator", "subject_teacher"})
            if action == "faculty.responsibilities.read":
                return is_admin or (
                    actor == target
                    and bool(roles & {"department_head", "class_coordinator", "subject_teacher"})
                )
            if action == "faculty.profile.update":
                return is_admin
            return False
        if resource.kind == "workflow_job":
            job_source = None if self.workflows is None else self.workflows.job_source_id(tenant, target)
            if job_source is None:
                return False
            return self._permits_resolved(context, action, ResourceRef(tenant_id=tenant, kind="source", resource_id=job_source), roles)
        if resource.kind == "source":
            return self._permits_source(tenant, actor, target, action, roles)
        return False

    @staticmethod
    def _permits_institution(action: str, roles: frozenset[str], is_admin: bool) -> bool:
        if is_admin and action in INSTITUTION_ADMIN_ACTIONS:
            return True
        if action in {"department.list", "programme.list", "subject.list"}:
            return "department_head" in roles
        if action == "section.list":
            return "class_coordinator" in roles
        if action == "offering.list":
            return bool(roles & {"class_coordinator", "subject_teacher", "student"})
        if action == "responsibilities.self.read":
            return bool(roles)
        if action in {"student.directory.list", "faculty.directory.list"}:
            return bool(roles & {"department_head", "class_coordinator", "subject_teacher"})
        if action == "guardian_link.list":
            return bool(roles & {"student", "guardian"})
        return action == "knowledge.retrieve" and bool(
            roles & {"subject_teacher", "student"}
        )

    def _permits_membership(
        self, tenant: "UUID", actor: "UUID", target: "UUID", action: str, is_owner: bool, is_admin: bool,
    ) -> bool:
        if action in {"membership.read", "membership.lifecycle"}:
            if not is_admin:
                return False
            if action == "membership.lifecycle" and not is_owner:
                return not (OWNER_OR_ADMIN_ROLES & self.identities.unrevoked_roles(target, tenant))
            return True
        if action in {"role.admin.grant", "role.admin.revoke"}:
            return is_owner and not (action == "role.admin.grant" and actor == target)
        if action in {"role.grant", "role.revoke"}:
            if not is_admin or (action == "role.grant" and actor == target):
                return False
            return is_owner or not (OWNER_OR_ADMIN_ROLES & self.identities.unrevoked_roles(target, tenant))
        return False

    def _permits_offering(
        self, tenant: "UUID", actor: "UUID", target: "UUID", action: str, roles: frozenset[str], is_admin: bool,
    ) -> bool:
        has_teacher, has_student, has_coord = "subject_teacher" in roles, "student" in roles, "class_coordinator" in roles

        if action in {"offering.read", "offering.teach", "offering.catalog.read", "offering.teacher.list", "offering.enrolment.list"}:
            if not (has_teacher or has_student or has_coord):
                return False
            want_s = has_student and action in {"offering.read", "offering.catalog.read"}
            active, t_ok, s_ok = self._offering_access_state(tenant, actor, target, need_teacher=has_teacher, need_student=want_s)
            if not active:
                return False
            if t_ok or s_ok:
                return True
            if has_coord and action in {"offering.catalog.read", "offering.teacher.list"}:
                section_id = self.institutions.offering_section(tenant, target)
                return section_id is not None and self.institutions.coordinates_section(tenant, actor, section_id)
            return False

        if action in {"source.policy.read", "source.policy.update"}:
            if not (is_admin or has_teacher):
                return False
            active, t_ok, _ = self._offering_access_state(tenant, actor, target, need_teacher=not is_admin and has_teacher, need_student=False)
            return active and (is_admin or t_ok)

        if action in {"source.teacher.create", "source.student.submit", "source.offering.list"}:
            if action == "source.teacher.create" and not has_teacher:
                return False
            if action == "source.student.submit" and not (has_student and self.sources is not None):
                return False
            if not (has_teacher or has_student):
                return False
            want_t = has_teacher and action in {"source.teacher.create", "source.offering.list"}
            want_s = has_student and action in {"source.student.submit", "source.offering.list"}
            active, t_ok, s_ok = self._offering_access_state(tenant, actor, target, need_teacher=want_t, need_student=want_s)
            if not active:
                return False
            if action == "source.student.submit":
                assert self.sources is not None
                return s_ok and self.sources.offering_source_policy(tenant, target).student_submissions_enabled
            return t_ok or s_ok
        return False

    def _permits_source(
        self, tenant: "UUID", actor: "UUID", target: "UUID", action: str, roles: frozenset[str],
    ) -> bool:
        has_teacher, has_student = "subject_teacher" in roles, "student" in roles
        if self.sources is None or not (has_teacher or has_student):
            return False
        info = self.sources.source_access_info(tenant, target)
        if info is None or info.offering_id is None or info.origin_kind == "unscoped":
            return False

        if info.origin_kind == "teacher_resource":
            t_cand = has_teacher and (action in TEACHER_RESOURCE_ACTIONS or (action in TEACHER_ACTIVE_RESOURCE_ACTIONS and info.lifecycle_state == "active"))
            s_cand = has_student and action in STUDENT_PUBLISHED_READ_ACTIONS and info.visibility == "offering_published" and info.lifecycle_state == "active"
            if not (t_cand or s_cand):
                return False
            active, t_ok, s_ok = self._offering_access_state(tenant, actor, info.offering_id, need_teacher=t_cand, need_student=s_cand)
            return active and (t_ok or s_ok)

        if info.origin_kind == "student_submission":
            owner_cand = has_student and actor == info.owner_user_id
            owner_needs_policy = action in STUDENT_MUTABLE_SOURCE_ACTIONS or (action == "source.process" and info.lifecycle_state == "active")
            owner_action_valid = owner_cand and (action in STUDENT_OWNER_ACTIONS or owner_needs_policy)
            teacher_cand = has_teacher and action in TEACHER_REVIEW_ACTIONS and info.visibility == "teacher_review" and info.lifecycle_state == "active"
            if not (owner_action_valid or teacher_cand):
                return False
            active, t_ok, s_ok = self._offering_access_state(tenant, actor, info.offering_id, need_teacher=teacher_cand, need_student=owner_action_valid)
            if not active:
                return False
            if s_ok:
                return True if action in STUDENT_OWNER_ACTIONS else self.sources.offering_source_policy(tenant, info.offering_id).student_submissions_enabled
            if t_ok:
                return self.sources.offering_source_policy(tenant, info.offering_id).teachers_can_view_student_submissions
        return False
