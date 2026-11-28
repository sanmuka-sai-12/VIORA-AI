from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def tenant_reference(
    column: str, parent: str, parent_key: str = "id"
) -> ForeignKeyConstraint:
    """Require each end of an academic relationship to belong to the same tenant."""
    return ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{parent}.tenant_id", f"{parent}.{parent_key}"],
        ondelete="RESTRICT",
    )


class Institution(Base):
    __tablename__ = "institutions"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_institutions_name"),
    )

    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str]
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class AcademicRecord:
    """Tenant-local definitions may reuse display names across independent programmes."""

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("institutions.tenant_id", ondelete="RESTRICT"),
        primary_key=True,
        sort_order=-2,
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, sort_order=-1)
    name: Mapped[str]
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class Campus(AcademicRecord, Base):
    __tablename__ = "campuses"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_campuses_name"),
        Index(
            "uq_campuses_tenant_name",
            "tenant_id",
            func.lower(func.btrim(text("name"))),
            unique=True,
        ),
    )


class Department(AcademicRecord, Base):
    __tablename__ = "departments"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_departments_name"),
        tenant_reference("campus_id", "campuses"),
        Index("ix_departments_campus_id", "tenant_id", "campus_id"),
        Index(
            "uq_departments_scope_name",
            "tenant_id",
            func.coalesce(
                text("campus_id"),
                text("'00000000-0000-0000-0000-000000000000'::uuid"),
            ),
            func.lower(func.btrim(text("name"))),
            unique=True,
        ),
    )

    campus_id: Mapped[UUID | None]


class Programme(AcademicRecord, Base):
    __tablename__ = "programmes"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_programmes_name"),
        tenant_reference("department_id", "departments"),
        Index("ix_programmes_department_id", "tenant_id", "department_id"),
        Index(
            "uq_programmes_scope_name",
            "tenant_id",
            func.coalesce(
                text("department_id"),
                text("'00000000-0000-0000-0000-000000000000'::uuid"),
            ),
            func.lower(func.btrim(text("name"))),
            unique=True,
        ),
    )

    department_id: Mapped[UUID | None]


class AcademicYear(AcademicRecord, Base):
    __tablename__ = "academic_years"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_academic_years_name"),
        Index(
            "uq_academic_years_tenant_name",
            "tenant_id",
            func.lower(func.btrim(text("name"))),
            unique=True,
        ),
    )


class Semester(AcademicRecord, Base):
    __tablename__ = "semesters"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_semesters_name"),
        tenant_reference("academic_year_id", "academic_years"),
        Index("ix_semesters_academic_year_id", "tenant_id", "academic_year_id"),
        Index(
            "uq_semesters_year_name",
            "tenant_id",
            "academic_year_id",
            func.lower(func.btrim(text("name"))),
            unique=True,
        ),
    )

    academic_year_id: Mapped[UUID]


class Section(AcademicRecord, Base):
    __tablename__ = "sections"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_sections_name"),
        tenant_reference("programme_id", "programmes"),
        Index("ix_sections_programme_id", "tenant_id", "programme_id"),
        tenant_reference("semester_id", "semesters"),
        Index("ix_sections_semester_id", "tenant_id", "semester_id"),
        Index(
            "uq_sections_scope_name",
            "tenant_id",
            func.coalesce(
                text("programme_id"),
                text("'00000000-0000-0000-0000-000000000000'::uuid"),
            ),
            "semester_id",
            func.lower(func.btrim(text("name"))),
            unique=True,
        ),
    )

    programme_id: Mapped[UUID | None]
    semester_id: Mapped[UUID]


class Subject(AcademicRecord, Base):
    __tablename__ = "subjects"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_subjects_name"),
        tenant_reference("department_id", "departments"),
        Index("ix_subjects_department_id", "tenant_id", "department_id"),
        Index(
            "uq_subjects_scope_name",
            "tenant_id",
            func.coalesce(
                text("department_id"),
                text("'00000000-0000-0000-0000-000000000000'::uuid"),
            ),
            func.lower(func.btrim(text("name"))),
            unique=True,
        ),
    )

    department_id: Mapped[UUID | None]


class SubjectOffering(AcademicRecord, Base):
    __tablename__ = "subject_offerings"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_subject_offerings_name"),
        tenant_reference("section_id", "sections"),
        Index("ix_subject_offerings_section_id", "tenant_id", "section_id"),
        tenant_reference("subject_id", "subjects"),
        Index("ix_subject_offerings_subject_id", "tenant_id", "subject_id"),
    )

    section_id: Mapped[UUID]
    subject_id: Mapped[UUID]


class MemberAssignment:
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True, sort_order=-2)
    user_id: Mapped[UUID] = mapped_column(primary_key=True, sort_order=-1)


class TeacherAssignment(MemberAssignment, Base):
    __tablename__ = "teacher_assignments"
    __table_args__ = (
        tenant_reference("user_id", "memberships", "user_id"),
        tenant_reference("offering_id", "subject_offerings"),
        Index("ix_teacher_assignments_offering_id", "tenant_id", "offering_id"),
    )

    offering_id: Mapped[UUID] = mapped_column(primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class StudentEnrolment(MemberAssignment, Base):
    __tablename__ = "student_enrolments"
    __table_args__ = (
        tenant_reference("user_id", "memberships", "user_id"),
        tenant_reference("offering_id", "subject_offerings"),
        Index("ix_student_enrolments_offering_id", "tenant_id", "offering_id"),
    )

    offering_id: Mapped[UUID] = mapped_column(primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class DepartmentHead(MemberAssignment, Base):
    __tablename__ = "department_heads"
    __table_args__ = (
        tenant_reference("user_id", "memberships", "user_id"),
        tenant_reference("department_id", "departments"),
        Index("ix_department_heads_department_id", "tenant_id", "department_id"),
    )

    department_id: Mapped[UUID] = mapped_column(primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class ClassCoordinator(MemberAssignment, Base):
    __tablename__ = "class_coordinators"
    __table_args__ = (
        tenant_reference("user_id", "memberships", "user_id"),
        tenant_reference("section_id", "sections"),
        Index("ix_class_coordinators_section_id", "tenant_id", "section_id"),
    )

    section_id: Mapped[UUID] = mapped_column(primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class GuardianLink(MemberAssignment, Base):
    __tablename__ = "guardian_links"
    __table_args__ = (
        tenant_reference("user_id", "memberships", "user_id"),
        tenant_reference("student_id", "memberships", "user_id"),
        Index("ix_guardian_links_student_id", "tenant_id", "student_id"),
        CheckConstraint("user_id <> student_id", name="ck_guardian_distinct"),
        CheckConstraint(
            "NOT active OR (consent_at IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_guardian_consent",
        ),
    )

    student_id: Mapped[UUID] = mapped_column(primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default=false())
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudentProfile(MemberAssignment, Base):
    __tablename__ = "student_profiles"
    __table_args__ = (
        tenant_reference("user_id", "memberships", "user_id"),
        tenant_reference("department_id", "departments"),
        tenant_reference("section_id", "sections"),
        tenant_reference("academic_year_id", "academic_years"),
        tenant_reference("semester_id", "semesters"),
        Index("ix_student_profiles_department_id", "tenant_id", "department_id"),
        Index("ix_student_profiles_section_id", "tenant_id", "section_id"),
        Index(
            "uq_student_profiles_roll_number",
            "tenant_id",
            func.lower(func.btrim(text("roll_number"))),
            unique=True,
            postgresql_where=text("roll_number IS NOT NULL"),
        ),
        CheckConstraint(
            "roll_number IS NULL OR btrim(roll_number) <> ''",
            name="ck_student_profiles_roll_number",
        ),
    )

    roll_number: Mapped[str | None]
    department_id: Mapped[UUID | None]
    section_id: Mapped[UUID | None]
    academic_year_id: Mapped[UUID | None]
    semester_id: Mapped[UUID | None]
    phone: Mapped[str | None]
    contact_email: Mapped[str | None]
    guardian_contact: Mapped[str | None]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FacultyProfile(MemberAssignment, Base):
    __tablename__ = "faculty_profiles"
    __table_args__ = (
        tenant_reference("user_id", "memberships", "user_id"),
        tenant_reference("department_id", "departments"),
        Index("ix_faculty_profiles_department_id", "tenant_id", "department_id"),
        Index(
            "uq_faculty_profiles_employee_code",
            "tenant_id",
            func.lower(func.btrim(text("employee_code"))),
            unique=True,
            postgresql_where=text("employee_code IS NOT NULL"),
        ),
        CheckConstraint(
            "employee_code IS NULL OR btrim(employee_code) <> ''",
            name="ck_faculty_profiles_employee_code",
        ),
    )

    employee_code: Mapped[str | None]
    department_id: Mapped[UUID | None]
    designation: Mapped[str | None]
    phone: Mapped[str | None]
    contact_email: Mapped[str | None]
    office_location: Mapped[str | None]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
