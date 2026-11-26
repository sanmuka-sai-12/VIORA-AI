"""Application services for institution metadata and academic hierarchy management."""

from collections.abc import Callable, Iterable
from typing import Any, TypeVar
from uuid import UUID, uuid4

from ..contracts import Page, ResourceRef, TenantContext
from ..identity.public import IdentityDirectory, InstitutionRole
from ..security import AccessError, AuthorizedScope
from .ports import AcademicRepository
from .schemas import (
    AcademicYearCreate,
    AcademicYearSummary,
    AcademicYearUpdate,
    AssignmentCreate,
    CampusCreate,
    CampusSummary,
    CampusUpdate,
    ClassCoordinatorSummary,
    DepartmentCreate,
    DepartmentHeadSummary,
    DepartmentSummary,
    DepartmentUpdate,
    GuardianLinkCreate,
    GuardianLinkSummary,
    InstitutionInfo,
    InstitutionUpdate,
    LifecycleUpdate,
    OfferingAssignmentSummary,
    ProgrammeCreate,
    ProgrammeSummary,
    ProgrammeUpdate,
    SectionCreate,
    SectionSummary,
    SectionUpdate,
    SemesterCreate,
    SemesterSummary,
    SemesterUpdate,
    StudentSummary,
    SubjectCreate,
    SubjectOfferingCreate,
    SubjectOfferingSummary,
    SubjectOfferingUpdate,
    SubjectSummary,
    SubjectUpdate,
)

T = TypeVar("T")
ADMIN_ROLES = frozenset({"institution_owner", "institution_admin"})


def _is_admin(context: TenantContext) -> bool:
    return bool(context.roles & ADMIN_ROLES)


def _require_found(row: T | None, kind: str, label: str | None = None) -> T:
    if row is None:
        raise AccessError(
            404, f"{kind}_missing", f"{label or kind.replace('_', ' ').capitalize()} unavailable"
        )
    return row


def _require_fields(fields_set: set[str], *non_null_fields: tuple[str, object]) -> None:
    if not fields_set:
        raise AccessError(422, "invalid_request", "At least one field must be provided")
    for field, value in non_null_fields:
        if field in fields_set and value is None:
            raise AccessError(422, "invalid_request", f"{field} cannot be null")


def _require_active_parent(
    scope: AuthorizedScope, action: str, kind: str, tenant_id: UUID, parent_id: UUID, parent_row: object | None,
) -> None:
    scope.recheck(action, ResourceRef(tenant_id=tenant_id, kind=kind, resource_id=parent_id))
    active = getattr(parent_row, "active", None) if parent_row is not None else None
    _require_found(active, kind)
    if not active:
        raise AccessError(409, "inactive_parent", f"Referenced {kind} is archived")


def _require_parent_if_present(
    parent_row: object | None, kind: str
) -> None:
    if parent_row is None or not getattr(parent_row, "active", False):
        raise AccessError(409, "inactive_parent", f"Referenced {kind} is archived")


def _ensure_no_active_children(has_children: bool, kind: str) -> None:
    if has_children:
        raise AccessError(
            409,
            "active_dependents",
            f"Archive active dependent records before archiving this {kind}",
        )


def _audit(
    scope: AuthorizedScope, action: str, tenant_id: UUID, kind: str, resource_id: UUID
) -> None:
    scope.audit.change(
        scope.context,
        action,
        ResourceRef(tenant_id=tenant_id, kind=kind, resource_id=resource_id),
    )


def _set_lifecycle(
    scope: AuthorizedScope,
    tenant_id: UUID,
    kind: str,
    entity_id: UUID,
    body: LifecycleUpdate,
    *,
    label: str | None = None,
    get_fn: Callable[[UUID, UUID], T | None] | None = None,
    has_children_fn: Callable[[UUID, UUID], bool] | None = None,
    parent_checks_on_activate: Callable[[T], Iterable[tuple[str, object | None]]] | None = None,
    set_active_fn: Callable[[UUID, UUID, bool], T | None],
) -> T:
    current: T | None = None
    if get_fn is not None:
        current = _require_found(get_fn(tenant_id, entity_id), kind, label)
    if not body.active:
        if has_children_fn is not None:
            _ensure_no_active_children(has_children_fn(tenant_id, entity_id), kind)
    elif parent_checks_on_activate is not None and current is not None:
        for parent_kind, parent_row in parent_checks_on_activate(current):
            _require_parent_if_present(parent_row, parent_kind)
    updated = _require_found(set_active_fn(tenant_id, entity_id, body.active), kind, label)
    _audit(scope, f"{kind}.lifecycle", tenant_id, kind, entity_id)
    return updated


def _require_role(
    identities: IdentityDirectory,
    tenant_id: UUID,
    user_id: UUID,
    role: InstitutionRole,
) -> None:
    if role not in identities.applicable_roles(user_id, tenant_id):
        raise AccessError(
            409,
            "user_ineligible",
            f"User must hold an active membership and {role.value} role in this institution",
        )


def _assign_role(
    scope: AuthorizedScope,
    identities: IdentityDirectory,
    tenant_id: UUID,
    kind: str,
    target_id: UUID,
    user_id: UUID,
    role: InstitutionRole,
    audit_action: str,
    assign_fn: Callable[[UUID, UUID, UUID], T],
) -> T:
    _require_role(identities, tenant_id, user_id, role)
    created = assign_fn(tenant_id, target_id, user_id)
    _audit(scope, audit_action, tenant_id, kind, target_id)
    return created


def _revoke_role(
    scope: AuthorizedScope,
    tenant_id: UUID,
    kind: str,
    target_id: UUID,
    user_id: UUID,
    audit_action: str,
    revoke_fn: Callable[[UUID, UUID, UUID], T | None],
    missing_code: str = "assignment_missing",
    missing_msg: str = "Active assignment unavailable",
) -> T:
    revoked = revoke_fn(tenant_id, target_id, user_id)
    if revoked is None:
        raise AccessError(404, missing_code, missing_msg)
    _audit(scope, audit_action, tenant_id, kind, target_id)
    return revoked


def _create_dept_child(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    kind: str,
    name: str,
    department_id: UUID | None,
    create_fn: Any,
) -> T:
    if department_id is not None:
        _require_parent_if_present(
            repo.get_department(tenant_id, department_id), "department"
        )
    entity_id = uuid4()
    created = create_fn(tenant_id, entity_id, name=name, department_id=department_id)
    _audit(scope, f"{kind}.create", tenant_id, kind, entity_id)
    return created


def _update_dept_child(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    kind: str,
    entity_id: UUID,
    body: Any,
    update_fn: Any,
) -> T:
    fields = set(body.model_fields_set)
    _require_fields(fields, ("name", body.name))
    update_dept = "department_id" in fields
    if update_dept and body.department_id is not None:
        _require_active_parent(scope, "department.read", "department", tenant_id, body.department_id, repo.get_department(tenant_id, body.department_id))
    updated = _require_found(
        update_fn(
            tenant_id,
            entity_id,
            name=body.name,
            department_id=body.department_id,
            update_department=update_dept,
        ),
        kind,
    )
    _audit(scope, f"{kind}.update", tenant_id, kind, entity_id)
    return updated


# --- Institution ---
def read_institution(repo: AcademicRepository, tenant_id: UUID) -> InstitutionInfo:
    return _require_found(repo.get_institution(tenant_id), "institution")


def update_institution(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    body: InstitutionUpdate,
) -> InstitutionInfo:
    updated = _require_found(repo.update_institution(tenant_id, name=body.name), "institution")
    _audit(scope, "institution.update_metadata", tenant_id, "institution", tenant_id)
    return updated


# --- Campuses ---
def list_campuses(
    repo: AcademicRepository, tenant_id: UUID, *, active: bool | None, cursor: UUID | None, limit: int
) -> Page[CampusSummary]:
    return repo.list_campuses(tenant_id, active=active, cursor=cursor, limit=limit)


def read_campus(repo: AcademicRepository, tenant_id: UUID, campus_id: UUID) -> CampusSummary:
    return _require_found(repo.get_campus(tenant_id, campus_id), "campus")


def create_campus(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, body: CampusCreate
) -> CampusSummary:
    campus_id = uuid4()
    created = repo.create_campus(tenant_id, campus_id, body.name)
    _audit(scope, "campus.create", tenant_id, "campus", campus_id)
    return created


def update_campus(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, campus_id: UUID, body: CampusUpdate
) -> CampusSummary:
    _require_fields(set(body.model_fields_set), ("name", body.name))
    updated = _require_found(repo.update_campus(tenant_id, campus_id, name=body.name), "campus")
    _audit(scope, "campus.update", tenant_id, "campus", campus_id)
    return updated


def set_campus_lifecycle(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, campus_id: UUID, body: LifecycleUpdate
) -> CampusSummary:
    return _set_lifecycle(
        scope, tenant_id, "campus", campus_id, body,
        has_children_fn=repo.has_active_campus_children,
        set_active_fn=repo.set_campus_active,
    )


# --- Departments ---
def list_departments(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, *, campus_id: UUID | None, active: bool | None, cursor: UUID | None, limit: int
) -> Page[DepartmentSummary]:
    actor_id = None if _is_admin(scope.context) else scope.context.actor_id
    return repo.list_departments(tenant_id, actor_id=actor_id, campus_id=campus_id, active=active, cursor=cursor, limit=limit)


def read_department(repo: AcademicRepository, tenant_id: UUID, department_id: UUID) -> DepartmentSummary:
    return _require_found(repo.get_department(tenant_id, department_id), "department")


def create_department(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, body: DepartmentCreate
) -> DepartmentSummary:
    if body.campus_id is not None:
        _require_parent_if_present(repo.get_campus(tenant_id, body.campus_id), "campus")
    department_id = uuid4()
    created = repo.create_department(tenant_id, department_id, name=body.name, campus_id=body.campus_id)
    _audit(scope, "department.create", tenant_id, "department", department_id)
    return created


def update_department(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, department_id: UUID, body: DepartmentUpdate
) -> DepartmentSummary:
    fields = set(body.model_fields_set)
    _require_fields(fields, ("name", body.name))
    update_campus = "campus_id" in fields
    if update_campus and body.campus_id is not None:
        _require_active_parent(scope, "campus.read", "campus", tenant_id, body.campus_id, repo.get_campus(tenant_id, body.campus_id))
    updated = _require_found(
        repo.update_department(tenant_id, department_id, name=body.name, campus_id=body.campus_id, update_campus=update_campus),
        "department",
    )
    _audit(scope, "department.update" if update_campus else "department.update_metadata", tenant_id, "department", department_id)
    return updated


def set_department_lifecycle(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, department_id: UUID, body: LifecycleUpdate
) -> DepartmentSummary:
    return _set_lifecycle(
        scope, tenant_id, "department", department_id, body,
        get_fn=repo.get_department,
        has_children_fn=repo.has_active_department_children,
        parent_checks_on_activate=lambda d: ([("campus", repo.get_campus(tenant_id, d.campus_id))] if d.campus_id is not None else []),
        set_active_fn=repo.set_department_active,
    )


# --- Programmes ---
def list_programmes(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, *, department_id: UUID | None, active: bool | None, cursor: UUID | None, limit: int
) -> Page[ProgrammeSummary]:
    actor_id = None if _is_admin(scope.context) else scope.context.actor_id
    return repo.list_programmes(tenant_id, actor_id=actor_id, department_id=department_id, active=active, cursor=cursor, limit=limit)


def read_programme(repo: AcademicRepository, tenant_id: UUID, programme_id: UUID) -> ProgrammeSummary:
    return _require_found(repo.get_programme(tenant_id, programme_id), "programme")


def create_programme(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, body: ProgrammeCreate
) -> ProgrammeSummary:
    return _create_dept_child(scope, repo, tenant_id, "programme", body.name, body.department_id, repo.create_programme)


def update_programme(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, programme_id: UUID, body: ProgrammeUpdate
) -> ProgrammeSummary:
    return _update_dept_child(scope, repo, tenant_id, "programme", programme_id, body, repo.update_programme)


def set_programme_lifecycle(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, programme_id: UUID, body: LifecycleUpdate
) -> ProgrammeSummary:
    return _set_lifecycle(
        scope, tenant_id, "programme", programme_id, body,
        get_fn=repo.get_programme,
        has_children_fn=repo.has_active_programme_children,
        parent_checks_on_activate=lambda p: ([("department", repo.get_department(tenant_id, p.department_id))] if p.department_id is not None else []),
        set_active_fn=repo.set_programme_active,
    )


# --- Academic Years ---
def list_academic_years(
    repo: AcademicRepository, tenant_id: UUID, *, active: bool | None, cursor: UUID | None, limit: int
) -> Page[AcademicYearSummary]:
    return repo.list_academic_years(tenant_id, active=active, cursor=cursor, limit=limit)


def read_academic_year(repo: AcademicRepository, tenant_id: UUID, academic_year_id: UUID) -> AcademicYearSummary:
    return _require_found(repo.get_academic_year(tenant_id, academic_year_id), "academic_year", "Academic year")


def create_academic_year(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, body: AcademicYearCreate
) -> AcademicYearSummary:
    academic_year_id = uuid4()
    created = repo.create_academic_year(tenant_id, academic_year_id, body.name)
    _audit(scope, "academic_year.create", tenant_id, "academic_year", academic_year_id)
    return created


def update_academic_year(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, academic_year_id: UUID, body: AcademicYearUpdate
) -> AcademicYearSummary:
    _require_fields(set(body.model_fields_set), ("name", body.name))
    updated = _require_found(repo.update_academic_year(tenant_id, academic_year_id, name=body.name), "academic_year", "Academic year")
    _audit(scope, "academic_year.update", tenant_id, "academic_year", academic_year_id)
    return updated


def set_academic_year_lifecycle(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, academic_year_id: UUID, body: LifecycleUpdate
) -> AcademicYearSummary:
    return _set_lifecycle(
        scope, tenant_id, "academic_year", academic_year_id, body, label="Academic year",
        has_children_fn=repo.has_active_academic_year_children,
        set_active_fn=repo.set_academic_year_active,
    )


# --- Semesters ---
def list_semesters(
    repo: AcademicRepository, tenant_id: UUID, *, academic_year_id: UUID | None, active: bool | None, cursor: UUID | None, limit: int
) -> Page[SemesterSummary]:
    return repo.list_semesters(tenant_id, academic_year_id=academic_year_id, active=active, cursor=cursor, limit=limit)


def read_semester(repo: AcademicRepository, tenant_id: UUID, semester_id: UUID) -> SemesterSummary:
    return _require_found(repo.get_semester(tenant_id, semester_id), "semester")


def create_semester(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, body: SemesterCreate
) -> SemesterSummary:
    _require_parent_if_present(repo.get_academic_year(tenant_id, body.academic_year_id), "academic_year")
    semester_id = uuid4()
    created = repo.create_semester(tenant_id, semester_id, name=body.name, academic_year_id=body.academic_year_id)
    _audit(scope, "semester.create", tenant_id, "semester", semester_id)
    return created


def update_semester(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, semester_id: UUID, body: SemesterUpdate
) -> SemesterSummary:
    fields = set(body.model_fields_set)
    _require_fields(fields, ("name", body.name), ("academic_year_id", body.academic_year_id))
    if body.academic_year_id is not None:
        _require_active_parent(scope, "academic_year.read", "academic_year", tenant_id, body.academic_year_id, repo.get_academic_year(tenant_id, body.academic_year_id))
    updated = _require_found(repo.update_semester(tenant_id, semester_id, name=body.name, academic_year_id=body.academic_year_id), "semester")
    _audit(scope, "semester.update", tenant_id, "semester", semester_id)
    return updated


def set_semester_lifecycle(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, semester_id: UUID, body: LifecycleUpdate
) -> SemesterSummary:
    return _set_lifecycle(
        scope, tenant_id, "semester", semester_id, body,
        get_fn=repo.get_semester,
        has_children_fn=repo.has_active_semester_children,
        parent_checks_on_activate=lambda s: [("academic_year", repo.get_academic_year(tenant_id, s.academic_year_id))],
        set_active_fn=repo.set_semester_active,
    )


# --- Sections ---
def list_sections(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, *, semester_id: UUID | None, programme_id: UUID | None, active: bool | None, cursor: UUID | None, limit: int
) -> Page[SectionSummary]:
    actor_id = None if _is_admin(scope.context) else scope.context.actor_id
    return repo.list_sections(tenant_id, actor_id=actor_id, semester_id=semester_id, programme_id=programme_id, active=active, cursor=cursor, limit=limit)


def read_section(repo: AcademicRepository, tenant_id: UUID, section_id: UUID) -> SectionSummary:
    return _require_found(repo.get_section(tenant_id, section_id), "section")


def create_section(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, body: SectionCreate
) -> SectionSummary:
    _require_parent_if_present(repo.get_semester(tenant_id, body.semester_id), "semester")
    if body.programme_id is not None:
        _require_active_parent(scope, "programme.read", "programme", tenant_id, body.programme_id, repo.get_programme(tenant_id, body.programme_id))
    section_id = uuid4()
    created = repo.create_section(tenant_id, section_id, name=body.name, semester_id=body.semester_id, programme_id=body.programme_id)
    _audit(scope, "section.create", tenant_id, "section", section_id)
    return created


def update_section(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, section_id: UUID, body: SectionUpdate
) -> SectionSummary:
    fields = set(body.model_fields_set)
    _require_fields(fields, ("name", body.name), ("semester_id", body.semester_id))
    structural = "semester_id" in fields or "programme_id" in fields
    if body.semester_id is not None:
        _require_active_parent(scope, "semester.read", "semester", tenant_id, body.semester_id, repo.get_semester(tenant_id, body.semester_id))
    update_programme = "programme_id" in fields
    if update_programme and body.programme_id is not None:
        _require_active_parent(scope, "programme.read", "programme", tenant_id, body.programme_id, repo.get_programme(tenant_id, body.programme_id))
    updated = _require_found(
        repo.update_section(tenant_id, section_id, name=body.name, semester_id=body.semester_id, programme_id=body.programme_id, update_programme=update_programme),
        "section",
    )
    _audit(scope, "section.update" if structural else "section.update_metadata", tenant_id, "section", section_id)
    return updated


def set_section_lifecycle(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, section_id: UUID, body: LifecycleUpdate
) -> SectionSummary:
    return _set_lifecycle(
        scope, tenant_id, "section", section_id, body,
        get_fn=repo.get_section,
        has_children_fn=repo.has_active_section_children,
        parent_checks_on_activate=lambda s: [
            ("semester", repo.get_semester(tenant_id, s.semester_id)),
            *((("programme", repo.get_programme(tenant_id, s.programme_id)),) if s.programme_id is not None else ()),
        ],
        set_active_fn=repo.set_section_active,
    )


# --- Subjects ---
def list_subjects(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, *, department_id: UUID | None, active: bool | None, cursor: UUID | None, limit: int
) -> Page[SubjectSummary]:
    actor_id = None if _is_admin(scope.context) else scope.context.actor_id
    return repo.list_subjects(tenant_id, actor_id=actor_id, department_id=department_id, active=active, cursor=cursor, limit=limit)


def read_subject(repo: AcademicRepository, tenant_id: UUID, subject_id: UUID) -> SubjectSummary:
    return _require_found(repo.get_subject(tenant_id, subject_id), "subject")


def create_subject(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, body: SubjectCreate
) -> SubjectSummary:
    return _create_dept_child(scope, repo, tenant_id, "subject", body.name, body.department_id, repo.create_subject)


def update_subject(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, subject_id: UUID, body: SubjectUpdate
) -> SubjectSummary:
    return _update_dept_child(scope, repo, tenant_id, "subject", subject_id, body, repo.update_subject)


def set_subject_lifecycle(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, subject_id: UUID, body: LifecycleUpdate
) -> SubjectSummary:
    return _set_lifecycle(
        scope, tenant_id, "subject", subject_id, body,
        get_fn=repo.get_subject,
        has_children_fn=repo.has_active_subject_children,
        parent_checks_on_activate=lambda sub: ([("department", repo.get_department(tenant_id, sub.department_id))] if sub.department_id is not None else []),
        set_active_fn=repo.set_subject_active,
    )


# --- Subject Offerings ---
def list_offerings(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, *, section_id: UUID | None, subject_id: UUID | None, active: bool | None, cursor: UUID | None, limit: int
) -> Page[SubjectOfferingSummary]:
    actor_id = None if _is_admin(scope.context) else scope.context.actor_id
    return repo.list_offerings(tenant_id, actor_id=actor_id, roles=scope.context.roles, section_id=section_id, subject_id=subject_id, active=active, cursor=cursor, limit=limit)


def read_offering(repo: AcademicRepository, tenant_id: UUID, offering_id: UUID) -> SubjectOfferingSummary:
    return _require_found(repo.get_offering(tenant_id, offering_id), "offering", "Subject offering")


def create_offering(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, body: SubjectOfferingCreate
) -> SubjectOfferingSummary:
    _require_parent_if_present(repo.get_section(tenant_id, body.section_id), "section")
    _require_active_parent(scope, "subject.read", "subject", tenant_id, body.subject_id, repo.get_subject(tenant_id, body.subject_id))
    offering_id = uuid4()
    if repo.has_duplicate_offering(tenant_id, section_id=body.section_id, subject_id=body.subject_id, name=body.name):
        raise AccessError(409, "duplicate_record", "A matching academic record already exists")
    created = repo.create_offering(tenant_id, offering_id, name=body.name, section_id=body.section_id, subject_id=body.subject_id)
    _audit(scope, "offering.create", tenant_id, "offering", offering_id)
    return created


def update_offering(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, offering_id: UUID, body: SubjectOfferingUpdate
) -> SubjectOfferingSummary:
    fields = set(body.model_fields_set)
    _require_fields(fields, ("name", body.name), ("section_id", body.section_id), ("subject_id", body.subject_id))
    if body.section_id is not None:
        _require_active_parent(scope, "section.read", "section", tenant_id, body.section_id, repo.get_section(tenant_id, body.section_id))
    if body.subject_id is not None:
        _require_active_parent(scope, "subject.read", "subject", tenant_id, body.subject_id, repo.get_subject(tenant_id, body.subject_id))
    current = _require_found(repo.get_offering(tenant_id, offering_id), "offering", "Subject offering")
    if repo.has_duplicate_offering(
        tenant_id,
        section_id=body.section_id if body.section_id is not None else current.section_id,
        subject_id=body.subject_id if body.subject_id is not None else current.subject_id,
        name=body.name if body.name is not None else current.name,
        exclude_id=offering_id,
    ):
        raise AccessError(409, "duplicate_record", "A matching academic record already exists")
    updated = _require_found(
        repo.update_offering(tenant_id, offering_id, name=body.name, section_id=body.section_id, subject_id=body.subject_id),
        "offering",
        "Subject offering",
    )
    _audit(scope, "offering.update", tenant_id, "offering", offering_id)
    return updated


def set_offering_lifecycle(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, offering_id: UUID, body: LifecycleUpdate
) -> SubjectOfferingSummary:
    return _set_lifecycle(
        scope, tenant_id, "offering", offering_id, body, label="Subject offering",
        get_fn=repo.get_offering,
        parent_checks_on_activate=lambda o: [
            ("section", repo.get_section(tenant_id, o.section_id)),
            ("subject", repo.get_subject(tenant_id, o.subject_id)),
        ],
        set_active_fn=repo.set_offering_active,
    )


# --- Role Assignments ---
def list_department_heads(
    repo: AcademicRepository, tenant_id: UUID, department_id: UUID, *, active: bool | None, cursor: UUID | None, limit: int
) -> Page[DepartmentHeadSummary]:
    return repo.list_department_heads(tenant_id, department_id, active=active, cursor=cursor, limit=limit)


def assign_department_head(
    scope: AuthorizedScope, repo: AcademicRepository, identities: IdentityDirectory, tenant_id: UUID, department_id: UUID, body: AssignmentCreate
) -> DepartmentHeadSummary:
    return _assign_role(scope, identities, tenant_id, "department", department_id, body.user_id, InstitutionRole.department_head, "department.head.assign", repo.assign_department_head)


def revoke_department_head(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, department_id: UUID, user_id: UUID
) -> DepartmentHeadSummary:
    return _revoke_role(scope, tenant_id, "department", department_id, user_id, "department.head.revoke", repo.revoke_department_head)


def list_section_coordinators(
    repo: AcademicRepository, tenant_id: UUID, section_id: UUID, *, active: bool | None, cursor: UUID | None, limit: int
) -> Page[ClassCoordinatorSummary]:
    return repo.list_section_coordinators(tenant_id, section_id, active=active, cursor=cursor, limit=limit)


def assign_section_coordinator(
    scope: AuthorizedScope, repo: AcademicRepository, identities: IdentityDirectory, tenant_id: UUID, section_id: UUID, body: AssignmentCreate
) -> ClassCoordinatorSummary:
    return _assign_role(scope, identities, tenant_id, "section", section_id, body.user_id, InstitutionRole.class_coordinator, "section.coordinator.assign", repo.assign_section_coordinator)


def revoke_section_coordinator(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, section_id: UUID, user_id: UUID
) -> ClassCoordinatorSummary:
    return _revoke_role(scope, tenant_id, "section", section_id, user_id, "section.coordinator.revoke", repo.revoke_section_coordinator)


def list_offering_teachers(
    repo: AcademicRepository, tenant_id: UUID, offering_id: UUID, *, active: bool | None, cursor: UUID | None, limit: int
) -> Page[OfferingAssignmentSummary]:
    return repo.list_offering_teachers(tenant_id, offering_id, active=active, cursor=cursor, limit=limit)


def assign_offering_teacher(
    scope: AuthorizedScope, repo: AcademicRepository, identities: IdentityDirectory, tenant_id: UUID, offering_id: UUID, body: AssignmentCreate
) -> OfferingAssignmentSummary:
    return _assign_role(scope, identities, tenant_id, "offering", offering_id, body.user_id, InstitutionRole.subject_teacher, "offering.teacher.assign", repo.assign_offering_teacher)


def revoke_offering_teacher(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, offering_id: UUID, user_id: UUID
) -> OfferingAssignmentSummary:
    return _revoke_role(scope, tenant_id, "offering", offering_id, user_id, "offering.teacher.revoke", repo.revoke_offering_teacher)


def list_offering_enrolments(
    repo: AcademicRepository, tenant_id: UUID, offering_id: UUID, *, active: bool | None, cursor: UUID | None, limit: int
) -> Page[OfferingAssignmentSummary]:
    return repo.list_offering_enrolments(tenant_id, offering_id, active=active, cursor=cursor, limit=limit)


def enrol_student(
    scope: AuthorizedScope, repo: AcademicRepository, identities: IdentityDirectory, tenant_id: UUID, offering_id: UUID, body: AssignmentCreate
) -> OfferingAssignmentSummary:
    return _assign_role(scope, identities, tenant_id, "offering", offering_id, body.user_id, InstitutionRole.student, "offering.enrolment.create", repo.enrol_student)


def withdraw_student(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, offering_id: UUID, user_id: UUID
) -> OfferingAssignmentSummary:
    return _revoke_role(scope, tenant_id, "offering", offering_id, user_id, "offering.enrolment.withdraw", repo.withdraw_student, "enrolment_missing", "Active enrolment unavailable")


# --- Guardian Relationships & Minimal Student Summary ---
def list_guardian_links(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, *, active: bool | None, cursor: UUID | None, limit: int
) -> Page[GuardianLinkSummary]:
    actor_id = None if _is_admin(scope.context) else scope.context.actor_id
    return repo.list_guardian_links(tenant_id, actor_id=actor_id, active=active, cursor=cursor, limit=limit)


def create_guardian_link(
    scope: AuthorizedScope, repo: AcademicRepository, identities: IdentityDirectory, tenant_id: UUID, body: GuardianLinkCreate
) -> GuardianLinkSummary:
    if body.guardian_user_id == body.student_user_id:
        raise AccessError(422, "invalid_request", "Guardian and student must be distinct users")
    _require_role(identities, tenant_id, body.guardian_user_id, InstitutionRole.guardian)
    _require_role(identities, tenant_id, body.student_user_id, InstitutionRole.student)
    created = repo.create_guardian_link(tenant_id, body.guardian_user_id, body.student_user_id)
    _audit(scope, "guardian_link.create", tenant_id, "guardian_link", body.student_user_id)
    return created


def verify_guardian_link(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
) -> GuardianLinkSummary:
    verified = _require_found(repo.verify_guardian_link(tenant_id, guardian_user_id, student_user_id), "guardian_link", "Guardian link")
    _audit(scope, "guardian_link.verify", tenant_id, "guardian_link", student_user_id)
    return verified


def consent_guardian_link(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
) -> GuardianLinkSummary:
    consented = _require_found(repo.consent_guardian_link(tenant_id, guardian_user_id, student_user_id), "guardian_link", "Guardian link")
    _audit(scope, "guardian_link.consent", tenant_id, "guardian_link", student_user_id)
    return consented


def activate_guardian_link(
    scope: AuthorizedScope, repo: AcademicRepository, identities: IdentityDirectory, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
) -> GuardianLinkSummary:
    _require_role(identities, tenant_id, guardian_user_id, InstitutionRole.guardian)
    _require_role(identities, tenant_id, student_user_id, InstitutionRole.student)
    activated = _require_found(repo.activate_guardian_link(tenant_id, guardian_user_id, student_user_id), "guardian_link", "Guardian link")
    _audit(scope, "guardian_link.activate", tenant_id, "guardian_link", student_user_id)
    return activated


def revoke_guardian_link(
    scope: AuthorizedScope, repo: AcademicRepository, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
) -> GuardianLinkSummary:
    if not _is_admin(scope.context) and scope.context.actor_id not in {guardian_user_id, student_user_id}:
        raise AccessError(403, "forbidden", "Access denied")
    revoked = _require_found(repo.revoke_guardian_link(tenant_id, guardian_user_id, student_user_id), "guardian_link", "Guardian link")
    _audit(scope, "guardian_link.revoke", tenant_id, "guardian_link", student_user_id)
    return revoked


def read_student_summary(identities: IdentityDirectory, tenant_id: UUID, student_id: UUID) -> StudentSummary:
    if InstitutionRole.student not in identities.applicable_roles(student_id, tenant_id):
        raise AccessError(404, "student_missing", "Student summary unavailable")
    user = identities.user(student_id)
    if user is None:
        raise AccessError(404, "student_missing", "Student summary unavailable")
    return StudentSummary(tenant_id=tenant_id, user_id=user.user_id, display_name=user.display_name)
