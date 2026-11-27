from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.orm import Session

from ..contracts import Page
from ..identity.public import IdentityDirectory, InstitutionRole
from ..security import AccessError
from .models import (
    AcademicYear,
    Campus,
    ClassCoordinator,
    Department,
    DepartmentHead,
    GuardianLink,
    Institution,
    Programme,
    Section,
    Semester,
    StudentEnrolment,
    Subject,
    SubjectOffering,
    TeacherAssignment,
)
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

T = TypeVar("T")

_ENTITY_MODELS: dict[str, type[Any]] = {
    "campus": Campus,
    "department": Department,
    "programme": Programme,
    "academic_year": AcademicYear,
    "semester": Semester,
    "section": Section,
    "subject": Subject,
    "offering": SubjectOffering,
}


def _active_section_chain(stmt: Any) -> Any:
    return (
        stmt.join(
            Semester,
            and_(
                Semester.tenant_id == Section.tenant_id,
                Semester.id == Section.semester_id,
            ),
        )
        .join(
            AcademicYear,
            and_(
                AcademicYear.tenant_id == Semester.tenant_id,
                AcademicYear.id == Semester.academic_year_id,
            ),
        )
        .outerjoin(
            Programme,
            and_(
                Programme.tenant_id == Section.tenant_id,
                Programme.id == Section.programme_id,
            ),
        )
        .where(
            Section.active.is_(True),
            Semester.active.is_(True),
            AcademicYear.active.is_(True),
            or_(Section.programme_id.is_(None), Programme.active.is_(True)),
        )
    )


def _dept_head_scope(stmt: Any, model: type[Any], actor_id: UUID) -> Any:
    return (
        stmt.join(
            Department,
            and_(
                Department.tenant_id == model.tenant_id,
                Department.id == model.department_id,
            ),
        )
        .join(
            DepartmentHead,
            and_(
                DepartmentHead.tenant_id == model.tenant_id,
                DepartmentHead.department_id == model.department_id,
                DepartmentHead.user_id == actor_id,
                DepartmentHead.active.is_(True),
            ),
        )
        .where(model.active.is_(True), Department.active.is_(True))
    )


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
        query = _active_section_chain(
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
                Subject,
                and_(
                    Subject.tenant_id == SubjectOffering.tenant_id,
                    Subject.id == SubjectOffering.subject_id,
                ),
            )
            .join(Institution, Institution.tenant_id == SubjectOffering.tenant_id)
        ).where(
            SubjectOffering.tenant_id == tenant_id,
            SubjectOffering.id == offering_id,
            Institution.active.is_(True),
            SubjectOffering.active.is_(True),
            Subject.active.is_(True),
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

    def offering_access_state(
        self,
        tenant_id: UUID,
        user_id: UUID,
        offering_id: UUID,
        *,
        need_teacher: bool = False,
        need_student: bool = False,
    ) -> tuple[bool, bool, bool]:
        """Verify active offering hierarchy and optional active teacher/student links in one query."""
        cols: list[Any] = [SubjectOffering.id]
        if need_teacher:
            cols.append(TeacherAssignment.active)
        if need_student:
            cols.append(StudentEnrolment.active)
        query = (
            _active_section_chain(
                select(*cols)
                .select_from(SubjectOffering)
                .join(
                    Section,
                    and_(
                        Section.tenant_id == SubjectOffering.tenant_id,
                        Section.id == SubjectOffering.section_id,
                    ),
                )
                .join(
                    Subject,
                    and_(
                        Subject.tenant_id == SubjectOffering.tenant_id,
                        Subject.id == SubjectOffering.subject_id,
                    ),
                )
                .join(Institution, Institution.tenant_id == SubjectOffering.tenant_id)
            )
            .where(
                SubjectOffering.tenant_id == tenant_id,
                SubjectOffering.id == offering_id,
                Institution.active.is_(True),
                SubjectOffering.active.is_(True),
                Subject.active.is_(True),
            )
        )
        if need_teacher:
            query = query.outerjoin(
                TeacherAssignment,
                and_(
                    TeacherAssignment.tenant_id == SubjectOffering.tenant_id,
                    TeacherAssignment.offering_id == SubjectOffering.id,
                    TeacherAssignment.user_id == user_id,
                ),
            )
        if need_student:
            query = query.outerjoin(
                StudentEnrolment,
                and_(
                    StudentEnrolment.tenant_id == SubjectOffering.tenant_id,
                    StudentEnrolment.offering_id == SubjectOffering.id,
                    StudentEnrolment.user_id == user_id,
                ),
            )
        row = self.session.execute(query).one_or_none()
        if row is None:
            return False, False, False
        idx = 1
        t_active = bool(row[idx] is True) if need_teacher else False
        if need_teacher:
            idx += 1
        s_active = bool(row[idx] is True) if need_student else False
        return True, t_active, s_active

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
                select(DepartmentHead.active)
                .join(
                    Department,
                    and_(
                        Department.tenant_id == DepartmentHead.tenant_id,
                        Department.id == DepartmentHead.department_id,
                    ),
                )
                .where(
                    DepartmentHead.tenant_id == tenant_id,
                    DepartmentHead.user_id == user_id,
                    DepartmentHead.department_id == department_id,
                    Department.active.is_(True),
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
                _active_section_chain(
                    select(ClassCoordinator.active).join(
                        Section,
                        and_(
                            Section.tenant_id == ClassCoordinator.tenant_id,
                            Section.id == ClassCoordinator.section_id,
                        ),
                    )
                ).where(
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

    def entity_active(
        self, tenant_id: UUID, kind: str, entity_id: UUID
    ) -> bool | None:
        model = _ENTITY_MODELS.get(kind)
        if model is None:
            return None
        return self.session.scalar(
            select(model.active).where(
                model.tenant_id == tenant_id, model.id == entity_id
            )
        )

    def entity_department(
        self, tenant_id: UUID, kind: str, entity_id: UUID
    ) -> UUID | None:
        model = Programme if kind == "programme" else Subject if kind == "subject" else None
        if model is None:
            return None
        return self.session.scalar(
            select(model.department_id).where(
                model.tenant_id == tenant_id, model.id == entity_id
            )
        )

    def offering_section(self, tenant_id: UUID, offering_id: UUID) -> UUID | None:
        return self.session.scalar(
            select(SubjectOffering.section_id).where(
                SubjectOffering.tenant_id == tenant_id,
                SubjectOffering.id == offering_id,
            )
        )

    def section_active(self, tenant_id: UUID, section_id: UUID) -> bool:
        return (
            self.session.scalar(
                _active_section_chain(
                    select(Section.id).join(
                        Institution, Institution.tenant_id == Section.tenant_id
                    )
                )
                .where(
                    Section.tenant_id == tenant_id,
                    Section.id == section_id,
                    Institution.active.is_(True),
                )
                .limit(1)
            )
            is not None
        )

    def has_guardian_link_record(
        self, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
    ) -> bool:
        return (
            self.session.scalar(
                select(GuardianLink.user_id).where(
                    GuardianLink.tenant_id == tenant_id,
                    GuardianLink.user_id == guardian_user_id,
                    GuardianLink.student_id == student_user_id,
                )
            )
            is not None
        )


def _dto(cls: type[T], row: Any) -> T:
    return cls.model_validate(row, from_attributes=True)


class SqlAcademicRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def lock_resource(self, tenant_id: UUID, kind: str, resource_id: UUID) -> None:
        if kind == "institution":
            self.session.scalar(
                select(Institution.tenant_id)
                .where(Institution.tenant_id == tenant_id)
                .with_for_update()
            )
            return
        model = _ENTITY_MODELS.get(kind)
        if model is not None:
            self.session.scalar(
                select(model.id)
                .where(model.tenant_id == tenant_id, model.id == resource_id)
                .with_for_update()
            )
            return
        if kind == "guardian_link":
            self.session.scalars(
                select(GuardianLink.user_id)
                .where(
                    GuardianLink.tenant_id == tenant_id,
                    GuardianLink.student_id == resource_id,
                )
                .order_by(GuardianLink.user_id)
                .with_for_update()
            ).all()

    def _get(
        self, model: type[Any], tenant_id: UUID, entity_id: UUID
    ) -> Any | None:
        return self.session.scalar(
            select(model)
            .where(model.tenant_id == tenant_id, model.id == entity_id)
            .execution_options(populate_existing=True)
        )

    def _paginate(
        self,
        stmt: Any,
        model: type[Any],
        dto_cls: type[T],
        cursor: UUID | None,
        limit: int,
        *,
        cursor_col: Any | None = None,
    ) -> Page[T]:
        key_col = cursor_col or model.id
        if cursor is not None:
            stmt = stmt.where(key_col > cursor)
        rows = list(self.session.scalars(stmt.order_by(key_col).limit(limit + 1)))
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        next_cursor = (
            str(getattr(page_rows[-1], key_col.key))
            if has_more and page_rows
            else None
        )
        return Page[T](
            items=tuple(_dto(dto_cls, row) for row in page_rows),
            next_cursor=next_cursor,
        )

    def _get_dto(
        self, model: type[Any], tenant_id: UUID, entity_id: UUID, dto_cls: type[T]
    ) -> T | None:
        row = self._get(model, tenant_id, entity_id)
        return _dto(dto_cls, row) if row is not None else None

    def _create_dto(self, row: Any, dto_cls: type[T]) -> T:
        self.session.add(row)
        self.session.flush()
        return _dto(dto_cls, row)

    def _update_dto(
        self,
        model: type[Any],
        tenant_id: UUID,
        entity_id: UUID,
        dto_cls: type[T],
        fields: dict[str, Any],
    ) -> T | None:
        row = self._get(model, tenant_id, entity_id)
        if row is None:
            return None
        for attr, value in fields.items():
            setattr(row, attr, value)
        self.session.flush()
        return _dto(dto_cls, row)

    def _set_active(
        self,
        model: type[Any],
        tenant_id: UUID,
        entity_id: UUID,
        active: bool,
        dto_cls: type[T],
    ) -> T | None:
        return self._update_dto(model, tenant_id, entity_id, dto_cls, {"active": active})

    def _has_active_child(
        self, model: type[Any], tenant_id: UUID, column: Any, parent_id: UUID
    ) -> bool:
        return (
            self.session.scalar(
                select(model.id)
                .where(
                    model.tenant_id == tenant_id,
                    column == parent_id,
                    model.active.is_(True),
                )
                .limit(1)
            )
            is not None
        )

    def get_institution(self, tenant_id: UUID) -> InstitutionInfo | None:
        row = self.session.scalar(
            select(Institution)
            .where(Institution.tenant_id == tenant_id)
            .execution_options(populate_existing=True)
        )
        return _dto(InstitutionInfo, row) if row is not None else None

    def update_institution(
        self, tenant_id: UUID, *, name: str
    ) -> InstitutionInfo | None:
        row = self.session.scalar(
            select(Institution)
            .where(Institution.tenant_id == tenant_id)
            .execution_options(populate_existing=True)
        )
        if row is None:
            return None
        row.name = name
        self.session.flush()
        return _dto(InstitutionInfo, row)

    def list_campuses(
        self,
        tenant_id: UUID,
        *,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[CampusSummary]:
        stmt = select(Campus).where(Campus.tenant_id == tenant_id)
        if active is not None:
            stmt = stmt.where(Campus.active.is_(active))
        return self._paginate(stmt, Campus, CampusSummary, cursor, limit)

    def get_campus(self, tenant_id: UUID, campus_id: UUID) -> CampusSummary | None:
        return self._get_dto(Campus, tenant_id, campus_id, CampusSummary)

    def create_campus(
        self, tenant_id: UUID, campus_id: UUID, name: str
    ) -> CampusSummary:
        return self._create_dto(
            Campus(tenant_id=tenant_id, id=campus_id, name=name, active=True),
            CampusSummary,
        )

    def update_campus(
        self, tenant_id: UUID, campus_id: UUID, *, name: str | None = None
    ) -> CampusSummary | None:
        return self._update_dto(
            Campus,
            tenant_id,
            campus_id,
            CampusSummary,
            {"name": name} if name is not None else {},
        )

    def set_campus_active(
        self, tenant_id: UUID, campus_id: UUID, active: bool
    ) -> CampusSummary | None:
        return self._set_active(Campus, tenant_id, campus_id, active, CampusSummary)

    def has_active_campus_children(self, tenant_id: UUID, campus_id: UUID) -> bool:
        return self._has_active_child(
            Department, tenant_id, Department.campus_id, campus_id
        )

    def list_departments(
        self,
        tenant_id: UUID,
        *,
        actor_id: UUID | None,
        campus_id: UUID | None,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[DepartmentSummary]:
        stmt = select(Department).where(Department.tenant_id == tenant_id)
        if actor_id is not None:
            stmt = stmt.join(
                DepartmentHead,
                and_(
                    DepartmentHead.tenant_id == Department.tenant_id,
                    DepartmentHead.department_id == Department.id,
                    DepartmentHead.user_id == actor_id,
                    DepartmentHead.active.is_(True),
                ),
            ).where(Department.active.is_(True))
        if campus_id is not None:
            stmt = stmt.where(Department.campus_id == campus_id)
        if active is not None:
            stmt = stmt.where(Department.active.is_(active))
        return self._paginate(stmt, Department, DepartmentSummary, cursor, limit)

    def get_department(
        self, tenant_id: UUID, department_id: UUID
    ) -> DepartmentSummary | None:
        return self._get_dto(Department, tenant_id, department_id, DepartmentSummary)

    def create_department(
        self,
        tenant_id: UUID,
        department_id: UUID,
        *,
        name: str,
        campus_id: UUID | None,
    ) -> DepartmentSummary:
        return self._create_dto(
            Department(
                tenant_id=tenant_id,
                id=department_id,
                name=name,
                campus_id=campus_id,
                active=True,
            ),
            DepartmentSummary,
        )

    def update_department(
        self,
        tenant_id: UUID,
        department_id: UUID,
        *,
        name: str | None = None,
        campus_id: UUID | None = None,
        update_campus: bool = False,
    ) -> DepartmentSummary | None:
        fields: dict[str, Any] = {}
        if name is not None:
            fields["name"] = name
        if update_campus:
            fields["campus_id"] = campus_id
        return self._update_dto(
            Department, tenant_id, department_id, DepartmentSummary, fields
        )

    def set_department_active(
        self, tenant_id: UUID, department_id: UUID, active: bool
    ) -> DepartmentSummary | None:
        return self._set_active(
            Department, tenant_id, department_id, active, DepartmentSummary
        )

    def has_active_department_children(
        self, tenant_id: UUID, department_id: UUID
    ) -> bool:
        return (
            self.session.scalar(
                select(literal(True)).where(
                    or_(
                        select(Programme.id)
                        .where(
                            Programme.tenant_id == tenant_id,
                            Programme.department_id == department_id,
                            Programme.active.is_(True),
                        )
                        .exists(),
                        select(Subject.id)
                        .where(
                            Subject.tenant_id == tenant_id,
                            Subject.department_id == department_id,
                            Subject.active.is_(True),
                        )
                        .exists(),
                    )
                )
            )
            is True
        )

    def list_programmes(
        self,
        tenant_id: UUID,
        *,
        actor_id: UUID | None,
        department_id: UUID | None,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[ProgrammeSummary]:
        stmt = select(Programme).where(Programme.tenant_id == tenant_id)
        if actor_id is not None:
            stmt = _dept_head_scope(stmt, Programme, actor_id)
        if department_id is not None:
            stmt = stmt.where(Programme.department_id == department_id)
        if active is not None:
            stmt = stmt.where(Programme.active.is_(active))
        return self._paginate(stmt, Programme, ProgrammeSummary, cursor, limit)

    def get_programme(
        self, tenant_id: UUID, programme_id: UUID
    ) -> ProgrammeSummary | None:
        return self._get_dto(Programme, tenant_id, programme_id, ProgrammeSummary)

    def create_programme(
        self,
        tenant_id: UUID,
        programme_id: UUID,
        *,
        name: str,
        department_id: UUID | None,
    ) -> ProgrammeSummary:
        return self._create_dto(
            Programme(
                tenant_id=tenant_id,
                id=programme_id,
                name=name,
                department_id=department_id,
                active=True,
            ),
            ProgrammeSummary,
        )

    def update_programme(
        self,
        tenant_id: UUID,
        programme_id: UUID,
        *,
        name: str | None = None,
        department_id: UUID | None = None,
        update_department: bool = False,
    ) -> ProgrammeSummary | None:
        fields: dict[str, Any] = {}
        if name is not None:
            fields["name"] = name
        if update_department:
            fields["department_id"] = department_id
        return self._update_dto(
            Programme, tenant_id, programme_id, ProgrammeSummary, fields
        )

    def set_programme_active(
        self, tenant_id: UUID, programme_id: UUID, active: bool
    ) -> ProgrammeSummary | None:
        return self._set_active(
            Programme, tenant_id, programme_id, active, ProgrammeSummary
        )

    def has_active_programme_children(
        self, tenant_id: UUID, programme_id: UUID
    ) -> bool:
        return self._has_active_child(
            Section, tenant_id, Section.programme_id, programme_id
        )

    def list_academic_years(
        self,
        tenant_id: UUID,
        *,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[AcademicYearSummary]:
        stmt = select(AcademicYear).where(AcademicYear.tenant_id == tenant_id)
        if active is not None:
            stmt = stmt.where(AcademicYear.active.is_(active))
        return self._paginate(stmt, AcademicYear, AcademicYearSummary, cursor, limit)

    def get_academic_year(
        self, tenant_id: UUID, academic_year_id: UUID
    ) -> AcademicYearSummary | None:
        return self._get_dto(
            AcademicYear, tenant_id, academic_year_id, AcademicYearSummary
        )

    def create_academic_year(
        self, tenant_id: UUID, academic_year_id: UUID, name: str
    ) -> AcademicYearSummary:
        return self._create_dto(
            AcademicYear(
                tenant_id=tenant_id, id=academic_year_id, name=name, active=True
            ),
            AcademicYearSummary,
        )

    def update_academic_year(
        self, tenant_id: UUID, academic_year_id: UUID, *, name: str | None = None
    ) -> AcademicYearSummary | None:
        return self._update_dto(
            AcademicYear,
            tenant_id,
            academic_year_id,
            AcademicYearSummary,
            {"name": name} if name is not None else {},
        )

    def set_academic_year_active(
        self, tenant_id: UUID, academic_year_id: UUID, active: bool
    ) -> AcademicYearSummary | None:
        return self._set_active(
            AcademicYear, tenant_id, academic_year_id, active, AcademicYearSummary
        )

    def has_active_academic_year_children(
        self, tenant_id: UUID, academic_year_id: UUID
    ) -> bool:
        return self._has_active_child(
            Semester, tenant_id, Semester.academic_year_id, academic_year_id
        )

    def list_semesters(
        self,
        tenant_id: UUID,
        *,
        academic_year_id: UUID | None,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[SemesterSummary]:
        stmt = select(Semester).where(Semester.tenant_id == tenant_id)
        if academic_year_id is not None:
            stmt = stmt.where(Semester.academic_year_id == academic_year_id)
        if active is not None:
            stmt = stmt.where(Semester.active.is_(active))
        return self._paginate(stmt, Semester, SemesterSummary, cursor, limit)

    def get_semester(
        self, tenant_id: UUID, semester_id: UUID
    ) -> SemesterSummary | None:
        return self._get_dto(Semester, tenant_id, semester_id, SemesterSummary)

    def create_semester(
        self,
        tenant_id: UUID,
        semester_id: UUID,
        *,
        name: str,
        academic_year_id: UUID,
    ) -> SemesterSummary:
        return self._create_dto(
            Semester(
                tenant_id=tenant_id,
                id=semester_id,
                name=name,
                academic_year_id=academic_year_id,
                active=True,
            ),
            SemesterSummary,
        )

    def update_semester(
        self,
        tenant_id: UUID,
        semester_id: UUID,
        *,
        name: str | None = None,
        academic_year_id: UUID | None = None,
    ) -> SemesterSummary | None:
        fields: dict[str, Any] = {}
        if name is not None:
            fields["name"] = name
        if academic_year_id is not None:
            fields["academic_year_id"] = academic_year_id
        return self._update_dto(
            Semester, tenant_id, semester_id, SemesterSummary, fields
        )

    def set_semester_active(
        self, tenant_id: UUID, semester_id: UUID, active: bool
    ) -> SemesterSummary | None:
        return self._set_active(
            Semester, tenant_id, semester_id, active, SemesterSummary
        )

    def has_active_semester_children(
        self, tenant_id: UUID, semester_id: UUID
    ) -> bool:
        return self._has_active_child(
            Section, tenant_id, Section.semester_id, semester_id
        )

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
    ) -> Page[SectionSummary]:
        stmt = select(Section).where(Section.tenant_id == tenant_id)
        if actor_id is not None:
            stmt = _active_section_chain(stmt).join(
                ClassCoordinator,
                and_(
                    ClassCoordinator.tenant_id == Section.tenant_id,
                    ClassCoordinator.section_id == Section.id,
                    ClassCoordinator.user_id == actor_id,
                    ClassCoordinator.active.is_(True),
                ),
            )
        if semester_id is not None:
            stmt = stmt.where(Section.semester_id == semester_id)
        if programme_id is not None:
            stmt = stmt.where(Section.programme_id == programme_id)
        if active is not None:
            stmt = stmt.where(Section.active.is_(active))
        return self._paginate(stmt, Section, SectionSummary, cursor, limit)

    def get_section(self, tenant_id: UUID, section_id: UUID) -> SectionSummary | None:
        return self._get_dto(Section, tenant_id, section_id, SectionSummary)

    def create_section(
        self,
        tenant_id: UUID,
        section_id: UUID,
        *,
        name: str,
        semester_id: UUID,
        programme_id: UUID | None,
    ) -> SectionSummary:
        return self._create_dto(
            Section(
                tenant_id=tenant_id,
                id=section_id,
                name=name,
                semester_id=semester_id,
                programme_id=programme_id,
                active=True,
            ),
            SectionSummary,
        )

    def update_section(
        self,
        tenant_id: UUID,
        section_id: UUID,
        *,
        name: str | None = None,
        semester_id: UUID | None = None,
        programme_id: UUID | None = None,
        update_programme: bool = False,
    ) -> SectionSummary | None:
        fields: dict[str, Any] = {}
        if name is not None:
            fields["name"] = name
        if semester_id is not None:
            fields["semester_id"] = semester_id
        if update_programme:
            fields["programme_id"] = programme_id
        return self._update_dto(
            Section, tenant_id, section_id, SectionSummary, fields
        )

    def set_section_active(
        self, tenant_id: UUID, section_id: UUID, active: bool
    ) -> SectionSummary | None:
        return self._set_active(
            Section, tenant_id, section_id, active, SectionSummary
        )

    def has_active_section_children(self, tenant_id: UUID, section_id: UUID) -> bool:
        return self._has_active_child(
            SubjectOffering, tenant_id, SubjectOffering.section_id, section_id
        )

    def list_subjects(
        self,
        tenant_id: UUID,
        *,
        actor_id: UUID | None,
        department_id: UUID | None,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[SubjectSummary]:
        stmt = select(Subject).where(Subject.tenant_id == tenant_id)
        if actor_id is not None:
            stmt = _dept_head_scope(stmt, Subject, actor_id)
        if department_id is not None:
            stmt = stmt.where(Subject.department_id == department_id)
        if active is not None:
            stmt = stmt.where(Subject.active.is_(active))
        return self._paginate(stmt, Subject, SubjectSummary, cursor, limit)

    def get_subject(self, tenant_id: UUID, subject_id: UUID) -> SubjectSummary | None:
        return self._get_dto(Subject, tenant_id, subject_id, SubjectSummary)

    def create_subject(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        *,
        name: str,
        department_id: UUID | None,
    ) -> SubjectSummary:
        return self._create_dto(
            Subject(
                tenant_id=tenant_id,
                id=subject_id,
                name=name,
                department_id=department_id,
                active=True,
            ),
            SubjectSummary,
        )

    def update_subject(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        *,
        name: str | None = None,
        department_id: UUID | None = None,
        update_department: bool = False,
    ) -> SubjectSummary | None:
        fields: dict[str, Any] = {}
        if name is not None:
            fields["name"] = name
        if update_department:
            fields["department_id"] = department_id
        return self._update_dto(
            Subject, tenant_id, subject_id, SubjectSummary, fields
        )

    def set_subject_active(
        self, tenant_id: UUID, subject_id: UUID, active: bool
    ) -> SubjectSummary | None:
        return self._set_active(
            Subject, tenant_id, subject_id, active, SubjectSummary
        )

    def has_active_subject_children(self, tenant_id: UUID, subject_id: UUID) -> bool:
        return self._has_active_child(
            SubjectOffering, tenant_id, SubjectOffering.subject_id, subject_id
        )

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
    ) -> Page[SubjectOfferingSummary]:
        stmt = select(SubjectOffering).where(SubjectOffering.tenant_id == tenant_id)
        if actor_id is not None:
            filters = []
            if "class_coordinator" in roles:
                filters.append(
                    SubjectOffering.section_id.in_(
                        select(ClassCoordinator.section_id).where(
                            ClassCoordinator.tenant_id == tenant_id,
                            ClassCoordinator.user_id == actor_id,
                            ClassCoordinator.active.is_(True),
                        )
                    )
                )
            if "subject_teacher" in roles:
                filters.append(
                    SubjectOffering.id.in_(
                        select(TeacherAssignment.offering_id).where(
                            TeacherAssignment.tenant_id == tenant_id,
                            TeacherAssignment.user_id == actor_id,
                            TeacherAssignment.active.is_(True),
                        )
                    )
                )
            if "student" in roles:
                filters.append(
                    SubjectOffering.id.in_(
                        select(StudentEnrolment.offering_id).where(
                            StudentEnrolment.tenant_id == tenant_id,
                            StudentEnrolment.user_id == actor_id,
                            StudentEnrolment.active.is_(True),
                        )
                    )
                )
            if not filters:
                return Page(items=(), next_cursor=None)
            stmt = _active_section_chain(
                stmt.join(
                    Section,
                    and_(
                        Section.tenant_id == SubjectOffering.tenant_id,
                        Section.id == SubjectOffering.section_id,
                    ),
                )
            ).join(
                Subject,
                and_(
                    Subject.tenant_id == SubjectOffering.tenant_id,
                    Subject.id == SubjectOffering.subject_id,
                ),
            ).where(
                SubjectOffering.active.is_(True),
                Subject.active.is_(True),
                or_(*filters),
            )
        if section_id is not None:
            stmt = stmt.where(SubjectOffering.section_id == section_id)
        if subject_id is not None:
            stmt = stmt.where(SubjectOffering.subject_id == subject_id)
        if active is not None:
            stmt = stmt.where(SubjectOffering.active.is_(active))
        return self._paginate(stmt, SubjectOffering, SubjectOfferingSummary, cursor, limit)

    def get_offering(
        self, tenant_id: UUID, offering_id: UUID
    ) -> SubjectOfferingSummary | None:
        return self._get_dto(
            SubjectOffering, tenant_id, offering_id, SubjectOfferingSummary
        )

    def create_offering(
        self,
        tenant_id: UUID,
        offering_id: UUID,
        *,
        name: str,
        section_id: UUID,
        subject_id: UUID,
    ) -> SubjectOfferingSummary:
        return self._create_dto(
            SubjectOffering(
                tenant_id=tenant_id,
                id=offering_id,
                name=name,
                section_id=section_id,
                subject_id=subject_id,
                active=True,
            ),
            SubjectOfferingSummary,
        )

    def update_offering(
        self,
        tenant_id: UUID,
        offering_id: UUID,
        *,
        name: str | None = None,
        section_id: UUID | None = None,
        subject_id: UUID | None = None,
    ) -> SubjectOfferingSummary | None:
        fields: dict[str, Any] = {}
        if name is not None:
            fields["name"] = name
        if section_id is not None:
            fields["section_id"] = section_id
        if subject_id is not None:
            fields["subject_id"] = subject_id
        return self._update_dto(
            SubjectOffering, tenant_id, offering_id, SubjectOfferingSummary, fields
        )

    def set_offering_active(
        self, tenant_id: UUID, offering_id: UUID, active: bool
    ) -> SubjectOfferingSummary | None:
        return self._set_active(
            SubjectOffering, tenant_id, offering_id, active, SubjectOfferingSummary
        )

    def has_duplicate_offering(
        self,
        tenant_id: UUID,
        *,
        section_id: UUID,
        subject_id: UUID,
        name: str,
        exclude_id: UUID | None = None,
    ) -> bool:
        self.session.scalar(
            select(Section.id)
            .where(Section.tenant_id == tenant_id, Section.id == section_id)
            .with_for_update()
        )
        stmt = select(SubjectOffering.id).where(
            SubjectOffering.tenant_id == tenant_id,
            SubjectOffering.section_id == section_id,
            SubjectOffering.subject_id == subject_id,
            func.lower(func.btrim(SubjectOffering.name)) == name.strip().lower(),
        )
        if exclude_id is not None:
            stmt = stmt.where(SubjectOffering.id != exclude_id)
        return self.session.scalar(stmt.limit(1)) is not None

    def _list_assignments(
        self,
        model: type[Any],
        tenant_id: UUID,
        target_col: Any,
        target_id: UUID,
        dto_cls: type[T],
        *,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[T]:
        stmt = select(model).where(
            model.tenant_id == tenant_id, target_col == target_id
        )
        if active is not None:
            stmt = stmt.where(model.active.is_(active))
        return self._paginate(stmt, model, dto_cls, cursor, limit, cursor_col=model.user_id)

    def _assign_member_target(
        self,
        model: type[Any],
        tenant_id: UUID,
        target_col: Any,
        target_field: str,
        target_id: UUID,
        user_id: UUID,
        dto_cls: type[T],
    ) -> T:
        existing = self.session.scalar(
            select(model)
            .where(
                model.tenant_id == tenant_id,
                model.user_id == user_id,
                target_col == target_id,
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.active:
                raise AccessError(
                    409, "duplicate_record", "Assignment is already active"
                )
            existing.active = True
            row = existing
        else:
            row = model(
                tenant_id=tenant_id,
                user_id=user_id,
                **{target_field: target_id, "active": True},
            )
            self.session.add(row)
        self.session.flush()
        return _dto(dto_cls, row)

    def _revoke_member_target(
        self,
        model: type[Any],
        tenant_id: UUID,
        target_col: Any,
        target_id: UUID,
        user_id: UUID,
        dto_cls: type[T],
    ) -> T | None:
        existing = self.session.scalar(
            select(model)
            .where(
                model.tenant_id == tenant_id,
                model.user_id == user_id,
                target_col == target_id,
            )
            .with_for_update()
        )
        if existing is None or not existing.active:
            return None
        existing.active = False
        self.session.flush()
        return _dto(dto_cls, existing)

    def list_department_heads(
        self, tenant_id: UUID, department_id: UUID, *, active: bool | None, cursor: UUID | None, limit: int
    ) -> Page[DepartmentHeadSummary]:
        return self._list_assignments(DepartmentHead, tenant_id, DepartmentHead.department_id, department_id, DepartmentHeadSummary, active=active, cursor=cursor, limit=limit)

    def assign_department_head(
        self, tenant_id: UUID, department_id: UUID, user_id: UUID
    ) -> DepartmentHeadSummary:
        return self._assign_member_target(DepartmentHead, tenant_id, DepartmentHead.department_id, "department_id", department_id, user_id, DepartmentHeadSummary)

    def revoke_department_head(
        self, tenant_id: UUID, department_id: UUID, user_id: UUID
    ) -> DepartmentHeadSummary | None:
        return self._revoke_member_target(DepartmentHead, tenant_id, DepartmentHead.department_id, department_id, user_id, DepartmentHeadSummary)

    def list_section_coordinators(
        self, tenant_id: UUID, section_id: UUID, *, active: bool | None, cursor: UUID | None, limit: int
    ) -> Page[ClassCoordinatorSummary]:
        return self._list_assignments(ClassCoordinator, tenant_id, ClassCoordinator.section_id, section_id, ClassCoordinatorSummary, active=active, cursor=cursor, limit=limit)

    def assign_section_coordinator(
        self, tenant_id: UUID, section_id: UUID, user_id: UUID
    ) -> ClassCoordinatorSummary:
        return self._assign_member_target(ClassCoordinator, tenant_id, ClassCoordinator.section_id, "section_id", section_id, user_id, ClassCoordinatorSummary)

    def revoke_section_coordinator(
        self, tenant_id: UUID, section_id: UUID, user_id: UUID
    ) -> ClassCoordinatorSummary | None:
        return self._revoke_member_target(ClassCoordinator, tenant_id, ClassCoordinator.section_id, section_id, user_id, ClassCoordinatorSummary)

    def list_offering_teachers(
        self, tenant_id: UUID, offering_id: UUID, *, active: bool | None, cursor: UUID | None, limit: int
    ) -> Page[OfferingAssignmentSummary]:
        return self._list_assignments(TeacherAssignment, tenant_id, TeacherAssignment.offering_id, offering_id, OfferingAssignmentSummary, active=active, cursor=cursor, limit=limit)

    def assign_offering_teacher(
        self, tenant_id: UUID, offering_id: UUID, user_id: UUID
    ) -> OfferingAssignmentSummary:
        return self._assign_member_target(TeacherAssignment, tenant_id, TeacherAssignment.offering_id, "offering_id", offering_id, user_id, OfferingAssignmentSummary)

    def revoke_offering_teacher(
        self, tenant_id: UUID, offering_id: UUID, user_id: UUID
    ) -> OfferingAssignmentSummary | None:
        return self._revoke_member_target(TeacherAssignment, tenant_id, TeacherAssignment.offering_id, offering_id, user_id, OfferingAssignmentSummary)

    def list_offering_enrolments(
        self, tenant_id: UUID, offering_id: UUID, *, active: bool | None, cursor: UUID | None, limit: int
    ) -> Page[OfferingAssignmentSummary]:
        return self._list_assignments(StudentEnrolment, tenant_id, StudentEnrolment.offering_id, offering_id, OfferingAssignmentSummary, active=active, cursor=cursor, limit=limit)

    def enrol_student(
        self, tenant_id: UUID, offering_id: UUID, user_id: UUID
    ) -> OfferingAssignmentSummary:
        return self._assign_member_target(StudentEnrolment, tenant_id, StudentEnrolment.offering_id, "offering_id", offering_id, user_id, OfferingAssignmentSummary)

    def withdraw_student(
        self, tenant_id: UUID, offering_id: UUID, user_id: UUID
    ) -> OfferingAssignmentSummary | None:
        return self._revoke_member_target(StudentEnrolment, tenant_id, StudentEnrolment.offering_id, offering_id, user_id, OfferingAssignmentSummary)

    @staticmethod
    def _guardian_dto(row: GuardianLink) -> GuardianLinkSummary:
        return GuardianLinkSummary(
            tenant_id=row.tenant_id,
            guardian_user_id=row.user_id,
            student_user_id=row.student_id,
            active=row.active,
            verified_at=row.verified_at,
            consent_at=row.consent_at,
        )

    def list_guardian_links(
        self,
        tenant_id: UUID,
        *,
        actor_id: UUID | None,
        active: bool | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[GuardianLinkSummary]:
        stmt = select(GuardianLink).where(GuardianLink.tenant_id == tenant_id)
        if actor_id is not None:
            stmt = stmt.where(
                or_(
                    GuardianLink.user_id == actor_id,
                    GuardianLink.student_id == actor_id,
                )
            )
        if active is not None:
            stmt = stmt.where(GuardianLink.active.is_(active))
        if cursor is not None:
            stmt = stmt.where(GuardianLink.student_id > cursor)
        rows = list(
            self.session.scalars(
                stmt.order_by(GuardianLink.student_id, GuardianLink.user_id).limit(
                    limit + 1
                )
            )
        )
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        next_cursor = str(page_rows[-1].student_id) if has_more and page_rows else None
        return Page[GuardianLinkSummary](
            items=tuple(self._guardian_dto(r) for r in page_rows),
            next_cursor=next_cursor,
        )

    def create_guardian_link(
        self, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
    ) -> GuardianLinkSummary:
        existing = self.session.scalar(
            select(GuardianLink)
            .where(
                GuardianLink.tenant_id == tenant_id,
                GuardianLink.user_id == guardian_user_id,
                GuardianLink.student_id == student_user_id,
            )
            .with_for_update()
        )
        if existing is not None:
            raise AccessError(
                409, "duplicate_record", "Guardian link already exists"
            )
        row = GuardianLink(
            tenant_id=tenant_id,
            user_id=guardian_user_id,
            student_id=student_user_id,
            active=False,
            verified_at=None,
            consent_at=None,
        )
        self.session.add(row)
        self.session.flush()
        return self._guardian_dto(row)

    def _get_guardian_link(
        self, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
    ) -> GuardianLink | None:
        return self.session.scalar(
            select(GuardianLink)
            .where(
                GuardianLink.tenant_id == tenant_id,
                GuardianLink.user_id == guardian_user_id,
                GuardianLink.student_id == student_user_id,
            )
            .with_for_update()
        )

    def verify_guardian_link(
        self, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
    ) -> GuardianLinkSummary | None:
        row = self._get_guardian_link(tenant_id, guardian_user_id, student_user_id)
        if row is None:
            return None
        row.verified_at = row.verified_at or datetime.now(UTC)
        self.session.flush()
        return self._guardian_dto(row)

    def consent_guardian_link(
        self, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
    ) -> GuardianLinkSummary | None:
        row = self._get_guardian_link(tenant_id, guardian_user_id, student_user_id)
        if row is None:
            return None
        row.consent_at = datetime.now(UTC)
        self.session.flush()
        return self._guardian_dto(row)

    def activate_guardian_link(
        self, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
    ) -> GuardianLinkSummary | None:
        row = self._get_guardian_link(tenant_id, guardian_user_id, student_user_id)
        if row is None:
            return None
        if row.verified_at is None or row.consent_at is None:
            raise AccessError(
                409,
                "verification_and_consent_required",
                "Guardian link requires both verification and student consent before activation",
            )
        row.active = True
        self.session.flush()
        return self._guardian_dto(row)

    def revoke_guardian_link(
        self, tenant_id: UUID, guardian_user_id: UUID, student_user_id: UUID
    ) -> GuardianLinkSummary | None:
        row = self._get_guardian_link(tenant_id, guardian_user_id, student_user_id)
        if row is None:
            return None
        row.active = False
        row.consent_at = None
        self.session.flush()
        return self._guardian_dto(row)
