from uuid import UUID

from ..contracts import Contract


class InstitutionInfo(Contract):
    tenant_id: UUID
    name: str
    active: bool


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
