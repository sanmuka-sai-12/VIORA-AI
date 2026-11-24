from typing import Protocol
from uuid import UUID

from .schemas import AcademicContext, InstitutionInfo, OfferingAssignment


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
