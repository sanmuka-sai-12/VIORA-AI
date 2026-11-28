import { useState } from "react";
import { PageHeader } from "../../components/common/UiPrimitives";
import { DepartmentsPage } from "../academics/DepartmentsPage";
import { ProgrammesPage } from "../academics/ProgrammesPage";
import { SubjectsPage } from "../academics/SubjectsPage";

type DeptTab = "departments" | "programmes" | "subjects";

export function TeacherDepartmentPage() {
  const [tab, setTab] = useState<DeptTab>("departments");

  return (
    <div className="viora-stack">
      <PageHeader
        title="My Department"
        subtitle="Departments, programmes, and subjects within your department head scope."
      />

      <div className="viora-tabs" role="tablist" aria-label="Department sections">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "departments"}
          className={`viora-tab ${tab === "departments" ? "is-active" : ""}`}
          onClick={() => setTab("departments")}
        >
          Departments
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "programmes"}
          className={`viora-tab ${tab === "programmes" ? "is-active" : ""}`}
          onClick={() => setTab("programmes")}
        >
          Programmes
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "subjects"}
          className={`viora-tab ${tab === "subjects" ? "is-active" : ""}`}
          onClick={() => setTab("subjects")}
        >
          Subjects
        </button>
      </div>

      {tab === "departments" && <DepartmentsPage embedded />}
      {tab === "programmes" && <ProgrammesPage embedded />}
      {tab === "subjects" && <SubjectsPage embedded />}
    </div>
  );
}
