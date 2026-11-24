from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..identity.public import IdentityDirectory, InstitutionRole
from .models import (
    ClassCoordinator,
    DepartmentHead,
    GuardianLink,
    Institution,
    Section,
    Semester,
    StudentEnrolment,
    SubjectOffering,
    TeacherAssignment,
)
from .schemas import AcademicContext, InstitutionInfo, OfferingAssignment


class SqlInstitutionStatus:
    def __init__(self, session: Session) -> None:
        self.session = session

    def is_active(self, tenant_id: UUID) -> bool:
        return (
            self.session.scalar(
                select(Institution.active).where(Institution.tenant_id == tenant_id)
            )
            is True
        )


class SqlInstitutionDirectory:
    def __init__(self, session: Session, identities: IdentityDirectory) -> None:
        self.session = session
        self.identities = identities

    def resolve_institution(self, tenant_id: UUID) -> InstitutionInfo | None:
        row = self.session.scalar(
            select(Institution)
            .where(Institution.tenant_id == tenant_id)
            .execution_options(populate_existing=True)
        )
        return (
            None
            if row is None
            else InstitutionInfo(
                tenant_id=row.tenant_id, name=row.name, active=row.active
            )
        )

    def academic_context(
        self, tenant_id: UUID, offering_id: UUID
    ) -> AcademicContext | None:
        query = (
            select(
                SubjectOffering.subject_id,
                Section.id,
                Section.programme_id,
                Section.semester_id,
                Semester.academic_year_id,
            )
            .select_from(SubjectOffering)
            .join(
                Section,
                and_(
                    Section.tenant_id == SubjectOffering.tenant_id,
                    Section.id == SubjectOffering.section_id,
                ),
            )
            .join(
                Semester,
                and_(
                    Semester.tenant_id == Section.tenant_id,
                    Semester.id == Section.semester_id,
                ),
            )
            .join(Institution, Institution.tenant_id == SubjectOffering.tenant_id)
            .where(
                SubjectOffering.tenant_id == tenant_id,
                SubjectOffering.id == offering_id,
                Institution.active.is_(True),
            )
        )
        row = self.session.execute(query).one_or_none()
        if row is None:
            return None
        subject_id, section_id, programme_id, semester_id, academic_year_id = row
        return AcademicContext(
            tenant_id=tenant_id,
            offering_id=offering_id,
            subject_id=subject_id,
            section_id=section_id,
            programme_id=programme_id,
            semester_id=semester_id,
            academic_year_id=academic_year_id,
        )

    def _eligible(self, tenant_id: UUID, user_id: UUID, role: InstitutionRole) -> bool:
        return role in self.identities.applicable_roles(user_id, tenant_id)

    def teacher_assignment(
        self, tenant_id: UUID, user_id: UUID, offering_id: UUID
    ) -> OfferingAssignment | None:
        if not self._eligible(tenant_id, user_id, InstitutionRole.subject_teacher):
            return None
        query = select(TeacherAssignment.active).where(
            TeacherAssignment.tenant_id == tenant_id,
            TeacherAssignment.user_id == user_id,
            TeacherAssignment.offering_id == offering_id,
        )
        if self.session.scalar(query) is not True:
            return None
        return OfferingAssignment(
            tenant_id=tenant_id, user_id=user_id, offering_id=offering_id
        )

    def student_enrolment(
        self, tenant_id: UUID, user_id: UUID, offering_id: UUID
    ) -> OfferingAssignment | None:
        if not self._eligible(tenant_id, user_id, InstitutionRole.student):
            return None
        query = select(StudentEnrolment.active).where(
            StudentEnrolment.tenant_id == tenant_id,
            StudentEnrolment.user_id == user_id,
            StudentEnrolment.offering_id == offering_id,
        )
        if self.session.scalar(query) is not True:
            return None
        return OfferingAssignment(
            tenant_id=tenant_id, user_id=user_id, offering_id=offering_id
        )

    def heads_department(
        self, tenant_id: UUID, user_id: UUID, department_id: UUID
    ) -> bool:
        return (
            self._eligible(tenant_id, user_id, InstitutionRole.department_head)
            and self.session.scalar(
                select(DepartmentHead.active).where(
                    DepartmentHead.tenant_id == tenant_id,
                    DepartmentHead.user_id == user_id,
                    DepartmentHead.department_id == department_id,
                )
            )
            is True
        )

    def coordinates_section(
        self, tenant_id: UUID, user_id: UUID, section_id: UUID
    ) -> bool:
        return (
            self._eligible(tenant_id, user_id, InstitutionRole.class_coordinator)
            and self.session.scalar(
                select(ClassCoordinator.active).where(
                    ClassCoordinator.tenant_id == tenant_id,
                    ClassCoordinator.user_id == user_id,
                    ClassCoordinator.section_id == section_id,
                )
            )
            is True
        )

    def guardian_link(self, tenant_id: UUID, user_id: UUID, student_id: UUID) -> bool:
        if not self._eligible(tenant_id, user_id, InstitutionRole.guardian):
            return False
        if InstitutionRole.student not in self.identities.applicable_roles(
            student_id, tenant_id
        ):
            return False
        query = select(GuardianLink.active).where(
            GuardianLink.tenant_id == tenant_id,
            GuardianLink.user_id == user_id,
            GuardianLink.student_id == student_id,
        )
        return self.session.scalar(query) is True
