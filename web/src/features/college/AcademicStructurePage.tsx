import { useEffect, useState } from "react";
import { PageHeader } from "../../components/common/UiPrimitives";
import { AcademicYearsSemestersPage } from "../academics/AcademicYearsSemestersPage";
import { CampusesPage } from "../academics/CampusesPage";
import { DepartmentsPage } from "../academics/DepartmentsPage";
import { OfferingsPage } from "../academics/OfferingsPage";
import { ProgrammesPage } from "../academics/ProgrammesPage";
import { SectionsPage } from "../academics/SectionsPage";
import { SubjectsPage } from "../academics/SubjectsPage";

export type AcademicTabId =
  | "campuses"
  | "departments"
  | "programmes"
  | "calendar"
  | "sections"
  | "subjects"
  | "offerings";

const TABS: ReadonlyArray<{ id: AcademicTabId; label: string }> = [
  { id: "campuses", label: "Campuses" },
  { id: "departments", label: "Departments" },
  { id: "programmes", label: "Programmes" },
  { id: "calendar", label: "Years & Semesters" },
  { id: "sections", label: "Class Sections" },
  { id: "subjects", label: "Subjects" },
  { id: "offerings", label: "Offerings & Enrolments" },
];

export function academicTabFromPathname(pathname: string): AcademicTabId {
  if (pathname.startsWith("/academics/campuses")) return "campuses";
  if (pathname.startsWith("/academics/departments")) return "departments";
  if (pathname.startsWith("/academics/programmes")) return "programmes";
  if (pathname.startsWith("/academics/calendar")) return "calendar";
  if (pathname.startsWith("/academics/sections")) return "sections";
  if (pathname.startsWith("/academics/subjects")) return "subjects";
  if (pathname.startsWith("/academics/offerings")) return "offerings";
  return "offerings";
}

export function AcademicStructurePage({
  initialTab = "offerings",
}: {
  initialTab?: AcademicTabId;
}) {
  const [tab, setTab] = useState<AcademicTabId>(initialTab);

  useEffect(() => {
    setTab(initialTab);
  }, [initialTab]);

  return (
    <div className="viora-stack">
      <PageHeader
        title="Academic Structure"
        subtitle="Manage campuses, departments, programmes, academic calendar, class sections, subjects, and subject offerings."
      />

      <div className="viora-tabs" role="tablist" aria-label="Academic structure sections">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            className={`viora-tab ${tab === item.id ? "is-active" : ""}`}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "campuses" && <CampusesPage embedded />}
      {tab === "departments" && <DepartmentsPage embedded />}
      {tab === "programmes" && <ProgrammesPage embedded />}
      {tab === "calendar" && <AcademicYearsSemestersPage embedded />}
      {tab === "sections" && <SectionsPage embedded />}
      {tab === "subjects" && <SubjectsPage embedded />}
      {tab === "offerings" && <OfferingsPage embedded />}
    </div>
  );
}
