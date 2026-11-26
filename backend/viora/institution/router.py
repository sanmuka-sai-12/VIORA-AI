"""HTTP endpoints for institution metadata and academic hierarchy management."""

from collections.abc import Callable
from typing import Annotated, TypeVar
from uuid import UUID

from fastapi import APIRouter, Query, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError

from ..contracts import Page, ResourceRef
from ..identity.public import IdentityDirectory, identity_directory
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


def _run_read(
    access: TransactionalAccess,
    bearer: HTTPAuthorizationCredentials | None,
    action: str,
    ref: ResourceRef,
    fn: Callable[[AuthorizedScope, SqlAcademicRepository], T],
) -> T:
    with access.transaction(_token(bearer), action, ref) as scope:
        return fn(scope, SqlAcademicRepository(scope.session))


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
            repository.lock_resource(resource.tenant_id, resource.kind, resource.resource_id)
            scope.recheck(action, resource)
            return fn(scope, repository)
    except IntegrityError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate == "23505":
            raise AccessError(409, "duplicate_record", "A matching academic record already exists") from None
        if sqlstate == "23503":
            raise AccessError(409, "invalid_reference", "Referenced parent record is unavailable") from None
        raise


def _inst_ref(tenant_id: UUID) -> ResourceRef:
    return ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)


def _ref(tenant_id: UUID, kind: str, resource_id: UUID) -> ResourceRef:
    return ResourceRef(tenant_id=tenant_id, kind=kind, resource_id=resource_id)


def _identities(scope: AuthorizedScope) -> IdentityDirectory:
    return identity_directory(scope.session, SqlInstitutionStatus(scope.session))


def build_router(access: TransactionalAccess) -> APIRouter:
    router = APIRouter()
    bearer_dep = Security(HTTPBearer(auto_error=False))

    @router.get("/institutions", tags=["institutions"], operation_id="discoverInstitutions")
    def get_institutions(bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> tuple[InstitutionDiscovery, ...]:
        discover = getattr(access, "discover_institutions", None)
        if discover is None:
            raise AccessError(403, "forbidden", "Access denied")
        return discover(_token(bearer))

    @router.get("/institutions/{tenant_id}", tags=["institutions"], operation_id="readInstitution")
    def get_institution(tenant_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> InstitutionInfo:
        return _run_read(access, bearer, "institution.read", _inst_ref(tenant_id), lambda _s, r: service.read_institution(r, tenant_id))

    @router.patch("/institutions/{tenant_id}", tags=["institutions"], operation_id="updateInstitution")
    def patch_institution(tenant_id: UUID, body: InstitutionUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> InstitutionInfo:
        return _run_mutation(access, bearer, "institution.update_metadata", _inst_ref(tenant_id), lambda s, r: service.update_institution(s, r, tenant_id, body))

    # --- Campuses ---
    @router.post("/tenants/{tenant_id}/campuses", tags=["academics"], operation_id="createCampus", status_code=201)
    def post_campus(tenant_id: UUID, body: CampusCreate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> CampusSummary:
        return _run_mutation(access, bearer, "campus.create", _inst_ref(tenant_id), lambda s, r: service.create_campus(s, r, tenant_id, body))

    @router.get("/tenants/{tenant_id}/campuses", tags=["academics"], operation_id="listCampuses")
    def get_campuses(tenant_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep], active: bool | None = None, cursor: UUID | None = None, limit: int = Query(default=50, ge=1, le=100)) -> Page[CampusSummary]:
        return _run_read(access, bearer, "campus.list", _inst_ref(tenant_id), lambda _s, r: service.list_campuses(r, tenant_id, active=active, cursor=cursor, limit=limit))

    @router.get("/tenants/{tenant_id}/campuses/{campus_id}", tags=["academics"], operation_id="readCampus")
    def get_campus(tenant_id: UUID, campus_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> CampusSummary:
        return _run_read(access, bearer, "campus.read", _ref(tenant_id, "campus", campus_id), lambda _s, r: service.read_campus(r, tenant_id, campus_id))

    @router.patch("/tenants/{tenant_id}/campuses/{campus_id}", tags=["academics"], operation_id="updateCampus")
    def patch_campus(tenant_id: UUID, campus_id: UUID, body: CampusUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> CampusSummary:
        return _run_mutation(access, bearer, "campus.update", _ref(tenant_id, "campus", campus_id), lambda s, r: service.update_campus(s, r, tenant_id, campus_id, body))

    @router.post("/tenants/{tenant_id}/campuses/{campus_id}/lifecycle", tags=["academics"], operation_id="setCampusLifecycle")
    def post_campus_lifecycle(tenant_id: UUID, campus_id: UUID, body: LifecycleUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> CampusSummary:
        return _run_mutation(access, bearer, "campus.lifecycle", _ref(tenant_id, "campus", campus_id), lambda s, r: service.set_campus_lifecycle(s, r, tenant_id, campus_id, body))

    # --- Departments ---
    @router.post("/tenants/{tenant_id}/departments", tags=["academics"], operation_id="createDepartment", status_code=201)
    def post_department(tenant_id: UUID, body: DepartmentCreate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> DepartmentSummary:
        action = "campus.department.create" if body.campus_id is not None else "department.create"
        ref = _ref(tenant_id, "campus", body.campus_id) if body.campus_id is not None else _inst_ref(tenant_id)
        return _run_mutation(access, bearer, action, ref, lambda s, r: service.create_department(s, r, tenant_id, body))

    @router.get("/tenants/{tenant_id}/departments", tags=["academics"], operation_id="listDepartments")
    def get_departments(tenant_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep], campus_id: UUID | None = None, active: bool | None = None, cursor: UUID | None = None, limit: int = Query(default=50, ge=1, le=100)) -> Page[DepartmentSummary]:
        return _run_read(access, bearer, "department.list", _inst_ref(tenant_id), lambda s, r: service.list_departments(s, r, tenant_id, campus_id=campus_id, active=active, cursor=cursor, limit=limit))

    @router.get("/tenants/{tenant_id}/departments/{department_id}", tags=["academics"], operation_id="readDepartment")
    def get_department(tenant_id: UUID, department_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> DepartmentSummary:
        return _run_read(access, bearer, "department.read", _ref(tenant_id, "department", department_id), lambda _s, r: service.read_department(r, tenant_id, department_id))

    @router.patch("/tenants/{tenant_id}/departments/{department_id}", tags=["academics"], operation_id="updateDepartment")
    def patch_department(tenant_id: UUID, department_id: UUID, body: DepartmentUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> DepartmentSummary:
        action = "department.update" if "campus_id" in body.model_fields_set else "department.update_metadata"
        return _run_mutation(access, bearer, action, _ref(tenant_id, "department", department_id), lambda s, r: service.update_department(s, r, tenant_id, department_id, body))

    @router.post("/tenants/{tenant_id}/departments/{department_id}/lifecycle", tags=["academics"], operation_id="setDepartmentLifecycle")
    def post_department_lifecycle(tenant_id: UUID, department_id: UUID, body: LifecycleUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> DepartmentSummary:
        return _run_mutation(access, bearer, "department.lifecycle", _ref(tenant_id, "department", department_id), lambda s, r: service.set_department_lifecycle(s, r, tenant_id, department_id, body))

    # --- Programmes ---
    @router.post("/tenants/{tenant_id}/programmes", tags=["academics"], operation_id="createProgramme", status_code=201)
    def post_programme(tenant_id: UUID, body: ProgrammeCreate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> ProgrammeSummary:
        action = "department.programme.create" if body.department_id is not None else "programme.create"
        ref = _ref(tenant_id, "department", body.department_id) if body.department_id is not None else _inst_ref(tenant_id)
        return _run_mutation(access, bearer, action, ref, lambda s, r: service.create_programme(s, r, tenant_id, body))

    @router.get("/tenants/{tenant_id}/programmes", tags=["academics"], operation_id="listProgrammes")
    def get_programmes(tenant_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep], department_id: UUID | None = None, active: bool | None = None, cursor: UUID | None = None, limit: int = Query(default=50, ge=1, le=100)) -> Page[ProgrammeSummary]:
        return _run_read(access, bearer, "programme.list", _inst_ref(tenant_id), lambda s, r: service.list_programmes(s, r, tenant_id, department_id=department_id, active=active, cursor=cursor, limit=limit))

    @router.get("/tenants/{tenant_id}/programmes/{programme_id}", tags=["academics"], operation_id="readProgramme")
    def get_programme(tenant_id: UUID, programme_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> ProgrammeSummary:
        return _run_read(access, bearer, "programme.read", _ref(tenant_id, "programme", programme_id), lambda _s, r: service.read_programme(r, tenant_id, programme_id))

    @router.patch("/tenants/{tenant_id}/programmes/{programme_id}", tags=["academics"], operation_id="updateProgramme")
    def patch_programme(tenant_id: UUID, programme_id: UUID, body: ProgrammeUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> ProgrammeSummary:
        return _run_mutation(access, bearer, "programme.update", _ref(tenant_id, "programme", programme_id), lambda s, r: service.update_programme(s, r, tenant_id, programme_id, body))

    @router.post("/tenants/{tenant_id}/programmes/{programme_id}/lifecycle", tags=["academics"], operation_id="setProgrammeLifecycle")
    def post_programme_lifecycle(tenant_id: UUID, programme_id: UUID, body: LifecycleUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> ProgrammeSummary:
        return _run_mutation(access, bearer, "programme.lifecycle", _ref(tenant_id, "programme", programme_id), lambda s, r: service.set_programme_lifecycle(s, r, tenant_id, programme_id, body))

    # --- Academic Years ---
    @router.post("/tenants/{tenant_id}/academic-years", tags=["academics"], operation_id="createAcademicYear", status_code=201)
    def post_academic_year(tenant_id: UUID, body: AcademicYearCreate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> AcademicYearSummary:
        return _run_mutation(access, bearer, "academic_year.create", _inst_ref(tenant_id), lambda s, r: service.create_academic_year(s, r, tenant_id, body))

    @router.get("/tenants/{tenant_id}/academic-years", tags=["academics"], operation_id="listAcademicYears")
    def get_academic_years(tenant_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep], active: bool | None = None, cursor: UUID | None = None, limit: int = Query(default=50, ge=1, le=100)) -> Page[AcademicYearSummary]:
        return _run_read(access, bearer, "academic_year.list", _inst_ref(tenant_id), lambda _s, r: service.list_academic_years(r, tenant_id, active=active, cursor=cursor, limit=limit))

    @router.get("/tenants/{tenant_id}/academic-years/{academic_year_id}", tags=["academics"], operation_id="readAcademicYear")
    def get_academic_year(tenant_id: UUID, academic_year_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> AcademicYearSummary:
        return _run_read(access, bearer, "academic_year.read", _ref(tenant_id, "academic_year", academic_year_id), lambda _s, r: service.read_academic_year(r, tenant_id, academic_year_id))

    @router.patch("/tenants/{tenant_id}/academic-years/{academic_year_id}", tags=["academics"], operation_id="updateAcademicYear")
    def patch_academic_year(tenant_id: UUID, academic_year_id: UUID, body: AcademicYearUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> AcademicYearSummary:
        return _run_mutation(access, bearer, "academic_year.update", _ref(tenant_id, "academic_year", academic_year_id), lambda s, r: service.update_academic_year(s, r, tenant_id, academic_year_id, body))

    @router.post("/tenants/{tenant_id}/academic-years/{academic_year_id}/lifecycle", tags=["academics"], operation_id="setAcademicYearLifecycle")
    def post_academic_year_lifecycle(tenant_id: UUID, academic_year_id: UUID, body: LifecycleUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> AcademicYearSummary:
        return _run_mutation(access, bearer, "academic_year.lifecycle", _ref(tenant_id, "academic_year", academic_year_id), lambda s, r: service.set_academic_year_lifecycle(s, r, tenant_id, academic_year_id, body))

    # --- Semesters ---
    @router.post("/tenants/{tenant_id}/semesters", tags=["academics"], operation_id="createSemester", status_code=201)
    def post_semester(tenant_id: UUID, body: SemesterCreate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> SemesterSummary:
        return _run_mutation(access, bearer, "academic_year.semester.create", _ref(tenant_id, "academic_year", body.academic_year_id), lambda s, r: service.create_semester(s, r, tenant_id, body))

    @router.get("/tenants/{tenant_id}/semesters", tags=["academics"], operation_id="listSemesters")
    def get_semesters(tenant_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep], academic_year_id: UUID | None = None, active: bool | None = None, cursor: UUID | None = None, limit: int = Query(default=50, ge=1, le=100)) -> Page[SemesterSummary]:
        return _run_read(access, bearer, "semester.list", _inst_ref(tenant_id), lambda _s, r: service.list_semesters(r, tenant_id, academic_year_id=academic_year_id, active=active, cursor=cursor, limit=limit))

    @router.get("/tenants/{tenant_id}/semesters/{semester_id}", tags=["academics"], operation_id="readSemester")
    def get_semester(tenant_id: UUID, semester_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> SemesterSummary:
        return _run_read(access, bearer, "semester.read", _ref(tenant_id, "semester", semester_id), lambda _s, r: service.read_semester(r, tenant_id, semester_id))

    @router.patch("/tenants/{tenant_id}/semesters/{semester_id}", tags=["academics"], operation_id="updateSemester")
    def patch_semester(tenant_id: UUID, semester_id: UUID, body: SemesterUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> SemesterSummary:
        return _run_mutation(access, bearer, "semester.update", _ref(tenant_id, "semester", semester_id), lambda s, r: service.update_semester(s, r, tenant_id, semester_id, body))

    @router.post("/tenants/{tenant_id}/semesters/{semester_id}/lifecycle", tags=["academics"], operation_id="setSemesterLifecycle")
    def post_semester_lifecycle(tenant_id: UUID, semester_id: UUID, body: LifecycleUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> SemesterSummary:
        return _run_mutation(access, bearer, "semester.lifecycle", _ref(tenant_id, "semester", semester_id), lambda s, r: service.set_semester_lifecycle(s, r, tenant_id, semester_id, body))

    # --- Sections ---
    @router.post("/tenants/{tenant_id}/sections", tags=["academics"], operation_id="createSection", status_code=201)
    def post_section(tenant_id: UUID, body: SectionCreate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> SectionSummary:
        return _run_mutation(access, bearer, "semester.section.create", _ref(tenant_id, "semester", body.semester_id), lambda s, r: service.create_section(s, r, tenant_id, body))

    @router.get("/tenants/{tenant_id}/sections", tags=["academics"], operation_id="listSections")
    def get_sections(tenant_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep], semester_id: UUID | None = None, programme_id: UUID | None = None, active: bool | None = None, cursor: UUID | None = None, limit: int = Query(default=50, ge=1, le=100)) -> Page[SectionSummary]:
        return _run_read(access, bearer, "section.list", _inst_ref(tenant_id), lambda s, r: service.list_sections(s, r, tenant_id, semester_id=semester_id, programme_id=programme_id, active=active, cursor=cursor, limit=limit))

    @router.get("/tenants/{tenant_id}/sections/{section_id}", tags=["academics"], operation_id="readSection")
    def get_section(tenant_id: UUID, section_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> SectionSummary:
        return _run_read(access, bearer, "section.read", _ref(tenant_id, "section", section_id), lambda _s, r: service.read_section(r, tenant_id, section_id))

    @router.patch("/tenants/{tenant_id}/sections/{section_id}", tags=["academics"], operation_id="updateSection")
    def patch_section(tenant_id: UUID, section_id: UUID, body: SectionUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> SectionSummary:
        action = "section.update" if ("semester_id" in body.model_fields_set or "programme_id" in body.model_fields_set) else "section.update_metadata"
        return _run_mutation(access, bearer, action, _ref(tenant_id, "section", section_id), lambda s, r: service.update_section(s, r, tenant_id, section_id, body))

    @router.post("/tenants/{tenant_id}/sections/{section_id}/lifecycle", tags=["academics"], operation_id="setSectionLifecycle")
    def post_section_lifecycle(tenant_id: UUID, section_id: UUID, body: LifecycleUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> SectionSummary:
        return _run_mutation(access, bearer, "section.lifecycle", _ref(tenant_id, "section", section_id), lambda s, r: service.set_section_lifecycle(s, r, tenant_id, section_id, body))

    # --- Subjects ---
    @router.post("/tenants/{tenant_id}/subjects", tags=["academics"], operation_id="createSubject", status_code=201)
    def post_subject(tenant_id: UUID, body: SubjectCreate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> SubjectSummary:
        action = "department.subject.create" if body.department_id is not None else "subject.create"
        ref = _ref(tenant_id, "department", body.department_id) if body.department_id is not None else _inst_ref(tenant_id)
        return _run_mutation(access, bearer, action, ref, lambda s, r: service.create_subject(s, r, tenant_id, body))

    @router.get("/tenants/{tenant_id}/subjects", tags=["academics"], operation_id="listSubjects")
    def get_subjects(tenant_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep], department_id: UUID | None = None, active: bool | None = None, cursor: UUID | None = None, limit: int = Query(default=50, ge=1, le=100)) -> Page[SubjectSummary]:
        return _run_read(access, bearer, "subject.list", _inst_ref(tenant_id), lambda s, r: service.list_subjects(s, r, tenant_id, department_id=department_id, active=active, cursor=cursor, limit=limit))

    @router.get("/tenants/{tenant_id}/subjects/{subject_id}", tags=["academics"], operation_id="readSubject")
    def get_subject(tenant_id: UUID, subject_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> SubjectSummary:
        return _run_read(access, bearer, "subject.read", _ref(tenant_id, "subject", subject_id), lambda _s, r: service.read_subject(r, tenant_id, subject_id))

    @router.patch("/tenants/{tenant_id}/subjects/{subject_id}", tags=["academics"], operation_id="updateSubject")
    def patch_subject(tenant_id: UUID, subject_id: UUID, body: SubjectUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> SubjectSummary:
        return _run_mutation(access, bearer, "subject.update", _ref(tenant_id, "subject", subject_id), lambda s, r: service.update_subject(s, r, tenant_id, subject_id, body))

    @router.post("/tenants/{tenant_id}/subjects/{subject_id}/lifecycle", tags=["academics"], operation_id="setSubjectLifecycle")
    def post_subject_lifecycle(tenant_id: UUID, subject_id: UUID, body: LifecycleUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> SubjectSummary:
        return _run_mutation(access, bearer, "subject.lifecycle", _ref(tenant_id, "subject", subject_id), lambda s, r: service.set_subject_lifecycle(s, r, tenant_id, subject_id, body))

    # --- Subject Offerings ---
    @router.post("/tenants/{tenant_id}/offerings", tags=["academics"], operation_id="createOffering", status_code=201)
    def post_offering(tenant_id: UUID, body: SubjectOfferingCreate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> SubjectOfferingSummary:
        return _run_mutation(access, bearer, "section.offering.create", _ref(tenant_id, "section", body.section_id), lambda s, r: service.create_offering(s, r, tenant_id, body))

    @router.get("/tenants/{tenant_id}/offerings", tags=["academics"], operation_id="listOfferings")
    def get_offerings(tenant_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep], section_id: UUID | None = None, subject_id: UUID | None = None, active: bool | None = None, cursor: UUID | None = None, limit: int = Query(default=50, ge=1, le=100)) -> Page[SubjectOfferingSummary]:
        return _run_read(access, bearer, "offering.list", _inst_ref(tenant_id), lambda s, r: service.list_offerings(s, r, tenant_id, section_id=section_id, subject_id=subject_id, active=active, cursor=cursor, limit=limit))

    @router.get("/tenants/{tenant_id}/offerings/{offering_id}", tags=["academics"], operation_id="readOffering")
    def get_offering(tenant_id: UUID, offering_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> SubjectOfferingSummary:
        return _run_read(access, bearer, "offering.catalog.read", _ref(tenant_id, "offering", offering_id), lambda _s, r: service.read_offering(r, tenant_id, offering_id))

    @router.patch("/tenants/{tenant_id}/offerings/{offering_id}", tags=["academics"], operation_id="updateOffering")
    def patch_offering(tenant_id: UUID, offering_id: UUID, body: SubjectOfferingUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> SubjectOfferingSummary:
        return _run_mutation(access, bearer, "offering.update", _ref(tenant_id, "offering", offering_id), lambda s, r: service.update_offering(s, r, tenant_id, offering_id, body))

    @router.post("/tenants/{tenant_id}/offerings/{offering_id}/lifecycle", tags=["academics"], operation_id="setOfferingLifecycle")
    def post_offering_lifecycle(tenant_id: UUID, offering_id: UUID, body: LifecycleUpdate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> SubjectOfferingSummary:
        return _run_mutation(access, bearer, "offering.lifecycle", _ref(tenant_id, "offering", offering_id), lambda s, r: service.set_offering_lifecycle(s, r, tenant_id, offering_id, body))

    # --- Academic Assignments ---
    @router.post("/tenants/{tenant_id}/departments/{department_id}/heads", tags=["assignments"], operation_id="assignDepartmentHead", status_code=201)
    def post_department_head(tenant_id: UUID, department_id: UUID, body: AssignmentCreate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> DepartmentHeadSummary:
        return _run_mutation(access, bearer, "department.head.assign", _ref(tenant_id, "department", department_id), lambda s, r: service.assign_department_head(s, r, _identities(s), tenant_id, department_id, body))

    @router.get("/tenants/{tenant_id}/departments/{department_id}/heads", tags=["assignments"], operation_id="listDepartmentHeads")
    def get_department_heads(tenant_id: UUID, department_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep], active: bool | None = None, cursor: UUID | None = None, limit: int = Query(default=50, ge=1, le=100)) -> Page[DepartmentHeadSummary]:
        return _run_read(access, bearer, "department.head.list", _ref(tenant_id, "department", department_id), lambda _s, r: service.list_department_heads(r, tenant_id, department_id, active=active, cursor=cursor, limit=limit))

    @router.post("/tenants/{tenant_id}/departments/{department_id}/heads/{user_id}/revoke", tags=["assignments"], operation_id="revokeDepartmentHead")
    def post_revoke_department_head(tenant_id: UUID, department_id: UUID, user_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> DepartmentHeadSummary:
        return _run_mutation(access, bearer, "department.head.revoke", _ref(tenant_id, "department", department_id), lambda s, r: service.revoke_department_head(s, r, tenant_id, department_id, user_id))

    @router.post("/tenants/{tenant_id}/sections/{section_id}/coordinators", tags=["assignments"], operation_id="assignSectionCoordinator", status_code=201)
    def post_section_coordinator(tenant_id: UUID, section_id: UUID, body: AssignmentCreate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> ClassCoordinatorSummary:
        return _run_mutation(access, bearer, "section.coordinator.assign", _ref(tenant_id, "section", section_id), lambda s, r: service.assign_section_coordinator(s, r, _identities(s), tenant_id, section_id, body))

    @router.get("/tenants/{tenant_id}/sections/{section_id}/coordinators", tags=["assignments"], operation_id="listSectionCoordinators")
    def get_section_coordinators(tenant_id: UUID, section_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep], active: bool | None = None, cursor: UUID | None = None, limit: int = Query(default=50, ge=1, le=100)) -> Page[ClassCoordinatorSummary]:
        return _run_read(access, bearer, "section.coordinator.list", _ref(tenant_id, "section", section_id), lambda _s, r: service.list_section_coordinators(r, tenant_id, section_id, active=active, cursor=cursor, limit=limit))

    @router.post("/tenants/{tenant_id}/sections/{section_id}/coordinators/{user_id}/revoke", tags=["assignments"], operation_id="revokeSectionCoordinator")
    def post_revoke_section_coordinator(tenant_id: UUID, section_id: UUID, user_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> ClassCoordinatorSummary:
        return _run_mutation(access, bearer, "section.coordinator.revoke", _ref(tenant_id, "section", section_id), lambda s, r: service.revoke_section_coordinator(s, r, tenant_id, section_id, user_id))

    @router.post("/tenants/{tenant_id}/offerings/{offering_id}/teachers", tags=["assignments"], operation_id="assignOfferingTeacher", status_code=201)
    def post_offering_teacher(tenant_id: UUID, offering_id: UUID, body: AssignmentCreate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> OfferingAssignmentSummary:
        return _run_mutation(access, bearer, "offering.teacher.assign", _ref(tenant_id, "offering", offering_id), lambda s, r: service.assign_offering_teacher(s, r, _identities(s), tenant_id, offering_id, body))

    @router.get("/tenants/{tenant_id}/offerings/{offering_id}/teachers", tags=["assignments"], operation_id="listOfferingTeachers")
    def get_offering_teachers(tenant_id: UUID, offering_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep], active: bool | None = None, cursor: UUID | None = None, limit: int = Query(default=50, ge=1, le=100)) -> Page[OfferingAssignmentSummary]:
        return _run_read(access, bearer, "offering.teacher.list", _ref(tenant_id, "offering", offering_id), lambda _s, r: service.list_offering_teachers(r, tenant_id, offering_id, active=active, cursor=cursor, limit=limit))

    @router.post("/tenants/{tenant_id}/offerings/{offering_id}/teachers/{user_id}/revoke", tags=["assignments"], operation_id="revokeOfferingTeacher")
    def post_revoke_offering_teacher(tenant_id: UUID, offering_id: UUID, user_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> OfferingAssignmentSummary:
        return _run_mutation(access, bearer, "offering.teacher.revoke", _ref(tenant_id, "offering", offering_id), lambda s, r: service.revoke_offering_teacher(s, r, tenant_id, offering_id, user_id))

    @router.post("/tenants/{tenant_id}/offerings/{offering_id}/enrolments", tags=["assignments"], operation_id="enrolStudent", status_code=201)
    def post_enrol_student(tenant_id: UUID, offering_id: UUID, body: AssignmentCreate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> OfferingAssignmentSummary:
        return _run_mutation(access, bearer, "offering.enrolment.create", _ref(tenant_id, "offering", offering_id), lambda s, r: service.enrol_student(s, r, _identities(s), tenant_id, offering_id, body))

    @router.get("/tenants/{tenant_id}/offerings/{offering_id}/enrolments", tags=["assignments"], operation_id="listOfferingEnrolments")
    def get_offering_enrolments(tenant_id: UUID, offering_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep], active: bool | None = None, cursor: UUID | None = None, limit: int = Query(default=50, ge=1, le=100)) -> Page[OfferingAssignmentSummary]:
        return _run_read(access, bearer, "offering.enrolment.list", _ref(tenant_id, "offering", offering_id), lambda _s, r: service.list_offering_enrolments(r, tenant_id, offering_id, active=active, cursor=cursor, limit=limit))

    @router.post("/tenants/{tenant_id}/offerings/{offering_id}/enrolments/{user_id}/withdraw", tags=["assignments"], operation_id="withdrawStudent")
    def post_withdraw_student(tenant_id: UUID, offering_id: UUID, user_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> OfferingAssignmentSummary:
        return _run_mutation(access, bearer, "offering.enrolment.withdraw", _ref(tenant_id, "offering", offering_id), lambda s, r: service.withdraw_student(s, r, tenant_id, offering_id, user_id))

    # --- Guardian Relationships & Minimal Student Summary ---
    @router.post("/tenants/{tenant_id}/guardian-links", tags=["guardians"], operation_id="createGuardianLink", status_code=201)
    def post_guardian_link(tenant_id: UUID, body: GuardianLinkCreate, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> GuardianLinkSummary:
        return _run_mutation(access, bearer, "guardian_link.create", _inst_ref(tenant_id), lambda s, r: service.create_guardian_link(s, r, _identities(s), tenant_id, body))

    @router.get("/tenants/{tenant_id}/guardian-links", tags=["guardians"], operation_id="listGuardianLinks")
    def get_guardian_links(tenant_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep], active: bool | None = None, cursor: UUID | None = None, limit: int = Query(default=50, ge=1, le=100)) -> Page[GuardianLinkSummary]:
        return _run_read(access, bearer, "guardian_link.list", _inst_ref(tenant_id), lambda s, r: service.list_guardian_links(s, r, tenant_id, active=active, cursor=cursor, limit=limit))

    @router.post("/tenants/{tenant_id}/guardian-links/{guardian_user_id}/students/{student_user_id}/verify", tags=["guardians"], operation_id="verifyGuardianLink")
    def post_verify_guardian_link(tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> GuardianLinkSummary:
        return _run_mutation(access, bearer, "guardian_link.verify", _ref(tenant_id, "guardian_link", student_user_id), lambda s, r: service.verify_guardian_link(s, r, tenant_id, guardian_user_id, student_user_id))

    @router.post("/tenants/{tenant_id}/guardian-links/{guardian_user_id}/students/{student_user_id}/consent", tags=["guardians"], operation_id="consentGuardianLink")
    def post_consent_guardian_link(tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> GuardianLinkSummary:
        return _run_mutation(access, bearer, "guardian_link.consent", _ref(tenant_id, "guardian_link", student_user_id), lambda s, r: service.consent_guardian_link(s, r, tenant_id, guardian_user_id, student_user_id))

    @router.post("/tenants/{tenant_id}/guardian-links/{guardian_user_id}/students/{student_user_id}/activate", tags=["guardians"], operation_id="activateGuardianLink")
    def post_activate_guardian_link(tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> GuardianLinkSummary:
        return _run_mutation(access, bearer, "guardian_link.activate", _ref(tenant_id, "guardian_link", student_user_id), lambda s, r: service.activate_guardian_link(s, r, _identities(s), tenant_id, guardian_user_id, student_user_id))

    @router.post("/tenants/{tenant_id}/guardian-links/{guardian_user_id}/students/{student_user_id}/revoke", tags=["guardians"], operation_id="revokeGuardianLink")
    def post_revoke_guardian_link(tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> GuardianLinkSummary:
        return _run_mutation(access, bearer, "guardian_link.revoke", _ref(tenant_id, "guardian_link", student_user_id), lambda s, r: service.revoke_guardian_link(s, r, tenant_id, guardian_user_id, student_user_id))

    @router.get("/tenants/{tenant_id}/students/{student_id}/summary", tags=["guardians"], operation_id="readStudentSummary")
    def get_student_summary(tenant_id: UUID, student_id: UUID, bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep]) -> StudentSummary:
        return _run_read(access, bearer, "student.summary.read", _ref(tenant_id, "student_summary", student_id), lambda s, _r: service.read_student_summary(_identities(s), tenant_id, student_id))

    return router
