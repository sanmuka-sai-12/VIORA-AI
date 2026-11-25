"""HTTP endpoints for institution metadata and academic hierarchy management."""

from collections.abc import Callable
from typing import Annotated, TypeVar
from uuid import UUID

from fastapi import APIRouter, Query, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError

from ..contracts import Page, ResourceRef
from ..identity.public import identity_directory
from ..security import AccessError, AuthorizedScope, TransactionalAccess
from . import service
from .repository import SqlAcademicRepository, SqlInstitutionStatus
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
    InstitutionDiscovery,
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


def _token(bearer: HTTPAuthorizationCredentials | None) -> str | None:
    return bearer.credentials if bearer else None


def _run_mutation(
    access: TransactionalAccess,
    bearer: HTTPAuthorizationCredentials | None,
    action: str,
    resource: ResourceRef,
    fn: Callable[[AuthorizedScope, SqlAcademicRepository], T],
) -> T:
    try:
        with access.transaction(_token(bearer), action, resource) as scope:
            repository = SqlAcademicRepository(scope.session)
            repository.lock_resource(
                resource.tenant_id, resource.kind, resource.resource_id
            )
            scope.recheck(action, resource)
            return fn(scope, repository)
    except IntegrityError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate == "23505":
            raise AccessError(
                409, "duplicate_record", "A matching academic record already exists"
            ) from None
        if sqlstate == "23503":
            raise AccessError(
                409, "invalid_reference", "Referenced parent record is unavailable"
            ) from None
        raise


def build_router(access: TransactionalAccess) -> APIRouter:
    router = APIRouter()
    bearer_dep = Security(HTTPBearer(auto_error=False))

    @router.get(
        "/institutions", tags=["institutions"], operation_id="discoverInstitutions"
    )
    def get_institutions(
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> tuple[InstitutionDiscovery, ...]:
        discover = getattr(access, "discover_institutions", None)
        if discover is None:
            raise AccessError(403, "forbidden", "Access denied")
        return discover(_token(bearer))

    # --- Institution ---
    @router.get(
        "/institutions/{tenant_id}",
        tags=["institutions"],
        operation_id="readInstitution",
    )
    def get_institution(
        tenant_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> InstitutionInfo:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        with access.transaction(_token(bearer), "institution.read", ref) as scope:
            return service.read_institution(
                SqlAcademicRepository(scope.session), tenant_id
            )

    @router.patch(
        "/institutions/{tenant_id}",
        tags=["institutions"],
        operation_id="updateInstitution",
    )
    def patch_institution(
        tenant_id: UUID,
        body: InstitutionUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> InstitutionInfo:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        return _run_mutation(
            access,
            bearer,
            "institution.update_metadata",
            ref,
            lambda s, repo: service.update_institution(s, repo, tenant_id, body),
        )

    # --- Campuses ---
    @router.post(
        "/tenants/{tenant_id}/campuses",
        tags=["academics"],
        operation_id="createCampus",
        status_code=201,
    )
    def post_campus(
        tenant_id: UUID,
        body: CampusCreate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> CampusSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        return _run_mutation(
            access,
            bearer,
            "campus.create",
            ref,
            lambda s, repo: service.create_campus(s, repo, tenant_id, body),
        )

    @router.get(
        "/tenants/{tenant_id}/campuses",
        tags=["academics"],
        operation_id="listCampuses",
    )
    def get_campuses(
        tenant_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        active: bool | None = None,
        cursor: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> Page[CampusSummary]:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        with access.transaction(_token(bearer), "campus.list", ref) as scope:
            return service.list_campuses(
                SqlAcademicRepository(scope.session),
                tenant_id,
                active=active,
                cursor=cursor,
                limit=limit,
            )

    @router.get(
        "/tenants/{tenant_id}/campuses/{campus_id}",
        tags=["academics"],
        operation_id="readCampus",
    )
    def get_campus(
        tenant_id: UUID,
        campus_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> CampusSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="campus", resource_id=campus_id)
        with access.transaction(_token(bearer), "campus.read", ref) as scope:
            return service.read_campus(
                SqlAcademicRepository(scope.session), tenant_id, campus_id
            )

    @router.patch(
        "/tenants/{tenant_id}/campuses/{campus_id}",
        tags=["academics"],
        operation_id="updateCampus",
    )
    def patch_campus(
        tenant_id: UUID,
        campus_id: UUID,
        body: CampusUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> CampusSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="campus", resource_id=campus_id)
        return _run_mutation(
            access,
            bearer,
            "campus.update",
            ref,
            lambda s, repo: service.update_campus(s, repo, tenant_id, campus_id, body),
        )

    @router.post(
        "/tenants/{tenant_id}/campuses/{campus_id}/lifecycle",
        tags=["academics"],
        operation_id="setCampusLifecycle",
    )
    def post_campus_lifecycle(
        tenant_id: UUID,
        campus_id: UUID,
        body: LifecycleUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> CampusSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="campus", resource_id=campus_id)
        return _run_mutation(
            access,
            bearer,
            "campus.lifecycle",
            ref,
            lambda s, repo: service.set_campus_lifecycle(
                s, repo, tenant_id, campus_id, body
            ),
        )

    # --- Departments ---
    @router.post(
        "/tenants/{tenant_id}/departments",
        tags=["academics"],
        operation_id="createDepartment",
        status_code=201,
    )
    def post_department(
        tenant_id: UUID,
        body: DepartmentCreate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> DepartmentSummary:
        if body.campus_id is not None:
            action = "campus.department.create"
            ref = ResourceRef(
                tenant_id=tenant_id, kind="campus", resource_id=body.campus_id
            )
        else:
            action = "department.create"
            ref = ResourceRef(
                tenant_id=tenant_id, kind="institution", resource_id=tenant_id
            )
        return _run_mutation(
            access,
            bearer,
            action,
            ref,
            lambda s, repo: service.create_department(s, repo, tenant_id, body),
        )

    @router.get(
        "/tenants/{tenant_id}/departments",
        tags=["academics"],
        operation_id="listDepartments",
    )
    def get_departments(
        tenant_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        campus_id: UUID | None = None,
        active: bool | None = None,
        cursor: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> Page[DepartmentSummary]:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        with access.transaction(_token(bearer), "department.list", ref) as scope:
            return service.list_departments(
                scope,
                SqlAcademicRepository(scope.session),
                tenant_id,
                campus_id=campus_id,
                active=active,
                cursor=cursor,
                limit=limit,
            )

    @router.get(
        "/tenants/{tenant_id}/departments/{department_id}",
        tags=["academics"],
        operation_id="readDepartment",
    )
    def get_department(
        tenant_id: UUID,
        department_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> DepartmentSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="department", resource_id=department_id
        )
        with access.transaction(_token(bearer), "department.read", ref) as scope:
            return service.read_department(
                SqlAcademicRepository(scope.session), tenant_id, department_id
            )

    @router.patch(
        "/tenants/{tenant_id}/departments/{department_id}",
        tags=["academics"],
        operation_id="updateDepartment",
    )
    def patch_department(
        tenant_id: UUID,
        department_id: UUID,
        body: DepartmentUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> DepartmentSummary:
        action = (
            "department.update"
            if "campus_id" in body.model_fields_set
            else "department.update_metadata"
        )
        ref = ResourceRef(
            tenant_id=tenant_id, kind="department", resource_id=department_id
        )
        return _run_mutation(
            access,
            bearer,
            action,
            ref,
            lambda s, repo: service.update_department(
                s, repo, tenant_id, department_id, body
            ),
        )

    @router.post(
        "/tenants/{tenant_id}/departments/{department_id}/lifecycle",
        tags=["academics"],
        operation_id="setDepartmentLifecycle",
    )
    def post_department_lifecycle(
        tenant_id: UUID,
        department_id: UUID,
        body: LifecycleUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> DepartmentSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="department", resource_id=department_id
        )
        return _run_mutation(
            access,
            bearer,
            "department.lifecycle",
            ref,
            lambda s, repo: service.set_department_lifecycle(
                s, repo, tenant_id, department_id, body
            ),
        )

    # --- Programmes ---
    @router.post(
        "/tenants/{tenant_id}/programmes",
        tags=["academics"],
        operation_id="createProgramme",
        status_code=201,
    )
    def post_programme(
        tenant_id: UUID,
        body: ProgrammeCreate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> ProgrammeSummary:
        if body.department_id is not None:
            action = "department.programme.create"
            ref = ResourceRef(
                tenant_id=tenant_id, kind="department", resource_id=body.department_id
            )
        else:
            action = "programme.create"
            ref = ResourceRef(
                tenant_id=tenant_id, kind="institution", resource_id=tenant_id
            )
        return _run_mutation(
            access,
            bearer,
            action,
            ref,
            lambda s, repo: service.create_programme(s, repo, tenant_id, body),
        )

    @router.get(
        "/tenants/{tenant_id}/programmes",
        tags=["academics"],
        operation_id="listProgrammes",
    )
    def get_programmes(
        tenant_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        department_id: UUID | None = None,
        active: bool | None = None,
        cursor: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> Page[ProgrammeSummary]:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        with access.transaction(_token(bearer), "programme.list", ref) as scope:
            return service.list_programmes(
                scope,
                SqlAcademicRepository(scope.session),
                tenant_id,
                department_id=department_id,
                active=active,
                cursor=cursor,
                limit=limit,
            )

    @router.get(
        "/tenants/{tenant_id}/programmes/{programme_id}",
        tags=["academics"],
        operation_id="readProgramme",
    )
    def get_programme(
        tenant_id: UUID,
        programme_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> ProgrammeSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="programme", resource_id=programme_id
        )
        with access.transaction(_token(bearer), "programme.read", ref) as scope:
            return service.read_programme(
                SqlAcademicRepository(scope.session), tenant_id, programme_id
            )

    @router.patch(
        "/tenants/{tenant_id}/programmes/{programme_id}",
        tags=["academics"],
        operation_id="updateProgramme",
    )
    def patch_programme(
        tenant_id: UUID,
        programme_id: UUID,
        body: ProgrammeUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> ProgrammeSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="programme", resource_id=programme_id
        )
        return _run_mutation(
            access,
            bearer,
            "programme.update",
            ref,
            lambda s, repo: service.update_programme(
                s, repo, tenant_id, programme_id, body
            ),
        )

    @router.post(
        "/tenants/{tenant_id}/programmes/{programme_id}/lifecycle",
        tags=["academics"],
        operation_id="setProgrammeLifecycle",
    )
    def post_programme_lifecycle(
        tenant_id: UUID,
        programme_id: UUID,
        body: LifecycleUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> ProgrammeSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="programme", resource_id=programme_id
        )
        return _run_mutation(
            access,
            bearer,
            "programme.lifecycle",
            ref,
            lambda s, repo: service.set_programme_lifecycle(
                s, repo, tenant_id, programme_id, body
            ),
        )

    # --- Academic Years ---
    @router.post(
        "/tenants/{tenant_id}/academic-years",
        tags=["academics"],
        operation_id="createAcademicYear",
        status_code=201,
    )
    def post_academic_year(
        tenant_id: UUID,
        body: AcademicYearCreate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> AcademicYearSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        return _run_mutation(
            access,
            bearer,
            "academic_year.create",
            ref,
            lambda s, repo: service.create_academic_year(s, repo, tenant_id, body),
        )

    @router.get(
        "/tenants/{tenant_id}/academic-years",
        tags=["academics"],
        operation_id="listAcademicYears",
    )
    def get_academic_years(
        tenant_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        active: bool | None = None,
        cursor: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> Page[AcademicYearSummary]:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        with access.transaction(_token(bearer), "academic_year.list", ref) as scope:
            return service.list_academic_years(
                SqlAcademicRepository(scope.session),
                tenant_id,
                active=active,
                cursor=cursor,
                limit=limit,
            )

    @router.get(
        "/tenants/{tenant_id}/academic-years/{academic_year_id}",
        tags=["academics"],
        operation_id="readAcademicYear",
    )
    def get_academic_year(
        tenant_id: UUID,
        academic_year_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> AcademicYearSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="academic_year", resource_id=academic_year_id
        )
        with access.transaction(_token(bearer), "academic_year.read", ref) as scope:
            return service.read_academic_year(
                SqlAcademicRepository(scope.session), tenant_id, academic_year_id
            )

    @router.patch(
        "/tenants/{tenant_id}/academic-years/{academic_year_id}",
        tags=["academics"],
        operation_id="updateAcademicYear",
    )
    def patch_academic_year(
        tenant_id: UUID,
        academic_year_id: UUID,
        body: AcademicYearUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> AcademicYearSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="academic_year", resource_id=academic_year_id
        )
        return _run_mutation(
            access,
            bearer,
            "academic_year.update",
            ref,
            lambda s, repo: service.update_academic_year(
                s, repo, tenant_id, academic_year_id, body
            ),
        )

    @router.post(
        "/tenants/{tenant_id}/academic-years/{academic_year_id}/lifecycle",
        tags=["academics"],
        operation_id="setAcademicYearLifecycle",
    )
    def post_academic_year_lifecycle(
        tenant_id: UUID,
        academic_year_id: UUID,
        body: LifecycleUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> AcademicYearSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="academic_year", resource_id=academic_year_id
        )
        return _run_mutation(
            access,
            bearer,
            "academic_year.lifecycle",
            ref,
            lambda s, repo: service.set_academic_year_lifecycle(
                s, repo, tenant_id, academic_year_id, body
            ),
        )

    # --- Semesters ---
    @router.post(
        "/tenants/{tenant_id}/semesters",
        tags=["academics"],
        operation_id="createSemester",
        status_code=201,
    )
    def post_semester(
        tenant_id: UUID,
        body: SemesterCreate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SemesterSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="academic_year", resource_id=body.academic_year_id
        )
        return _run_mutation(
            access,
            bearer,
            "academic_year.semester.create",
            ref,
            lambda s, repo: service.create_semester(s, repo, tenant_id, body),
        )

    @router.get(
        "/tenants/{tenant_id}/semesters",
        tags=["academics"],
        operation_id="listSemesters",
    )
    def get_semesters(
        tenant_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        academic_year_id: UUID | None = None,
        active: bool | None = None,
        cursor: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> Page[SemesterSummary]:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        with access.transaction(_token(bearer), "semester.list", ref) as scope:
            return service.list_semesters(
                SqlAcademicRepository(scope.session),
                tenant_id,
                academic_year_id=academic_year_id,
                active=active,
                cursor=cursor,
                limit=limit,
            )

    @router.get(
        "/tenants/{tenant_id}/semesters/{semester_id}",
        tags=["academics"],
        operation_id="readSemester",
    )
    def get_semester(
        tenant_id: UUID,
        semester_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SemesterSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="semester", resource_id=semester_id)
        with access.transaction(_token(bearer), "semester.read", ref) as scope:
            return service.read_semester(
                SqlAcademicRepository(scope.session), tenant_id, semester_id
            )

    @router.patch(
        "/tenants/{tenant_id}/semesters/{semester_id}",
        tags=["academics"],
        operation_id="updateSemester",
    )
    def patch_semester(
        tenant_id: UUID,
        semester_id: UUID,
        body: SemesterUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SemesterSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="semester", resource_id=semester_id)
        return _run_mutation(
            access,
            bearer,
            "semester.update",
            ref,
            lambda s, repo: service.update_semester(
                s, repo, tenant_id, semester_id, body
            ),
        )

    @router.post(
        "/tenants/{tenant_id}/semesters/{semester_id}/lifecycle",
        tags=["academics"],
        operation_id="setSemesterLifecycle",
    )
    def post_semester_lifecycle(
        tenant_id: UUID,
        semester_id: UUID,
        body: LifecycleUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SemesterSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="semester", resource_id=semester_id)
        return _run_mutation(
            access,
            bearer,
            "semester.lifecycle",
            ref,
            lambda s, repo: service.set_semester_lifecycle(
                s, repo, tenant_id, semester_id, body
            ),
        )

    # --- Sections ---
    @router.post(
        "/tenants/{tenant_id}/sections",
        tags=["academics"],
        operation_id="createSection",
        status_code=201,
    )
    def post_section(
        tenant_id: UUID,
        body: SectionCreate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SectionSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="semester", resource_id=body.semester_id
        )
        return _run_mutation(
            access,
            bearer,
            "semester.section.create",
            ref,
            lambda s, repo: service.create_section(s, repo, tenant_id, body),
        )

    @router.get(
        "/tenants/{tenant_id}/sections",
        tags=["academics"],
        operation_id="listSections",
    )
    def get_sections(
        tenant_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        semester_id: UUID | None = None,
        programme_id: UUID | None = None,
        active: bool | None = None,
        cursor: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> Page[SectionSummary]:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        with access.transaction(_token(bearer), "section.list", ref) as scope:
            return service.list_sections(
                scope,
                SqlAcademicRepository(scope.session),
                tenant_id,
                semester_id=semester_id,
                programme_id=programme_id,
                active=active,
                cursor=cursor,
                limit=limit,
            )

    @router.get(
        "/tenants/{tenant_id}/sections/{section_id}",
        tags=["academics"],
        operation_id="readSection",
    )
    def get_section(
        tenant_id: UUID,
        section_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SectionSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="section", resource_id=section_id)
        with access.transaction(_token(bearer), "section.read", ref) as scope:
            return service.read_section(
                SqlAcademicRepository(scope.session), tenant_id, section_id
            )

    @router.patch(
        "/tenants/{tenant_id}/sections/{section_id}",
        tags=["academics"],
        operation_id="updateSection",
    )
    def patch_section(
        tenant_id: UUID,
        section_id: UUID,
        body: SectionUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SectionSummary:
        structural = (
            "semester_id" in body.model_fields_set
            or "programme_id" in body.model_fields_set
        )
        action = "section.update" if structural else "section.update_metadata"
        ref = ResourceRef(tenant_id=tenant_id, kind="section", resource_id=section_id)
        return _run_mutation(
            access,
            bearer,
            action,
            ref,
            lambda s, repo: service.update_section(
                s, repo, tenant_id, section_id, body
            ),
        )

    @router.post(
        "/tenants/{tenant_id}/sections/{section_id}/lifecycle",
        tags=["academics"],
        operation_id="setSectionLifecycle",
    )
    def post_section_lifecycle(
        tenant_id: UUID,
        section_id: UUID,
        body: LifecycleUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SectionSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="section", resource_id=section_id)
        return _run_mutation(
            access,
            bearer,
            "section.lifecycle",
            ref,
            lambda s, repo: service.set_section_lifecycle(
                s, repo, tenant_id, section_id, body
            ),
        )

    # --- Subjects ---
    @router.post(
        "/tenants/{tenant_id}/subjects",
        tags=["academics"],
        operation_id="createSubject",
        status_code=201,
    )
    def post_subject(
        tenant_id: UUID,
        body: SubjectCreate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SubjectSummary:
        if body.department_id is not None:
            action = "department.subject.create"
            ref = ResourceRef(
                tenant_id=tenant_id, kind="department", resource_id=body.department_id
            )
        else:
            action = "subject.create"
            ref = ResourceRef(
                tenant_id=tenant_id, kind="institution", resource_id=tenant_id
            )
        return _run_mutation(
            access,
            bearer,
            action,
            ref,
            lambda s, repo: service.create_subject(s, repo, tenant_id, body),
        )

    @router.get(
        "/tenants/{tenant_id}/subjects",
        tags=["academics"],
        operation_id="listSubjects",
    )
    def get_subjects(
        tenant_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        department_id: UUID | None = None,
        active: bool | None = None,
        cursor: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> Page[SubjectSummary]:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        with access.transaction(_token(bearer), "subject.list", ref) as scope:
            return service.list_subjects(
                scope,
                SqlAcademicRepository(scope.session),
                tenant_id,
                department_id=department_id,
                active=active,
                cursor=cursor,
                limit=limit,
            )

    @router.get(
        "/tenants/{tenant_id}/subjects/{subject_id}",
        tags=["academics"],
        operation_id="readSubject",
    )
    def get_subject(
        tenant_id: UUID,
        subject_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SubjectSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="subject", resource_id=subject_id)
        with access.transaction(_token(bearer), "subject.read", ref) as scope:
            return service.read_subject(
                SqlAcademicRepository(scope.session), tenant_id, subject_id
            )

    @router.patch(
        "/tenants/{tenant_id}/subjects/{subject_id}",
        tags=["academics"],
        operation_id="updateSubject",
    )
    def patch_subject(
        tenant_id: UUID,
        subject_id: UUID,
        body: SubjectUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SubjectSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="subject", resource_id=subject_id)
        return _run_mutation(
            access,
            bearer,
            "subject.update",
            ref,
            lambda s, repo: service.update_subject(
                s, repo, tenant_id, subject_id, body
            ),
        )

    @router.post(
        "/tenants/{tenant_id}/subjects/{subject_id}/lifecycle",
        tags=["academics"],
        operation_id="setSubjectLifecycle",
    )
    def post_subject_lifecycle(
        tenant_id: UUID,
        subject_id: UUID,
        body: LifecycleUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SubjectSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="subject", resource_id=subject_id)
        return _run_mutation(
            access,
            bearer,
            "subject.lifecycle",
            ref,
            lambda s, repo: service.set_subject_lifecycle(
                s, repo, tenant_id, subject_id, body
            ),
        )

    # --- Subject Offerings ---
    @router.post(
        "/tenants/{tenant_id}/offerings",
        tags=["academics"],
        operation_id="createOffering",
        status_code=201,
    )
    def post_offering(
        tenant_id: UUID,
        body: SubjectOfferingCreate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SubjectOfferingSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="section", resource_id=body.section_id
        )
        return _run_mutation(
            access,
            bearer,
            "section.offering.create",
            ref,
            lambda s, repo: service.create_offering(s, repo, tenant_id, body),
        )

    @router.get(
        "/tenants/{tenant_id}/offerings",
        tags=["academics"],
        operation_id="listOfferings",
    )
    def get_offerings(
        tenant_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        section_id: UUID | None = None,
        subject_id: UUID | None = None,
        active: bool | None = None,
        cursor: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> Page[SubjectOfferingSummary]:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        with access.transaction(_token(bearer), "offering.list", ref) as scope:
            return service.list_offerings(
                scope,
                SqlAcademicRepository(scope.session),
                tenant_id,
                section_id=section_id,
                subject_id=subject_id,
                active=active,
                cursor=cursor,
                limit=limit,
            )

    @router.get(
        "/tenants/{tenant_id}/offerings/{offering_id}",
        tags=["academics"],
        operation_id="readOffering",
    )
    def get_offering(
        tenant_id: UUID,
        offering_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SubjectOfferingSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="offering", resource_id=offering_id)
        with access.transaction(_token(bearer), "offering.catalog.read", ref) as scope:
            return service.read_offering(
                SqlAcademicRepository(scope.session), tenant_id, offering_id
            )

    @router.patch(
        "/tenants/{tenant_id}/offerings/{offering_id}",
        tags=["academics"],
        operation_id="updateOffering",
    )
    def patch_offering(
        tenant_id: UUID,
        offering_id: UUID,
        body: SubjectOfferingUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SubjectOfferingSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="offering", resource_id=offering_id)
        return _run_mutation(
            access,
            bearer,
            "offering.update",
            ref,
            lambda s, repo: service.update_offering(
                s, repo, tenant_id, offering_id, body
            ),
        )

    @router.post(
        "/tenants/{tenant_id}/offerings/{offering_id}/lifecycle",
        tags=["academics"],
        operation_id="setOfferingLifecycle",
    )
    def post_offering_lifecycle(
        tenant_id: UUID,
        offering_id: UUID,
        body: LifecycleUpdate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SubjectOfferingSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="offering", resource_id=offering_id)
        return _run_mutation(
            access,
            bearer,
            "offering.lifecycle",
            ref,
            lambda s, repo: service.set_offering_lifecycle(
                s, repo, tenant_id, offering_id, body
            ),
        )

    # --- Academic Assignments ---
    @router.post(
        "/tenants/{tenant_id}/departments/{department_id}/heads",
        tags=["assignments"],
        operation_id="assignDepartmentHead",
        status_code=201,
    )
    def post_department_head(
        tenant_id: UUID,
        department_id: UUID,
        body: AssignmentCreate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> DepartmentHeadSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="department", resource_id=department_id
        )
        return _run_mutation(
            access,
            bearer,
            "department.head.assign",
            ref,
            lambda s, repo: service.assign_department_head(
                s,
                repo,
                identity_directory(s.session, SqlInstitutionStatus(s.session)),
                tenant_id,
                department_id,
                body,
            ),
        )

    @router.get(
        "/tenants/{tenant_id}/departments/{department_id}/heads",
        tags=["assignments"],
        operation_id="listDepartmentHeads",
    )
    def get_department_heads(
        tenant_id: UUID,
        department_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        active: bool | None = None,
        cursor: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> Page[DepartmentHeadSummary]:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="department", resource_id=department_id
        )
        with access.transaction(_token(bearer), "department.head.list", ref) as scope:
            return service.list_department_heads(
                SqlAcademicRepository(scope.session),
                tenant_id,
                department_id,
                active=active,
                cursor=cursor,
                limit=limit,
            )

    @router.post(
        "/tenants/{tenant_id}/departments/{department_id}/heads/{user_id}/revoke",
        tags=["assignments"],
        operation_id="revokeDepartmentHead",
    )
    def post_revoke_department_head(
        tenant_id: UUID,
        department_id: UUID,
        user_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> DepartmentHeadSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="department", resource_id=department_id
        )
        return _run_mutation(
            access,
            bearer,
            "department.head.revoke",
            ref,
            lambda s, repo: service.revoke_department_head(
                s, repo, tenant_id, department_id, user_id
            ),
        )

    @router.post(
        "/tenants/{tenant_id}/sections/{section_id}/coordinators",
        tags=["assignments"],
        operation_id="assignSectionCoordinator",
        status_code=201,
    )
    def post_section_coordinator(
        tenant_id: UUID,
        section_id: UUID,
        body: AssignmentCreate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> ClassCoordinatorSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="section", resource_id=section_id)
        return _run_mutation(
            access,
            bearer,
            "section.coordinator.assign",
            ref,
            lambda s, repo: service.assign_section_coordinator(
                s,
                repo,
                identity_directory(s.session, SqlInstitutionStatus(s.session)),
                tenant_id,
                section_id,
                body,
            ),
        )

    @router.get(
        "/tenants/{tenant_id}/sections/{section_id}/coordinators",
        tags=["assignments"],
        operation_id="listSectionCoordinators",
    )
    def get_section_coordinators(
        tenant_id: UUID,
        section_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        active: bool | None = None,
        cursor: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> Page[ClassCoordinatorSummary]:
        ref = ResourceRef(tenant_id=tenant_id, kind="section", resource_id=section_id)
        with access.transaction(
            _token(bearer), "section.coordinator.list", ref
        ) as scope:
            return service.list_section_coordinators(
                SqlAcademicRepository(scope.session),
                tenant_id,
                section_id,
                active=active,
                cursor=cursor,
                limit=limit,
            )

    @router.post(
        "/tenants/{tenant_id}/sections/{section_id}/coordinators/{user_id}/revoke",
        tags=["assignments"],
        operation_id="revokeSectionCoordinator",
    )
    def post_revoke_section_coordinator(
        tenant_id: UUID,
        section_id: UUID,
        user_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> ClassCoordinatorSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="section", resource_id=section_id)
        return _run_mutation(
            access,
            bearer,
            "section.coordinator.revoke",
            ref,
            lambda s, repo: service.revoke_section_coordinator(
                s, repo, tenant_id, section_id, user_id
            ),
        )

    @router.post(
        "/tenants/{tenant_id}/offerings/{offering_id}/teachers",
        tags=["assignments"],
        operation_id="assignOfferingTeacher",
        status_code=201,
    )
    def post_offering_teacher(
        tenant_id: UUID,
        offering_id: UUID,
        body: AssignmentCreate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> OfferingAssignmentSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="offering", resource_id=offering_id)
        return _run_mutation(
            access,
            bearer,
            "offering.teacher.assign",
            ref,
            lambda s, repo: service.assign_offering_teacher(
                s,
                repo,
                identity_directory(s.session, SqlInstitutionStatus(s.session)),
                tenant_id,
                offering_id,
                body,
            ),
        )

    @router.get(
        "/tenants/{tenant_id}/offerings/{offering_id}/teachers",
        tags=["assignments"],
        operation_id="listOfferingTeachers",
    )
    def get_offering_teachers(
        tenant_id: UUID,
        offering_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        active: bool | None = None,
        cursor: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> Page[OfferingAssignmentSummary]:
        ref = ResourceRef(tenant_id=tenant_id, kind="offering", resource_id=offering_id)
        with access.transaction(_token(bearer), "offering.teacher.list", ref) as scope:
            return service.list_offering_teachers(
                SqlAcademicRepository(scope.session),
                tenant_id,
                offering_id,
                active=active,
                cursor=cursor,
                limit=limit,
            )

    @router.post(
        "/tenants/{tenant_id}/offerings/{offering_id}/teachers/{user_id}/revoke",
        tags=["assignments"],
        operation_id="revokeOfferingTeacher",
    )
    def post_revoke_offering_teacher(
        tenant_id: UUID,
        offering_id: UUID,
        user_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> OfferingAssignmentSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="offering", resource_id=offering_id)
        return _run_mutation(
            access,
            bearer,
            "offering.teacher.revoke",
            ref,
            lambda s, repo: service.revoke_offering_teacher(
                s, repo, tenant_id, offering_id, user_id
            ),
        )

    @router.post(
        "/tenants/{tenant_id}/offerings/{offering_id}/enrolments",
        tags=["assignments"],
        operation_id="enrolStudent",
        status_code=201,
    )
    def post_enrol_student(
        tenant_id: UUID,
        offering_id: UUID,
        body: AssignmentCreate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> OfferingAssignmentSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="offering", resource_id=offering_id)
        return _run_mutation(
            access,
            bearer,
            "offering.enrolment.create",
            ref,
            lambda s, repo: service.enrol_student(
                s,
                repo,
                identity_directory(s.session, SqlInstitutionStatus(s.session)),
                tenant_id,
                offering_id,
                body,
            ),
        )

    @router.get(
        "/tenants/{tenant_id}/offerings/{offering_id}/enrolments",
        tags=["assignments"],
        operation_id="listOfferingEnrolments",
    )
    def get_offering_enrolments(
        tenant_id: UUID,
        offering_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        active: bool | None = None,
        cursor: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> Page[OfferingAssignmentSummary]:
        ref = ResourceRef(tenant_id=tenant_id, kind="offering", resource_id=offering_id)
        with access.transaction(
            _token(bearer), "offering.enrolment.list", ref
        ) as scope:
            return service.list_offering_enrolments(
                SqlAcademicRepository(scope.session),
                tenant_id,
                offering_id,
                active=active,
                cursor=cursor,
                limit=limit,
            )

    @router.post(
        "/tenants/{tenant_id}/offerings/{offering_id}/enrolments/{user_id}/withdraw",
        tags=["assignments"],
        operation_id="withdrawStudent",
    )
    def post_withdraw_student(
        tenant_id: UUID,
        offering_id: UUID,
        user_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> OfferingAssignmentSummary:
        ref = ResourceRef(tenant_id=tenant_id, kind="offering", resource_id=offering_id)
        return _run_mutation(
            access,
            bearer,
            "offering.enrolment.withdraw",
            ref,
            lambda s, repo: service.withdraw_student(
                s, repo, tenant_id, offering_id, user_id
            ),
        )

    # --- Guardian Relationships & Minimal Student Summary ---
    @router.post(
        "/tenants/{tenant_id}/guardian-links",
        tags=["guardians"],
        operation_id="createGuardianLink",
        status_code=201,
    )
    def post_guardian_link(
        tenant_id: UUID,
        body: GuardianLinkCreate,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> GuardianLinkSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        return _run_mutation(
            access,
            bearer,
            "guardian_link.create",
            ref,
            lambda s, repo: service.create_guardian_link(
                s,
                repo,
                identity_directory(s.session, SqlInstitutionStatus(s.session)),
                tenant_id,
                body,
            ),
        )

    @router.get(
        "/tenants/{tenant_id}/guardian-links",
        tags=["guardians"],
        operation_id="listGuardianLinks",
    )
    def get_guardian_links(
        tenant_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        active: bool | None = None,
        cursor: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> Page[GuardianLinkSummary]:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="institution", resource_id=tenant_id
        )
        with access.transaction(_token(bearer), "guardian_link.list", ref) as scope:
            return service.list_guardian_links(
                scope,
                SqlAcademicRepository(scope.session),
                tenant_id,
                active=active,
                cursor=cursor,
                limit=limit,
            )

    @router.post(
        "/tenants/{tenant_id}/guardian-links/{guardian_user_id}/students/{student_user_id}/verify",
        tags=["guardians"],
        operation_id="verifyGuardianLink",
    )
    def post_verify_guardian_link(
        tenant_id: UUID,
        guardian_user_id: UUID,
        student_user_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> GuardianLinkSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="guardian_link", resource_id=student_user_id
        )
        return _run_mutation(
            access,
            bearer,
            "guardian_link.verify",
            ref,
            lambda s, repo: service.verify_guardian_link(
                s, repo, tenant_id, guardian_user_id, student_user_id
            ),
        )

    @router.post(
        "/tenants/{tenant_id}/guardian-links/{guardian_user_id}/students/{student_user_id}/consent",
        tags=["guardians"],
        operation_id="consentGuardianLink",
    )
    def post_consent_guardian_link(
        tenant_id: UUID,
        guardian_user_id: UUID,
        student_user_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> GuardianLinkSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="guardian_link", resource_id=student_user_id
        )
        return _run_mutation(
            access,
            bearer,
            "guardian_link.consent",
            ref,
            lambda s, repo: service.consent_guardian_link(
                s, repo, tenant_id, guardian_user_id, student_user_id
            ),
        )

    @router.post(
        "/tenants/{tenant_id}/guardian-links/{guardian_user_id}/students/{student_user_id}/activate",
        tags=["guardians"],
        operation_id="activateGuardianLink",
    )
    def post_activate_guardian_link(
        tenant_id: UUID,
        guardian_user_id: UUID,
        student_user_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> GuardianLinkSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="guardian_link", resource_id=student_user_id
        )
        return _run_mutation(
            access,
            bearer,
            "guardian_link.activate",
            ref,
            lambda s, repo: service.activate_guardian_link(
                s,
                repo,
                identity_directory(s.session, SqlInstitutionStatus(s.session)),
                tenant_id,
                guardian_user_id,
                student_user_id,
            ),
        )

    @router.post(
        "/tenants/{tenant_id}/guardian-links/{guardian_user_id}/students/{student_user_id}/revoke",
        tags=["guardians"],
        operation_id="revokeGuardianLink",
    )
    def post_revoke_guardian_link(
        tenant_id: UUID,
        guardian_user_id: UUID,
        student_user_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> GuardianLinkSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="guardian_link", resource_id=student_user_id
        )
        return _run_mutation(
            access,
            bearer,
            "guardian_link.revoke",
            ref,
            lambda s, repo: service.revoke_guardian_link(
                s, repo, tenant_id, guardian_user_id, student_user_id
            ),
        )

    @router.get(
        "/tenants/{tenant_id}/students/{student_id}/summary",
        tags=["guardians"],
        operation_id="readStudentSummary",
    )
    def get_student_summary(
        tenant_id: UUID,
        student_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> StudentSummary:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="student_summary", resource_id=student_id
        )
        with access.transaction(_token(bearer), "student.summary.read", ref) as scope:
            return service.read_student_summary(
                identity_directory(scope.session, SqlInstitutionStatus(scope.session)),
                tenant_id,
                student_id,
            )

    return router
