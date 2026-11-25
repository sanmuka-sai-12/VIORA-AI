"""Application services for institution metadata and academic hierarchy management."""

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

ADMIN_ROLES = frozenset({"institution_owner", "institution_admin"})


def _is_admin(context: TenantContext) -> bool:
    return bool(context.roles & ADMIN_ROLES)


def _require_fields(fields_set: set[str]) -> None:
    if not fields_set:
        raise AccessError(422, "invalid_request", "At least one field must be provided")


def _require_non_null(fields_set: set[str], field: str, value: UUID | None) -> None:
    if field in fields_set and value is None:
        raise AccessError(422, "invalid_request", f"{field} cannot be null")


def _require_active_parent(
    scope: AuthorizedScope,
    action: str,
    kind: str,
    tenant_id: UUID,
    parent_id: UUID,
    active: bool | None,
) -> None:
    scope.recheck(
        action, ResourceRef(tenant_id=tenant_id, kind=kind, resource_id=parent_id)
    )
    if active is None:
        raise AccessError(404, f"{kind}_missing", f"{kind} unavailable")
    if not active:
        raise AccessError(409, "inactive_parent", f"Referenced {kind} is archived")


def _ensure_no_active_children(has_children: bool, kind: str) -> None:
    if has_children:
        raise AccessError(
            409,
            "active_dependents",
            f"Archive active dependent records before archiving this {kind}",
        )


def read_institution(repo: AcademicRepository, tenant_id: UUID) -> InstitutionInfo:
    info = repo.get_institution(tenant_id)
    if info is None:
        raise AccessError(404, "institution_missing", "Institution unavailable")
    return info


def update_institution(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    body: InstitutionUpdate,
) -> InstitutionInfo:
    updated = repo.update_institution(tenant_id, name=body.name)
    if updated is None:
        raise AccessError(404, "institution_missing", "Institution unavailable")
    scope.audit.change(
        scope.context,
        "institution.update_metadata",
        ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id),
    )
    return updated


def list_campuses(
    repo: AcademicRepository,
    tenant_id: UUID,
    *,
    active: bool | None,
    cursor: UUID | None,
    limit: int,
) -> Page[CampusSummary]:
    return repo.list_campuses(tenant_id, active=active, cursor=cursor, limit=limit)


def read_campus(
    repo: AcademicRepository, tenant_id: UUID, campus_id: UUID
) -> CampusSummary:
    row = repo.get_campus(tenant_id, campus_id)
    if row is None:
        raise AccessError(404, "campus_missing", "Campus unavailable")
    return row


def create_campus(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    body: CampusCreate,
) -> CampusSummary:
    campus_id = uuid4()
    created = repo.create_campus(tenant_id, campus_id, body.name)
    scope.audit.change(
        scope.context,
        "campus.create",
        ResourceRef(tenant_id=tenant_id, kind="campus", resource_id=campus_id),
    )
    return created


def update_campus(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    campus_id: UUID,
    body: CampusUpdate,
) -> CampusSummary:
    _require_fields(set(body.model_fields_set))
    _require_non_null(
        set(body.model_fields_set), "name", None if body.name is None else campus_id
    )
    updated = repo.update_campus(tenant_id, campus_id, name=body.name)
    if updated is None:
        raise AccessError(404, "campus_missing", "Campus unavailable")
    scope.audit.change(
        scope.context,
        "campus.update",
        ResourceRef(tenant_id=tenant_id, kind="campus", resource_id=campus_id),
    )
    return updated


def set_campus_lifecycle(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    campus_id: UUID,
    body: LifecycleUpdate,
) -> CampusSummary:
    if not body.active:
        _ensure_no_active_children(
            repo.has_active_campus_children(tenant_id, campus_id), "campus"
        )
    updated = repo.set_campus_active(tenant_id, campus_id, body.active)
    if updated is None:
        raise AccessError(404, "campus_missing", "Campus unavailable")
    scope.audit.change(
        scope.context,
        "campus.lifecycle",
        ResourceRef(tenant_id=tenant_id, kind="campus", resource_id=campus_id),
    )
    return updated


def list_departments(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    *,
    campus_id: UUID | None,
    active: bool | None,
    cursor: UUID | None,
    limit: int,
) -> Page[DepartmentSummary]:
    actor_id = None if _is_admin(scope.context) else scope.context.actor_id
    return repo.list_departments(
        tenant_id,
        actor_id=actor_id,
        campus_id=campus_id,
        active=active,
        cursor=cursor,
        limit=limit,
    )


def read_department(
    repo: AcademicRepository, tenant_id: UUID, department_id: UUID
) -> DepartmentSummary:
    row = repo.get_department(tenant_id, department_id)
    if row is None:
        raise AccessError(404, "department_missing", "Department unavailable")
    return row


def create_department(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    body: DepartmentCreate,
) -> DepartmentSummary:
    if body.campus_id is not None:
        campus = repo.get_campus(tenant_id, body.campus_id)
        if campus is None or not campus.active:
            raise AccessError(409, "inactive_parent", "Referenced campus is archived")
    department_id = uuid4()
    created = repo.create_department(
        tenant_id, department_id, name=body.name, campus_id=body.campus_id
    )
    scope.audit.change(
        scope.context,
        "department.create",
        ResourceRef(tenant_id=tenant_id, kind="department", resource_id=department_id),
    )
    return created


def update_department(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    department_id: UUID,
    body: DepartmentUpdate,
) -> DepartmentSummary:
    fields = set(body.model_fields_set)
    _require_fields(fields)
    if "name" in fields and body.name is None:
        raise AccessError(422, "invalid_request", "name cannot be null")
    update_campus = "campus_id" in fields
    if update_campus and body.campus_id is not None:
        campus = repo.get_campus(tenant_id, body.campus_id)
        _require_active_parent(
            scope,
            "campus.read",
            "campus",
            tenant_id,
            body.campus_id,
            campus.active if campus else None,
        )
    updated = repo.update_department(
        tenant_id,
        department_id,
        name=body.name,
        campus_id=body.campus_id,
        update_campus=update_campus,
    )
    if updated is None:
        raise AccessError(404, "department_missing", "Department unavailable")
    scope.audit.change(
        scope.context,
        "department.update" if update_campus else "department.update_metadata",
        ResourceRef(tenant_id=tenant_id, kind="department", resource_id=department_id),
    )
    return updated


def set_department_lifecycle(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    department_id: UUID,
    body: LifecycleUpdate,
) -> DepartmentSummary:
    current = repo.get_department(tenant_id, department_id)
    if current is None:
        raise AccessError(404, "department_missing", "Department unavailable")
    if not body.active:
        _ensure_no_active_children(
            repo.has_active_department_children(tenant_id, department_id), "department"
        )
    elif current.campus_id is not None:
        campus = repo.get_campus(tenant_id, current.campus_id)
        if campus is None or not campus.active:
            raise AccessError(409, "inactive_parent", "Referenced campus is archived")
    updated = repo.set_department_active(tenant_id, department_id, body.active)
    assert updated is not None
    scope.audit.change(
        scope.context,
        "department.lifecycle",
        ResourceRef(tenant_id=tenant_id, kind="department", resource_id=department_id),
    )
    return updated


def list_programmes(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    *,
    department_id: UUID | None,
    active: bool | None,
    cursor: UUID | None,
    limit: int,
) -> Page[ProgrammeSummary]:
    actor_id = None if _is_admin(scope.context) else scope.context.actor_id
    return repo.list_programmes(
        tenant_id,
        actor_id=actor_id,
        department_id=department_id,
        active=active,
        cursor=cursor,
        limit=limit,
    )


def read_programme(
    repo: AcademicRepository, tenant_id: UUID, programme_id: UUID
) -> ProgrammeSummary:
    row = repo.get_programme(tenant_id, programme_id)
    if row is None:
        raise AccessError(404, "programme_missing", "Programme unavailable")
    return row


def create_programme(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    body: ProgrammeCreate,
) -> ProgrammeSummary:
    if body.department_id is not None:
        dept = repo.get_department(tenant_id, body.department_id)
        if dept is None or not dept.active:
            raise AccessError(
                409, "inactive_parent", "Referenced department is archived"
            )
    programme_id = uuid4()
    created = repo.create_programme(
        tenant_id, programme_id, name=body.name, department_id=body.department_id
    )
    scope.audit.change(
        scope.context,
        "programme.create",
        ResourceRef(tenant_id=tenant_id, kind="programme", resource_id=programme_id),
    )
    return created


def update_programme(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    programme_id: UUID,
    body: ProgrammeUpdate,
) -> ProgrammeSummary:
    fields = set(body.model_fields_set)
    _require_fields(fields)
    if "name" in fields and body.name is None:
        raise AccessError(422, "invalid_request", "name cannot be null")
    update_dept = "department_id" in fields
    if update_dept and body.department_id is not None:
        dept = repo.get_department(tenant_id, body.department_id)
        _require_active_parent(
            scope,
            "department.read",
            "department",
            tenant_id,
            body.department_id,
            dept.active if dept else None,
        )
    updated = repo.update_programme(
        tenant_id,
        programme_id,
        name=body.name,
        department_id=body.department_id,
        update_department=update_dept,
    )
    if updated is None:
        raise AccessError(404, "programme_missing", "Programme unavailable")
    scope.audit.change(
        scope.context,
        "programme.update",
        ResourceRef(tenant_id=tenant_id, kind="programme", resource_id=programme_id),
    )
    return updated


def set_programme_lifecycle(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    programme_id: UUID,
    body: LifecycleUpdate,
) -> ProgrammeSummary:
    current = repo.get_programme(tenant_id, programme_id)
    if current is None:
        raise AccessError(404, "programme_missing", "Programme unavailable")
    if not body.active:
        _ensure_no_active_children(
            repo.has_active_programme_children(tenant_id, programme_id), "programme"
        )
    elif current.department_id is not None:
        dept = repo.get_department(tenant_id, current.department_id)
        if dept is None or not dept.active:
            raise AccessError(
                409, "inactive_parent", "Referenced department is archived"
            )
    updated = repo.set_programme_active(tenant_id, programme_id, body.active)
    assert updated is not None
    scope.audit.change(
        scope.context,
        "programme.lifecycle",
        ResourceRef(tenant_id=tenant_id, kind="programme", resource_id=programme_id),
    )
    return updated


def list_academic_years(
    repo: AcademicRepository,
    tenant_id: UUID,
    *,
    active: bool | None,
    cursor: UUID | None,
    limit: int,
) -> Page[AcademicYearSummary]:
    return repo.list_academic_years(
        tenant_id, active=active, cursor=cursor, limit=limit
    )


def read_academic_year(
    repo: AcademicRepository, tenant_id: UUID, academic_year_id: UUID
) -> AcademicYearSummary:
    row = repo.get_academic_year(tenant_id, academic_year_id)
    if row is None:
        raise AccessError(404, "academic_year_missing", "Academic year unavailable")
    return row


def create_academic_year(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    body: AcademicYearCreate,
) -> AcademicYearSummary:
    academic_year_id = uuid4()
    created = repo.create_academic_year(tenant_id, academic_year_id, body.name)
    scope.audit.change(
        scope.context,
        "academic_year.create",
        ResourceRef(
            tenant_id=tenant_id, kind="academic_year", resource_id=academic_year_id
        ),
    )
    return created


def update_academic_year(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    academic_year_id: UUID,
    body: AcademicYearUpdate,
) -> AcademicYearSummary:
    _require_fields(set(body.model_fields_set))
    if "name" in body.model_fields_set and body.name is None:
        raise AccessError(422, "invalid_request", "name cannot be null")
    updated = repo.update_academic_year(tenant_id, academic_year_id, name=body.name)
    if updated is None:
        raise AccessError(404, "academic_year_missing", "Academic year unavailable")
    scope.audit.change(
        scope.context,
        "academic_year.update",
        ResourceRef(
            tenant_id=tenant_id, kind="academic_year", resource_id=academic_year_id
        ),
    )
    return updated


def set_academic_year_lifecycle(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    academic_year_id: UUID,
    body: LifecycleUpdate,
) -> AcademicYearSummary:
    if not body.active:
        _ensure_no_active_children(
            repo.has_active_academic_year_children(tenant_id, academic_year_id),
            "academic_year",
        )
    updated = repo.set_academic_year_active(tenant_id, academic_year_id, body.active)
    if updated is None:
        raise AccessError(404, "academic_year_missing", "Academic year unavailable")
    scope.audit.change(
        scope.context,
        "academic_year.lifecycle",
        ResourceRef(
            tenant_id=tenant_id, kind="academic_year", resource_id=academic_year_id
        ),
    )
    return updated


def list_semesters(
    repo: AcademicRepository,
    tenant_id: UUID,
    *,
    academic_year_id: UUID | None,
    active: bool | None,
    cursor: UUID | None,
    limit: int,
) -> Page[SemesterSummary]:
    return repo.list_semesters(
        tenant_id,
        academic_year_id=academic_year_id,
        active=active,
        cursor=cursor,
        limit=limit,
    )


def read_semester(
    repo: AcademicRepository, tenant_id: UUID, semester_id: UUID
) -> SemesterSummary:
    row = repo.get_semester(tenant_id, semester_id)
    if row is None:
        raise AccessError(404, "semester_missing", "Semester unavailable")
    return row


def create_semester(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    body: SemesterCreate,
) -> SemesterSummary:
    year = repo.get_academic_year(tenant_id, body.academic_year_id)
    if year is None or not year.active:
        raise AccessError(
            409, "inactive_parent", "Referenced academic_year is archived"
        )
    semester_id = uuid4()
    created = repo.create_semester(
        tenant_id, semester_id, name=body.name, academic_year_id=body.academic_year_id
    )
    scope.audit.change(
        scope.context,
        "semester.create",
        ResourceRef(tenant_id=tenant_id, kind="semester", resource_id=semester_id),
    )
    return created


def update_semester(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    semester_id: UUID,
    body: SemesterUpdate,
) -> SemesterSummary:
    fields = set(body.model_fields_set)
    _require_fields(fields)
    if "name" in fields and body.name is None:
        raise AccessError(422, "invalid_request", "name cannot be null")
    _require_non_null(fields, "academic_year_id", body.academic_year_id)
    if body.academic_year_id is not None:
        year = repo.get_academic_year(tenant_id, body.academic_year_id)
        _require_active_parent(
            scope,
            "academic_year.read",
            "academic_year",
            tenant_id,
            body.academic_year_id,
            year.active if year else None,
        )
    updated = repo.update_semester(
        tenant_id,
        semester_id,
        name=body.name,
        academic_year_id=body.academic_year_id,
    )
    if updated is None:
        raise AccessError(404, "semester_missing", "Semester unavailable")
    scope.audit.change(
        scope.context,
        "semester.update",
        ResourceRef(tenant_id=tenant_id, kind="semester", resource_id=semester_id),
    )
    return updated


def set_semester_lifecycle(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    semester_id: UUID,
    body: LifecycleUpdate,
) -> SemesterSummary:
    current = repo.get_semester(tenant_id, semester_id)
    if current is None:
        raise AccessError(404, "semester_missing", "Semester unavailable")
    if not body.active:
        _ensure_no_active_children(
            repo.has_active_semester_children(tenant_id, semester_id), "semester"
        )
    else:
        year = repo.get_academic_year(tenant_id, current.academic_year_id)
        if year is None or not year.active:
            raise AccessError(
                409, "inactive_parent", "Referenced academic_year is archived"
            )
    updated = repo.set_semester_active(tenant_id, semester_id, body.active)
    assert updated is not None
    scope.audit.change(
        scope.context,
        "semester.lifecycle",
        ResourceRef(tenant_id=tenant_id, kind="semester", resource_id=semester_id),
    )
    return updated


def list_sections(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    *,
    semester_id: UUID | None,
    programme_id: UUID | None,
    active: bool | None,
    cursor: UUID | None,
    limit: int,
) -> Page[SectionSummary]:
    actor_id = None if _is_admin(scope.context) else scope.context.actor_id
    return repo.list_sections(
        tenant_id,
        actor_id=actor_id,
        semester_id=semester_id,
        programme_id=programme_id,
        active=active,
        cursor=cursor,
        limit=limit,
    )


def read_section(
    repo: AcademicRepository, tenant_id: UUID, section_id: UUID
) -> SectionSummary:
    row = repo.get_section(tenant_id, section_id)
    if row is None:
        raise AccessError(404, "section_missing", "Section unavailable")
    return row


def create_section(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    body: SectionCreate,
) -> SectionSummary:
    semester = repo.get_semester(tenant_id, body.semester_id)
    if semester is None or not semester.active:
        raise AccessError(409, "inactive_parent", "Referenced semester is archived")
    if body.programme_id is not None:
        programme = repo.get_programme(tenant_id, body.programme_id)
        _require_active_parent(
            scope,
            "programme.read",
            "programme",
            tenant_id,
            body.programme_id,
            programme.active if programme else None,
        )
    section_id = uuid4()
    created = repo.create_section(
        tenant_id,
        section_id,
        name=body.name,
        semester_id=body.semester_id,
        programme_id=body.programme_id,
    )
    scope.audit.change(
        scope.context,
        "section.create",
        ResourceRef(tenant_id=tenant_id, kind="section", resource_id=section_id),
    )
    return created


def update_section(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    section_id: UUID,
    body: SectionUpdate,
) -> SectionSummary:
    fields = set(body.model_fields_set)
    _require_fields(fields)
    if "name" in fields and body.name is None:
        raise AccessError(422, "invalid_request", "name cannot be null")
    _require_non_null(fields, "semester_id", body.semester_id)
    structural = "semester_id" in fields or "programme_id" in fields
    if body.semester_id is not None:
        semester = repo.get_semester(tenant_id, body.semester_id)
        _require_active_parent(
            scope,
            "semester.read",
            "semester",
            tenant_id,
            body.semester_id,
            semester.active if semester else None,
        )
    update_programme = "programme_id" in fields
    if update_programme and body.programme_id is not None:
        programme = repo.get_programme(tenant_id, body.programme_id)
        _require_active_parent(
            scope,
            "programme.read",
            "programme",
            tenant_id,
            body.programme_id,
            programme.active if programme else None,
        )
    updated = repo.update_section(
        tenant_id,
        section_id,
        name=body.name,
        semester_id=body.semester_id,
        programme_id=body.programme_id,
        update_programme=update_programme,
    )
    if updated is None:
        raise AccessError(404, "section_missing", "Section unavailable")
    scope.audit.change(
        scope.context,
        "section.update" if structural else "section.update_metadata",
        ResourceRef(tenant_id=tenant_id, kind="section", resource_id=section_id),
    )
    return updated


def set_section_lifecycle(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    section_id: UUID,
    body: LifecycleUpdate,
) -> SectionSummary:
    current = repo.get_section(tenant_id, section_id)
    if current is None:
        raise AccessError(404, "section_missing", "Section unavailable")
    if not body.active:
        _ensure_no_active_children(
            repo.has_active_section_children(tenant_id, section_id), "section"
        )
    else:
        semester = repo.get_semester(tenant_id, current.semester_id)
        if semester is None or not semester.active:
            raise AccessError(409, "inactive_parent", "Referenced semester is archived")
        if current.programme_id is not None:
            programme = repo.get_programme(tenant_id, current.programme_id)
            if programme is None or not programme.active:
                raise AccessError(
                    409, "inactive_parent", "Referenced programme is archived"
                )
    updated = repo.set_section_active(tenant_id, section_id, body.active)
    assert updated is not None
    scope.audit.change(
        scope.context,
        "section.lifecycle",
        ResourceRef(tenant_id=tenant_id, kind="section", resource_id=section_id),
    )
    return updated


def list_subjects(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    *,
    department_id: UUID | None,
    active: bool | None,
    cursor: UUID | None,
    limit: int,
) -> Page[SubjectSummary]:
    actor_id = None if _is_admin(scope.context) else scope.context.actor_id
    return repo.list_subjects(
        tenant_id,
        actor_id=actor_id,
        department_id=department_id,
        active=active,
        cursor=cursor,
        limit=limit,
    )


def read_subject(
    repo: AcademicRepository, tenant_id: UUID, subject_id: UUID
) -> SubjectSummary:
    row = repo.get_subject(tenant_id, subject_id)
    if row is None:
        raise AccessError(404, "subject_missing", "Subject unavailable")
    return row


def create_subject(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    body: SubjectCreate,
) -> SubjectSummary:
    if body.department_id is not None:
        dept = repo.get_department(tenant_id, body.department_id)
        if dept is None or not dept.active:
            raise AccessError(
                409, "inactive_parent", "Referenced department is archived"
            )
    subject_id = uuid4()
    created = repo.create_subject(
        tenant_id, subject_id, name=body.name, department_id=body.department_id
    )
    scope.audit.change(
        scope.context,
        "subject.create",
        ResourceRef(tenant_id=tenant_id, kind="subject", resource_id=subject_id),
    )
    return created


def update_subject(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    subject_id: UUID,
    body: SubjectUpdate,
) -> SubjectSummary:
    fields = set(body.model_fields_set)
    _require_fields(fields)
    if "name" in fields and body.name is None:
        raise AccessError(422, "invalid_request", "name cannot be null")
    update_dept = "department_id" in fields
    if update_dept and body.department_id is not None:
        dept = repo.get_department(tenant_id, body.department_id)
        _require_active_parent(
            scope,
            "department.read",
            "department",
            tenant_id,
            body.department_id,
            dept.active if dept else None,
        )
    updated = repo.update_subject(
        tenant_id,
        subject_id,
        name=body.name,
        department_id=body.department_id,
        update_department=update_dept,
    )
    if updated is None:
        raise AccessError(404, "subject_missing", "Subject unavailable")
    scope.audit.change(
        scope.context,
        "subject.update",
        ResourceRef(tenant_id=tenant_id, kind="subject", resource_id=subject_id),
    )
    return updated


def set_subject_lifecycle(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    subject_id: UUID,
    body: LifecycleUpdate,
) -> SubjectSummary:
    current = repo.get_subject(tenant_id, subject_id)
    if current is None:
        raise AccessError(404, "subject_missing", "Subject unavailable")
    if not body.active:
        _ensure_no_active_children(
            repo.has_active_subject_children(tenant_id, subject_id), "subject"
        )
    elif current.department_id is not None:
        dept = repo.get_department(tenant_id, current.department_id)
        if dept is None or not dept.active:
            raise AccessError(
                409, "inactive_parent", "Referenced department is archived"
            )
    updated = repo.set_subject_active(tenant_id, subject_id, body.active)
    assert updated is not None
    scope.audit.change(
        scope.context,
        "subject.lifecycle",
        ResourceRef(tenant_id=tenant_id, kind="subject", resource_id=subject_id),
    )
    return updated


def list_offerings(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    *,
    section_id: UUID | None,
    subject_id: UUID | None,
    active: bool | None,
    cursor: UUID | None,
    limit: int,
) -> Page[SubjectOfferingSummary]:
    actor_id = None if _is_admin(scope.context) else scope.context.actor_id
    return repo.list_offerings(
        tenant_id,
        actor_id=actor_id,
        roles=scope.context.roles,
        section_id=section_id,
        subject_id=subject_id,
        active=active,
        cursor=cursor,
        limit=limit,
    )


def read_offering(
    repo: AcademicRepository, tenant_id: UUID, offering_id: UUID
) -> SubjectOfferingSummary:
    row = repo.get_offering(tenant_id, offering_id)
    if row is None:
        raise AccessError(404, "offering_missing", "Subject offering unavailable")
    return row


def create_offering(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    body: SubjectOfferingCreate,
) -> SubjectOfferingSummary:
    section = repo.get_section(tenant_id, body.section_id)
    if section is None or not section.active:
        raise AccessError(409, "inactive_parent", "Referenced section is archived")
    subject = repo.get_subject(tenant_id, body.subject_id)
    _require_active_parent(
        scope,
        "subject.read",
        "subject",
        tenant_id,
        body.subject_id,
        subject.active if subject else None,
    )
    offering_id = uuid4()
    if repo.has_duplicate_offering(
        tenant_id,
        section_id=body.section_id,
        subject_id=body.subject_id,
        name=body.name,
    ):
        raise AccessError(
            409, "duplicate_record", "A matching academic record already exists"
        )
    created = repo.create_offering(
        tenant_id,
        offering_id,
        name=body.name,
        section_id=body.section_id,
        subject_id=body.subject_id,
    )
    scope.audit.change(
        scope.context,
        "offering.create",
        ResourceRef(tenant_id=tenant_id, kind="offering", resource_id=offering_id),
    )
    return created


def update_offering(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    offering_id: UUID,
    body: SubjectOfferingUpdate,
) -> SubjectOfferingSummary:
    fields = set(body.model_fields_set)
    _require_fields(fields)
    if "name" in fields and body.name is None:
        raise AccessError(422, "invalid_request", "name cannot be null")
    _require_non_null(fields, "section_id", body.section_id)
    _require_non_null(fields, "subject_id", body.subject_id)
    if body.section_id is not None:
        section = repo.get_section(tenant_id, body.section_id)
        _require_active_parent(
            scope,
            "section.read",
            "section",
            tenant_id,
            body.section_id,
            section.active if section else None,
        )
    if body.subject_id is not None:
        subject = repo.get_subject(tenant_id, body.subject_id)
        _require_active_parent(
            scope,
            "subject.read",
            "subject",
            tenant_id,
            body.subject_id,
            subject.active if subject else None,
        )
    current = repo.get_offering(tenant_id, offering_id)
    if current is None:
        raise AccessError(404, "offering_missing", "Subject offering unavailable")
    target_name = body.name if body.name is not None else current.name
    target_section = (
        body.section_id if body.section_id is not None else current.section_id
    )
    target_subject = (
        body.subject_id if body.subject_id is not None else current.subject_id
    )
    if repo.has_duplicate_offering(
        tenant_id,
        section_id=target_section,
        subject_id=target_subject,
        name=target_name,
        exclude_id=offering_id,
    ):
        raise AccessError(
            409, "duplicate_record", "A matching academic record already exists"
        )
    updated = repo.update_offering(
        tenant_id,
        offering_id,
        name=body.name,
        section_id=body.section_id,
        subject_id=body.subject_id,
    )
    if updated is None:
        raise AccessError(404, "offering_missing", "Subject offering unavailable")
    scope.audit.change(
        scope.context,
        "offering.update",
        ResourceRef(tenant_id=tenant_id, kind="offering", resource_id=offering_id),
    )
    return updated


def set_offering_lifecycle(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    offering_id: UUID,
    body: LifecycleUpdate,
) -> SubjectOfferingSummary:
    current = repo.get_offering(tenant_id, offering_id)
    if current is None:
        raise AccessError(404, "offering_missing", "Subject offering unavailable")
    if body.active:
        section = repo.get_section(tenant_id, current.section_id)
        if section is None or not section.active:
            raise AccessError(409, "inactive_parent", "Referenced section is archived")
        subject = repo.get_subject(tenant_id, current.subject_id)
        if subject is None or not subject.active:
            raise AccessError(409, "inactive_parent", "Referenced subject is archived")
    updated = repo.set_offering_active(tenant_id, offering_id, body.active)
    assert updated is not None
    scope.audit.change(
        scope.context,
        "offering.lifecycle",
        ResourceRef(tenant_id=tenant_id, kind="offering", resource_id=offering_id),
    )
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


def list_department_heads(
    repo: AcademicRepository,
    tenant_id: UUID,
    department_id: UUID,
    *,
    active: bool | None,
    cursor: UUID | None,
    limit: int,
) -> Page[DepartmentHeadSummary]:
    return repo.list_department_heads(
        tenant_id, department_id, active=active, cursor=cursor, limit=limit
    )


def assign_department_head(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    identities: IdentityDirectory,
    tenant_id: UUID,
    department_id: UUID,
    body: AssignmentCreate,
) -> DepartmentHeadSummary:
    _require_role(identities, tenant_id, body.user_id, InstitutionRole.department_head)
    created = repo.assign_department_head(tenant_id, department_id, body.user_id)
    scope.audit.change(
        scope.context,
        "department.head.assign",
        ResourceRef(tenant_id=tenant_id, kind="department", resource_id=department_id),
    )
    return created


def revoke_department_head(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    department_id: UUID,
    user_id: UUID,
) -> DepartmentHeadSummary:
    revoked = repo.revoke_department_head(tenant_id, department_id, user_id)
    if revoked is None:
        raise AccessError(404, "assignment_missing", "Active assignment unavailable")
    scope.audit.change(
        scope.context,
        "department.head.revoke",
        ResourceRef(tenant_id=tenant_id, kind="department", resource_id=department_id),
    )
    return revoked


def list_section_coordinators(
    repo: AcademicRepository,
    tenant_id: UUID,
    section_id: UUID,
    *,
    active: bool | None,
    cursor: UUID | None,
    limit: int,
) -> Page[ClassCoordinatorSummary]:
    return repo.list_section_coordinators(
        tenant_id, section_id, active=active, cursor=cursor, limit=limit
    )


def assign_section_coordinator(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    identities: IdentityDirectory,
    tenant_id: UUID,
    section_id: UUID,
    body: AssignmentCreate,
) -> ClassCoordinatorSummary:
    _require_role(
        identities, tenant_id, body.user_id, InstitutionRole.class_coordinator
    )
    created = repo.assign_section_coordinator(tenant_id, section_id, body.user_id)
    scope.audit.change(
        scope.context,
        "section.coordinator.assign",
        ResourceRef(tenant_id=tenant_id, kind="section", resource_id=section_id),
    )
    return created


def revoke_section_coordinator(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    section_id: UUID,
    user_id: UUID,
) -> ClassCoordinatorSummary:
    revoked = repo.revoke_section_coordinator(tenant_id, section_id, user_id)
    if revoked is None:
        raise AccessError(404, "assignment_missing", "Active assignment unavailable")
    scope.audit.change(
        scope.context,
        "section.coordinator.revoke",
        ResourceRef(tenant_id=tenant_id, kind="section", resource_id=section_id),
    )
    return revoked


def list_offering_teachers(
    repo: AcademicRepository,
    tenant_id: UUID,
    offering_id: UUID,
    *,
    active: bool | None,
    cursor: UUID | None,
    limit: int,
) -> Page[OfferingAssignmentSummary]:
    return repo.list_offering_teachers(
        tenant_id, offering_id, active=active, cursor=cursor, limit=limit
    )


def assign_offering_teacher(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    identities: IdentityDirectory,
    tenant_id: UUID,
    offering_id: UUID,
    body: AssignmentCreate,
) -> OfferingAssignmentSummary:
    _require_role(identities, tenant_id, body.user_id, InstitutionRole.subject_teacher)
    created = repo.assign_offering_teacher(tenant_id, offering_id, body.user_id)
    scope.audit.change(
        scope.context,
        "offering.teacher.assign",
        ResourceRef(tenant_id=tenant_id, kind="offering", resource_id=offering_id),
    )
    return created


def revoke_offering_teacher(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    offering_id: UUID,
    user_id: UUID,
) -> OfferingAssignmentSummary:
    revoked = repo.revoke_offering_teacher(tenant_id, offering_id, user_id)
    if revoked is None:
        raise AccessError(404, "assignment_missing", "Active assignment unavailable")
    scope.audit.change(
        scope.context,
        "offering.teacher.revoke",
        ResourceRef(tenant_id=tenant_id, kind="offering", resource_id=offering_id),
    )
    return revoked


def list_offering_enrolments(
    repo: AcademicRepository,
    tenant_id: UUID,
    offering_id: UUID,
    *,
    active: bool | None,
    cursor: UUID | None,
    limit: int,
) -> Page[OfferingAssignmentSummary]:
    return repo.list_offering_enrolments(
        tenant_id, offering_id, active=active, cursor=cursor, limit=limit
    )


def enrol_student(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    identities: IdentityDirectory,
    tenant_id: UUID,
    offering_id: UUID,
    body: AssignmentCreate,
) -> OfferingAssignmentSummary:
    _require_role(identities, tenant_id, body.user_id, InstitutionRole.student)
    created = repo.enrol_student(tenant_id, offering_id, body.user_id)
    scope.audit.change(
        scope.context,
        "offering.enrolment.create",
        ResourceRef(tenant_id=tenant_id, kind="offering", resource_id=offering_id),
    )
    return created


def withdraw_student(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    offering_id: UUID,
    user_id: UUID,
) -> OfferingAssignmentSummary:
    withdrawn = repo.withdraw_student(tenant_id, offering_id, user_id)
    if withdrawn is None:
        raise AccessError(404, "enrolment_missing", "Active enrolment unavailable")
    scope.audit.change(
        scope.context,
        "offering.enrolment.withdraw",
        ResourceRef(tenant_id=tenant_id, kind="offering", resource_id=offering_id),
    )
    return withdrawn


def list_guardian_links(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    *,
    active: bool | None,
    cursor: UUID | None,
    limit: int,
) -> Page[GuardianLinkSummary]:
    actor_id = None if _is_admin(scope.context) else scope.context.actor_id
    return repo.list_guardian_links(
        tenant_id, actor_id=actor_id, active=active, cursor=cursor, limit=limit
    )


def create_guardian_link(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    identities: IdentityDirectory,
    tenant_id: UUID,
    body: GuardianLinkCreate,
) -> GuardianLinkSummary:
    if body.guardian_user_id == body.student_user_id:
        raise AccessError(
            422, "invalid_request", "Guardian and student must be distinct users"
        )
    _require_role(
        identities, tenant_id, body.guardian_user_id, InstitutionRole.guardian
    )
    _require_role(identities, tenant_id, body.student_user_id, InstitutionRole.student)
    created = repo.create_guardian_link(
        tenant_id, body.guardian_user_id, body.student_user_id
    )
    scope.audit.change(
        scope.context,
        "guardian_link.create",
        ResourceRef(
            tenant_id=tenant_id, kind="guardian_link", resource_id=body.student_user_id
        ),
    )
    return created


def verify_guardian_link(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    guardian_user_id: UUID,
    student_user_id: UUID,
) -> GuardianLinkSummary:
    verified = repo.verify_guardian_link(tenant_id, guardian_user_id, student_user_id)
    if verified is None:
        raise AccessError(404, "guardian_link_missing", "Guardian link unavailable")
    scope.audit.change(
        scope.context,
        "guardian_link.verify",
        ResourceRef(
            tenant_id=tenant_id, kind="guardian_link", resource_id=student_user_id
        ),
    )
    return verified


def consent_guardian_link(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    guardian_user_id: UUID,
    student_user_id: UUID,
) -> GuardianLinkSummary:
    consented = repo.consent_guardian_link(tenant_id, guardian_user_id, student_user_id)
    if consented is None:
        raise AccessError(404, "guardian_link_missing", "Guardian link unavailable")
    scope.audit.change(
        scope.context,
        "guardian_link.consent",
        ResourceRef(
            tenant_id=tenant_id, kind="guardian_link", resource_id=student_user_id
        ),
    )
    return consented


def activate_guardian_link(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    identities: IdentityDirectory,
    tenant_id: UUID,
    guardian_user_id: UUID,
    student_user_id: UUID,
) -> GuardianLinkSummary:
    _require_role(identities, tenant_id, guardian_user_id, InstitutionRole.guardian)
    _require_role(identities, tenant_id, student_user_id, InstitutionRole.student)
    activated = repo.activate_guardian_link(
        tenant_id, guardian_user_id, student_user_id
    )
    if activated is None:
        raise AccessError(404, "guardian_link_missing", "Guardian link unavailable")
    scope.audit.change(
        scope.context,
        "guardian_link.activate",
        ResourceRef(
            tenant_id=tenant_id, kind="guardian_link", resource_id=student_user_id
        ),
    )
    return activated


def revoke_guardian_link(
    scope: AuthorizedScope,
    repo: AcademicRepository,
    tenant_id: UUID,
    guardian_user_id: UUID,
    student_user_id: UUID,
) -> GuardianLinkSummary:
    if not _is_admin(scope.context) and scope.context.actor_id not in {
        guardian_user_id,
        student_user_id,
    }:
        raise AccessError(403, "forbidden", "Access denied")
    revoked = repo.revoke_guardian_link(tenant_id, guardian_user_id, student_user_id)
    if revoked is None:
        raise AccessError(404, "guardian_link_missing", "Guardian link unavailable")
    scope.audit.change(
        scope.context,
        "guardian_link.revoke",
        ResourceRef(
            tenant_id=tenant_id, kind="guardian_link", resource_id=student_user_id
        ),
    )
    return revoked


def read_student_summary(
    identities: IdentityDirectory, tenant_id: UUID, student_id: UUID
) -> StudentSummary:
    if InstitutionRole.student not in identities.applicable_roles(
        student_id, tenant_id
    ):
        raise AccessError(404, "student_missing", "Student summary unavailable")
    user = identities.user(student_id)
    if user is None:
        raise AccessError(404, "student_missing", "Student summary unavailable")
    return StudentSummary(
        tenant_id=tenant_id, user_id=user.user_id, display_name=user.display_name
    )
