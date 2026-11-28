from typing import Protocol
from uuid import UUID

from ..contracts import Page
from .schemas import (
    AcademicContext,
    AcademicYearSummary,
    CampusSummary,
    ClassCoordinatorSummary,
    DepartmentHeadSummary,
    DepartmentSummary,
    GuardianLinkSummary,
    InstitutionInfo,
    OfferingAssignment,
    OfferingAssignmentSummary,
    ProgrammeSummary,
    SectionSummary,
    SemesterSummary,
    SubjectOfferingSummary,
    SubjectSummary,
)


class InstitutionDirectory(Protocol):
    """Internal read contracts. Callers authorize the actor before disclosing results."""

    def resolve_institution(self, tenant_id: UUID) -> InstitutionInfo | None: ...

    def academic_context(
        self, tenant_id: UUID, offering_id: UUID
    ) -> AcademicContext | None: ...

    def teacher_assignment(
        self, tenant_id: UUID, user_id: UUID, offering_id: UUID
    ) -> OfferingAssignment | None: ...

    def student_enrolment(
        self, tenant_id: UUID, user_id: UUID, offering_id: UUID
    ) -> OfferingAssignment | None: ...

    def heads_department(
        self, tenant_id: UUID, user_id: UUID, department_id: UUID
    ) -> bool: ...

    def coordinates_section(
        self, tenant_id: UUID, user_id: UUID, section_id: UUID
    ) -> bool: ...

    def guardian_link(
        self, tenant_id: UUID, user_id: UUID, student_id: UUID
    ) -> bool: ...

    def entity_active(
        self, tenant_id: UUID, kind: str, entity_id: UUID
    ) -> bool | None: ...

    def entity_department(
        self, tenant_id: UUID, kind: str, entity_id: UUID
    ) -> UUID | None: ...

    def offering_section(self, tenant_id: UUID, offering_id: UUID) -> UUID | None: ...

    def section_active(self, tenant_id: UUID, section_id: UUID) -> bool: ...

    def has_guardian_link_record(
        self, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
    ) -> bool: ...

    def coordinator_has_student(
        self, tenant_id: UUID, coordinator_user_id: UUID, student_user_id: UUID
    ) -> bool: ...

    def teacher_has_student(
        self, tenant_id: UUID, teacher_user_id: UUID, student_user_id: UUID
    ) -> bool: ...


class AcademicRepository(Protocol):
    def lock_resource(self, tenant_id: UUID, kind: str, resource_id: UUID) -> None: ...

    def get_institution(self, tenant_id: UUID) -> InstitutionInfo | None: ...

    def update_institution(
        self, tenant_id: UUID, *, name: str
    ) -> InstitutionInfo | None: ...

    def list_campuses(
        self,
        tenant_id: UUID,
        *,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[CampusSummary]: ...

    def get_campus(self, tenant_id: UUID, campus_id: UUID) -> CampusSummary | None: ...

    def create_campus(
        self, tenant_id: UUID, campus_id: UUID, name: str
    ) -> CampusSummary: ...

    def update_campus(
        self, tenant_id: UUID, campus_id: UUID, *, name: str | None = None
    ) -> CampusSummary | None: ...

    def set_campus_active(
        self, tenant_id: UUID, campus_id: UUID, active: bool
    ) -> CampusSummary | None: ...

    def has_active_campus_children(self, tenant_id: UUID, campus_id: UUID) -> bool: ...

    def list_departments(
        self,
        tenant_id: UUID,
        *,
        actor_id: UUID | None,
        campus_id: UUID | None,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[DepartmentSummary]: ...

    def get_department(
        self, tenant_id: UUID, department_id: UUID
    ) -> DepartmentSummary | None: ...

    def create_department(
        self,
        tenant_id: UUID,
        department_id: UUID,
        *,
        name: str,
        campus_id: UUID | None,
    ) -> DepartmentSummary: ...

    def update_department(
        self,
        tenant_id: UUID,
        department_id: UUID,
        *,
        name: str | None = None,
        campus_id: UUID | None = None,
        update_campus: bool = False,
    ) -> DepartmentSummary | None: ...

    def set_department_active(
        self, tenant_id: UUID, department_id: UUID, active: bool
    ) -> DepartmentSummary | None: ...

    def has_active_department_children(
        self, tenant_id: UUID, department_id: UUID
    ) -> bool: ...

    def list_programmes(
        self,
        tenant_id: UUID,
        *,
        actor_id: UUID | None,
        department_id: UUID | None,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[ProgrammeSummary]: ...

    def get_programme(
        self, tenant_id: UUID, programme_id: UUID
    ) -> ProgrammeSummary | None: ...

    def create_programme(
        self,
        tenant_id: UUID,
        programme_id: UUID,
        *,
        name: str,
        department_id: UUID | None,
    ) -> ProgrammeSummary: ...

    def update_programme(
        self,
        tenant_id: UUID,
        programme_id: UUID,
        *,
        name: str | None = None,
        department_id: UUID | None = None,
        update_department: bool = False,
    ) -> ProgrammeSummary | None: ...

    def set_programme_active(
        self, tenant_id: UUID, programme_id: UUID, active: bool
    ) -> ProgrammeSummary | None: ...

    def has_active_programme_children(
        self, tenant_id: UUID, programme_id: UUID
    ) -> bool: ...

    def list_academic_years(
        self,
        tenant_id: UUID,
        *,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[AcademicYearSummary]: ...

    def get_academic_year(
        self, tenant_id: UUID, academic_year_id: UUID
    ) -> AcademicYearSummary | None: ...

    def create_academic_year(
        self, tenant_id: UUID, academic_year_id: UUID, name: str
    ) -> AcademicYearSummary: ...

    def update_academic_year(
        self, tenant_id: UUID, academic_year_id: UUID, *, name: str | None = None
    ) -> AcademicYearSummary | None: ...

    def set_academic_year_active(
        self, tenant_id: UUID, academic_year_id: UUID, active: bool
    ) -> AcademicYearSummary | None: ...

    def has_active_academic_year_children(
        self, tenant_id: UUID, academic_year_id: UUID
    ) -> bool: ...

    def list_semesters(
        self,
        tenant_id: UUID,
        *,
        academic_year_id: UUID | None,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[SemesterSummary]: ...

    def get_semester(
        self, tenant_id: UUID, semester_id: UUID
    ) -> SemesterSummary | None: ...

    def create_semester(
        self,
        tenant_id: UUID,
        semester_id: UUID,
        *,
        name: str,
        academic_year_id: UUID,
    ) -> SemesterSummary: ...

    def update_semester(
        self,
        tenant_id: UUID,
        semester_id: UUID,
        *,
        name: str | None = None,
        academic_year_id: UUID | None = None,
    ) -> SemesterSummary | None: ...

    def set_semester_active(
        self, tenant_id: UUID, semester_id: UUID, active: bool
    ) -> SemesterSummary | None: ...

    def has_active_semester_children(
        self, tenant_id: UUID, semester_id: UUID
    ) -> bool: ...

    def list_sections(
        self,
        tenant_id: UUID,
        *,
        actor_id: UUID | None,
        semester_id: UUID | None,
        programme_id: UUID | None,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[SectionSummary]: ...

    def get_section(
        self, tenant_id: UUID, section_id: UUID
    ) -> SectionSummary | None: ...

    def create_section(
        self,
        tenant_id: UUID,
        section_id: UUID,
        *,
        name: str,
        semester_id: UUID,
        programme_id: UUID | None,
    ) -> SectionSummary: ...

    def update_section(
        self,
        tenant_id: UUID,
        section_id: UUID,
        *,
        name: str | None = None,
        semester_id: UUID | None = None,
        programme_id: UUID | None = None,
        update_programme: bool = False,
    ) -> SectionSummary | None: ...

    def set_section_active(
        self, tenant_id: UUID, section_id: UUID, active: bool
    ) -> SectionSummary | None: ...

    def has_active_section_children(
        self, tenant_id: UUID, section_id: UUID
    ) -> bool: ...

    def list_subjects(
        self,
        tenant_id: UUID,
        *,
        actor_id: UUID | None,
        department_id: UUID | None,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[SubjectSummary]: ...

    def get_subject(
        self, tenant_id: UUID, subject_id: UUID
    ) -> SubjectSummary | None: ...

    def create_subject(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        *,
        name: str,
        department_id: UUID | None,
    ) -> SubjectSummary: ...

    def update_subject(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        *,
        name: str | None = None,
        department_id: UUID | None = None,
        update_department: bool = False,
    ) -> SubjectSummary | None: ...

    def set_subject_active(
        self, tenant_id: UUID, subject_id: UUID, active: bool
    ) -> SubjectSummary | None: ...

    def has_active_subject_children(
        self, tenant_id: UUID, subject_id: UUID
    ) -> bool: ...

    def list_offerings(
        self,
        tenant_id: UUID,
        *,
        actor_id: UUID | None,
        roles: frozenset[str],
        section_id: UUID | None,
        subject_id: UUID | None,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[SubjectOfferingSummary]: ...

    def get_offering(
        self, tenant_id: UUID, offering_id: UUID
    ) -> SubjectOfferingSummary | None: ...

    def create_offering(
        self,
        tenant_id: UUID,
        offering_id: UUID,
        *,
        name: str,
        section_id: UUID,
        subject_id: UUID,
    ) -> SubjectOfferingSummary: ...

    def update_offering(
        self,
        tenant_id: UUID,
        offering_id: UUID,
        *,
        name: str | None = None,
        section_id: UUID | None = None,
        subject_id: UUID | None = None,
    ) -> SubjectOfferingSummary | None: ...

    def set_offering_active(
        self, tenant_id: UUID, offering_id: UUID, active: bool
    ) -> SubjectOfferingSummary | None: ...

    def has_duplicate_offering(
        self,
        tenant_id: UUID,
        *,
        section_id: UUID,
        subject_id: UUID,
        name: str,
        exclude_id: UUID | None = None,
    ) -> bool: ...

    def list_department_heads(
        self,
        tenant_id: UUID,
        department_id: UUID,
        *,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[DepartmentHeadSummary]: ...

    def assign_department_head(
        self, tenant_id: UUID, department_id: UUID, user_id: UUID
    ) -> DepartmentHeadSummary: ...

    def revoke_department_head(
        self, tenant_id: UUID, department_id: UUID, user_id: UUID
    ) -> DepartmentHeadSummary | None: ...

    def list_section_coordinators(
        self,
        tenant_id: UUID,
        section_id: UUID,
        *,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[ClassCoordinatorSummary]: ...

    def assign_section_coordinator(
        self, tenant_id: UUID, section_id: UUID, user_id: UUID
    ) -> ClassCoordinatorSummary: ...

    def revoke_section_coordinator(
        self, tenant_id: UUID, section_id: UUID, user_id: UUID
    ) -> ClassCoordinatorSummary | None: ...

    def list_offering_teachers(
        self,
        tenant_id: UUID,
        offering_id: UUID,
        *,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[OfferingAssignmentSummary]: ...

    def assign_offering_teacher(
        self, tenant_id: UUID, offering_id: UUID, user_id: UUID
    ) -> OfferingAssignmentSummary: ...

    def revoke_offering_teacher(
        self, tenant_id: UUID, offering_id: UUID, user_id: UUID
    ) -> OfferingAssignmentSummary | None: ...

    def list_offering_enrolments(
        self,
        tenant_id: UUID,
        offering_id: UUID,
        *,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[OfferingAssignmentSummary]: ...

    def enrol_student(
        self, tenant_id: UUID, offering_id: UUID, user_id: UUID
    ) -> OfferingAssignmentSummary: ...

    def withdraw_student(
        self, tenant_id: UUID, offering_id: UUID, user_id: UUID
    ) -> OfferingAssignmentSummary | None: ...

    def list_guardian_links(
        self,
        tenant_id: UUID,
        *,
        actor_id: UUID | None,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[GuardianLinkSummary]: ...

    def create_guardian_link(
        self, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
    ) -> GuardianLinkSummary: ...

    def verify_guardian_link(
        self, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
    ) -> GuardianLinkSummary | None: ...

    def consent_guardian_link(
        self, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
    ) -> GuardianLinkSummary | None: ...

    def activate_guardian_link(
        self, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
    ) -> GuardianLinkSummary | None: ...

    def revoke_guardian_link(
        self, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
    ) -> GuardianLinkSummary | None: ...
