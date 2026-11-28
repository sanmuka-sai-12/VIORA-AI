import { useCallback, useState } from "react";
import type { AcademicYearSummary, SemesterSummary } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { FormField, PageHeader, UuidChip } from "../../components/common/UiPrimitives";
import { deriveRoleFlags } from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { validateRequiredName, validateRequiredUuid } from "../../tenant/validation";
import { AcademicCrudSection, type ActiveFilterValue } from "../common/AcademicCrudSection";
import { useActionFeedback } from "../common/useActionFeedback";
import { useCursorList } from "../common/useCursorList";
import { resolveLabelById, useTenantOptions } from "../common/useTenantOptions";

export function AcademicYearsSemestersPage({ embedded = false }: { embedded?: boolean }) {
  const { apiClient } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);
  const feedback = useActionFeedback();
  const options = useTenantOptions({ academicYears: true });

  const [yearActiveFilter, setYearActiveFilter] = useState<ActiveFilterValue>("all");
  const [yearName, setYearName] = useState("");
  const [editYearName, setEditYearName] = useState("");

  const [semesterYearFilter, setSemesterYearFilter] = useState("");
  const [semesterActiveFilter, setSemesterActiveFilter] = useState<ActiveFilterValue>("all");
  const [semesterName, setSemesterName] = useState("");
  const [semesterYearId, setSemesterYearId] = useState("");
  const [editSemesterName, setEditSemesterName] = useState("");
  const [editSemesterYearId, setEditSemesterYearId] = useState("");

  const yearsFetcher = useCallback(
    ({ cursor, limit }: { cursor?: string; limit: number }) =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/academic-years", {
        params: {
          path: { tenant_id: tenantId! },
          query: {
            active: yearActiveFilter === "all" ? undefined : yearActiveFilter === "active",
            cursor,
            limit,
          },
        },
      }),
    [apiClient, tenantId, yearActiveFilter],
  );
  const semestersFetcher = useCallback(
    ({ cursor, limit }: { cursor?: string; limit: number }) =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/semesters", {
        params: {
          path: { tenant_id: tenantId! },
          query: {
            academic_year_id: semesterYearFilter || undefined,
            active: semesterActiveFilter === "all" ? undefined : semesterActiveFilter === "active",
            cursor,
            limit,
          },
        },
      }),
    [apiClient, semesterActiveFilter, semesterYearFilter, tenantId],
  );

  const yearsList = useCursorList<AcademicYearSummary>(tenantId, yearsFetcher);
  const semestersList = useCursorList<SemesterSummary>(tenantId, semestersFetcher);
  const mergedYears = yearsList.items.length > 0 ? yearsList.items : options.academicYears;

  const handleCreateYear = async () => {
    if (!tenantId) return false;
    const err = validateRequiredName(yearName, "Academic year name", 120);
    if (err) {
      feedback.setValidationError(err);
      return false;
    }
    return feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/academic-years", {
          params: { path: { tenant_id: tenantId } },
          body: { name: yearName.trim() },
        }),
      fallbackError: "Failed to create academic year.",
      onSuccess: (data) => {
        setYearName("");
        feedback.setActionSuccess(`Academic year "${data.name}" created.`);
        yearsList.resetToFirstPage();
        yearsList.reload();
      },
    });
  };

  const handleSaveYear = async (target: AcademicYearSummary) => {
    if (!tenantId) return false;
    const err = validateRequiredName(editYearName, "Academic year name", 120);
    if (err) {
      feedback.setValidationError(err);
      return false;
    }
    return feedback.runApiAction({
      call: () =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/academic-years/{academic_year_id}", {
          params: { path: { tenant_id: tenantId, academic_year_id: target.id } },
          body: { name: editYearName.trim() },
        }),
      fallbackError: "Failed to update academic year.",
      onSuccess: (data) => {
        feedback.setActionSuccess(`Academic year renamed to "${data.name}".`);
        yearsList.reload();
      },
    });
  };

  const handleToggleYear = async (target: AcademicYearSummary) => {
    if (!tenantId) return;
    await feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/academic-years/{academic_year_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, academic_year_id: target.id } },
          body: { active: !target.active },
        }),
      fallbackError: "Failed to update year status.",
      onSuccess: (data) => {
        feedback.setActionSuccess(
          `Academic year "${data.name}" ${data.active ? "activated" : "archived"}.`,
        );
        yearsList.reload();
      },
    });
  };

  const handleCreateSemester = async () => {
    if (!tenantId) return false;
    const nameErr = validateRequiredName(semesterName, "Semester name", 120);
    const yearErr = validateRequiredUuid(semesterYearId, "Academic year");
    if (nameErr || yearErr) {
      feedback.setValidationError(nameErr ?? yearErr ?? "Invalid semester inputs.");
      return false;
    }
    return feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/semesters", {
          params: { path: { tenant_id: tenantId } },
          body: { name: semesterName.trim(), academic_year_id: semesterYearId },
        }),
      fallbackError: "Failed to create semester.",
      onSuccess: (data) => {
        setSemesterName("");
        feedback.setActionSuccess(`Semester "${data.name}" created.`);
        semestersList.resetToFirstPage();
        semestersList.reload();
      },
    });
  };

  const handleSaveSemester = async (target: SemesterSummary) => {
    if (!tenantId) return false;
    const err = validateRequiredName(editSemesterName, "Semester name", 120);
    if (err) {
      feedback.setValidationError(err);
      return false;
    }
    return feedback.runApiAction({
      call: () =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/semesters/{semester_id}", {
          params: { path: { tenant_id: tenantId, semester_id: target.id } },
          body: { name: editSemesterName.trim(), academic_year_id: editSemesterYearId || null },
        }),
      fallbackError: "Failed to update semester.",
      onSuccess: (data) => {
        feedback.setActionSuccess(`Semester updated to "${data.name}".`);
        semestersList.reload();
      },
    });
  };

  const handleToggleSemester = async (target: SemesterSummary) => {
    if (!tenantId) return;
    await feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/semesters/{semester_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, semester_id: target.id } },
          body: { active: !target.active },
        }),
      fallbackError: "Failed to update semester status.",
      onSuccess: (data) => {
        feedback.setActionSuccess(
          `Semester "${data.name}" ${data.active ? "activated" : "archived"}.`,
        );
        semestersList.reload();
      },
    });
  };

  return (
    <div className="viora-stack">
      {!embedded && (
        <PageHeader
          title="Academic Years & Semesters"
          subtitle="Define institutional academic calendars and semester terms."
        />
      )}
      {feedback.alerts}

      <AcademicCrudSection
        entityLabel="Academic year"
        list={yearsList}
        activeFilter={yearActiveFilter}
        onActiveFilterChange={setYearActiveFilter}
        statusFilterAriaLabel="Filter academic years by status"
        canCreate={flags.isAdmin}
        inlineCreateInDirectory
        createSubmitLabel="Create academic year"
        onCreate={handleCreateYear}
        renderCreateFields={(busy) => (
          <div style={{ flex: 1, minWidth: "220px", maxWidth: "360px" }}>
            <FormField label="New academic year" htmlFor="year-create-name">
              <input
                id="year-create-name"
                type="text"
                className="viora-input"
                value={yearName}
                onChange={(e) => setYearName(e.target.value)}
                placeholder="2026-2027"
                disabled={busy}
                required
              />
            </FormField>
          </div>
        )}
        directoryTitle="Academic Years"
        loadingMessage="Loading academic years..."
        errorTitle="Could not load academic years"
        emptyTitle="No academic years configured"
        emptyDescription="Create an academic year above before adding semesters."
        columns={[
          { key: "name", header: "Year", render: (y) => <strong>{y.name}</strong> },
        ]}
        extraRowActions={(y) => (
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            onClick={() => {
              setSemesterYearFilter(y.id);
              semestersList.resetToFirstPage();
            }}
          >
            Filter semesters
          </button>
        )}
        canEditRow={() => flags.isAdmin}
        canToggleRow={() => flags.isAdmin}
        editButtonLabel="Rename"
        editModalTitle="Rename Academic Year"
        onOpenEdit={(y) => setEditYearName(y.name)}
        renderEditFields={(_, busy) => (
          <FormField label="Academic year name" htmlFor="year-edit-name">
            <input
              id="year-edit-name"
              type="text"
              className="viora-input"
              value={editYearName}
              onChange={(e) => setEditYearName(e.target.value)}
              disabled={busy}
              required
            />
          </FormField>
        )}
        onSaveEdit={handleSaveYear}
        archiveMessage={(y) =>
          `Change status for academic year "${y.name}"? Archiving blocks new semesters under this year.`
        }
        onToggleLifecycle={handleToggleYear}
      />

      <AcademicCrudSection
        entityLabel="Semester"
        list={semestersList}
        activeFilter={semesterActiveFilter}
        onActiveFilterChange={setSemesterActiveFilter}
        statusFilterAriaLabel="Filter semesters by status"
        canCreate={flags.isAdmin}
        inlineCreateInDirectory
        createSubmitLabel="Create semester"
        onCreate={handleCreateSemester}
        renderCreateFields={(busy) => (
          <>
            <div style={{ flex: 1, minWidth: "200px" }}>
              <FormField label="Semester name" htmlFor="sem-create-name">
                <input
                  id="sem-create-name"
                  type="text"
                  className="viora-input"
                  value={semesterName}
                  onChange={(e) => setSemesterName(e.target.value)}
                  placeholder="Fall 2026"
                  disabled={busy}
                  required
                />
              </FormField>
            </div>
            <div style={{ flex: 1, minWidth: "220px" }}>
              <FormField label="Academic year" htmlFor="sem-create-year">
                <select
                  id="sem-create-year"
                  className="viora-select"
                  value={semesterYearId}
                  onChange={(e) => setSemesterYearId(e.target.value)}
                  disabled={busy}
                  required
                >
                  <option value="">Select active year...</option>
                  {mergedYears
                    .filter((y) => y.active)
                    .map((y) => (
                      <option key={y.id} value={y.id}>
                        {y.name}
                      </option>
                    ))}
                </select>
              </FormField>
            </div>
          </>
        )}
        directoryTitle="Semesters"
        extraFilters={
          <select
            className="viora-select viora-select-inline"
            aria-label="Filter semesters by academic year"
            value={semesterYearFilter}
            onChange={(e) => {
              setSemesterYearFilter(e.target.value);
              semestersList.resetToFirstPage();
            }}
          >
            <option value="">All academic years</option>
            {mergedYears.map((y) => (
              <option key={y.id} value={y.id}>
                {y.name}
              </option>
            ))}
          </select>
        }
        loadingMessage="Loading semesters..."
        errorTitle="Could not load semesters"
        emptyTitle="No semesters configured"
        emptyDescription="Create a semester linked to an active academic year or widen the filter."
        columns={[
          { key: "name", header: "Semester", render: (s) => <strong>{s.name}</strong> },
          {
            key: "year",
            header: "Academic Year",
            render: (s) =>
              resolveLabelById(mergedYears, s.academic_year_id) ?? (
                <UuidChip value={s.academic_year_id} />
              ),
          },
        ]}
        canEditRow={() => flags.isAdmin}
        canToggleRow={() => flags.isAdmin}
        editModalTitle="Edit Semester"
        onOpenEdit={(s) => {
          setEditSemesterName(s.name);
          setEditSemesterYearId(s.academic_year_id);
        }}
        renderEditFields={(_, busy) => (
          <>
            <FormField label="Semester name" htmlFor="sem-edit-name">
              <input
                id="sem-edit-name"
                type="text"
                className="viora-input"
                value={editSemesterName}
                onChange={(e) => setEditSemesterName(e.target.value)}
                disabled={busy}
                required
              />
            </FormField>
            <FormField label="Academic year" htmlFor="sem-edit-year">
              <select
                id="sem-edit-year"
                className="viora-select"
                value={editSemesterYearId}
                onChange={(e) => setEditSemesterYearId(e.target.value)}
                disabled={busy}
              >
                {mergedYears.map((y) => (
                  <option key={y.id} value={y.id}>
                    {y.name}
                    {y.active ? "" : " (Archived)"}
                  </option>
                ))}
              </select>
            </FormField>
          </>
        )}
        onSaveEdit={handleSaveSemester}
        archiveMessage={(s) =>
          `Change status for semester "${s.name}"? Archiving blocks new sections under this semester.`
        }
        onToggleLifecycle={handleToggleSemester}
      />
    </div>
  );
}
