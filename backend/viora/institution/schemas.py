from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints

from ..contracts import Contract

NonEmptyName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


class InstitutionInfo(Contract):
    tenant_id: UUID
    name: str
    active: bool


class InstitutionDiscovery(Contract):
    tenant_id: UUID
    name: str
    roles: tuple[str, ...]


class InstitutionUpdate(Contract):
    name: NonEmptyName


class AcademicContext(Contract):
    tenant_id: UUID
    offering_id: UUID
    subject_id: UUID
    section_id: UUID
    programme_id: UUID | None
    semester_id: UUID
    academic_year_id: UUID


class OfferingAssignment(Contract):
    tenant_id: UUID
    user_id: UUID
    offering_id: UUID


class LifecycleUpdate(Contract):
    active: bool


class CampusCreate(Contract):
    name: NonEmptyName


class CampusUpdate(Contract):
    name: NonEmptyName | None = None


class CampusSummary(Contract):
    tenant_id: UUID
    id: UUID
    name: str
    active: bool


class DepartmentCreate(Contract):
    name: NonEmptyName
    campus_id: UUID | None = None


class DepartmentUpdate(Contract):
    name: NonEmptyName | None = None
    campus_id: UUID | None = None


class DepartmentSummary(Contract):
    tenant_id: UUID
    id: UUID
    name: str
    campus_id: UUID | None
    active: bool


class ProgrammeCreate(Contract):
    name: NonEmptyName
    department_id: UUID | None = None


class ProgrammeUpdate(Contract):
    name: NonEmptyName | None = None
    department_id: UUID | None = None


class ProgrammeSummary(Contract):
    tenant_id: UUID
    id: UUID
    name: str
    department_id: UUID | None
    active: bool


class AcademicYearCreate(Contract):
    name: NonEmptyName


class AcademicYearUpdate(Contract):
    name: NonEmptyName | None = None


class AcademicYearSummary(Contract):
    tenant_id: UUID
    id: UUID
    name: str
    active: bool


class SemesterCreate(Contract):
    name: NonEmptyName
    academic_year_id: UUID


class SemesterUpdate(Contract):
    name: NonEmptyName | None = None
    academic_year_id: UUID | None = None


class SemesterSummary(Contract):
    tenant_id: UUID
    id: UUID
    name: str
    academic_year_id: UUID
    active: bool


class SectionCreate(Contract):
    name: NonEmptyName
    semester_id: UUID
    programme_id: UUID | None = None


class SectionUpdate(Contract):
    name: NonEmptyName | None = None
    semester_id: UUID | None = None
    programme_id: UUID | None = None


class SectionSummary(Contract):
    tenant_id: UUID
    id: UUID
    name: str
    semester_id: UUID
    programme_id: UUID | None
    active: bool


class SubjectCreate(Contract):
    name: NonEmptyName
    department_id: UUID | None = None


class SubjectUpdate(Contract):
    name: NonEmptyName | None = None
    department_id: UUID | None = None


class SubjectSummary(Contract):
    tenant_id: UUID
    id: UUID
    name: str
    department_id: UUID | None
    active: bool


class SubjectOfferingCreate(Contract):
    name: NonEmptyName
    section_id: UUID
    subject_id: UUID


class SubjectOfferingUpdate(Contract):
    name: NonEmptyName | None = None
    section_id: UUID | None = None
    subject_id: UUID | None = None


class SubjectOfferingSummary(Contract):
    tenant_id: UUID
    id: UUID
    name: str
    section_id: UUID
    subject_id: UUID
    active: bool


class PageQuery(Contract):
    limit: int = Field(default=50, ge=1, le=100)
    cursor: UUID | None = None


class AssignmentCreate(Contract):
    user_id: UUID


class DepartmentHeadSummary(Contract):
    tenant_id: UUID
    department_id: UUID
    user_id: UUID
    active: bool


class ClassCoordinatorSummary(Contract):
    tenant_id: UUID
    section_id: UUID
    user_id: UUID
    active: bool


class OfferingAssignmentSummary(Contract):
    tenant_id: UUID
    offering_id: UUID
    user_id: UUID
    active: bool


class GuardianLinkCreate(Contract):
    guardian_user_id: UUID
    student_user_id: UUID


class GuardianLinkSummary(Contract):
    tenant_id: UUID
    guardian_user_id: UUID
    student_user_id: UUID
    active: bool
    verified_at: datetime | None
    consent_at: datetime | None


class StudentSummary(Contract):
    tenant_id: UUID
    user_id: UUID
    display_name: str
