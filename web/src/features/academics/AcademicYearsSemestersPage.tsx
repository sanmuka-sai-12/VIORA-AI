import { useCallback, useState, type FormEvent } from "react";
import {
  normalizeApiError,
  type AcademicYearSummary,
  type NormalizedApiError,
  type SemesterSummary,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import {
  ActiveBadge,
  AlertBanner,
  ConfirmDialog,
  DataTable,
  FormField,
  ModalDialog,
  PageHeader,
  PaginationBar,
  SectionCard,
  UuidChip,
} from "../../components/common/UiPrimitives";
import { ErrorState, LoadingSpinner } from "../../components/common/FeedbackStates";
import { deriveRoleFlags } from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { validateRequiredName, validateRequiredUuid } from "../../tenant/validation";
import { useCursorList } from "../common/useCursorList";
import { resolveLabelById, useTenantOptions } from "../common/useTenantOptions";

export function AcademicYearsSemestersPage() {
  const { apiClient, controller: authController } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);

  const options = useTenantOptions({ academicYears: true });

  const [actionError, setActionError] = useState<NormalizedApiError | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  // Academic year state
  const [yearActiveFilter, setYearActiveFilter] = useState<"all" | "active" | "archived">("all");
  const [yearName, setYearName] = useState("");
  const [creatingYear, setCreatingYear] = useState(false);
  const [editingYear, setEditingYear] = useState<AcademicYearSummary | null>(null);
  const [editYearName, setEditYearName] = useState("");
  const [lifecycleYear, setLifecycleYear] = useState<AcademicYearSummary | null>(null);

  // Semester state
  const [semesterYearFilter, setSemesterYearFilter] = useState("");
  const [semesterActiveFilter, setSemesterActiveFilter] = useState<"all" | "active" | "archived">("all");
  const [semesterName, setSemesterName] = useState("");
  const [semesterYearId, setSemesterYearId] = useState("");
  const [creatingSemester, setCreatingSemester] = useState(false);
  const [editingSemester, setEditingSemester] = useState<SemesterSummary | null>(null);
  const [editSemesterName, setEditSemesterName] = useState("");
  const [editSemesterYearId, setEditSemesterYearId] = useState("");
  const [lifecycleSemester, setLifecycleSemester] = useState<SemesterSummary | null>(null);

  const [busy, setBusy] = useState(false);

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
            active:
              semesterActiveFilter === "all" ? undefined : semesterActiveFilter === "active",
            cursor,
            limit,
          },
        },
      }),
    [apiClient, semesterActiveFilter, semesterYearFilter, tenantId],
  );

  const yearsList = useCursorList<AcademicYearSummary>(tenantId, yearsFetcher);
  const semestersList = useCursorList<SemesterSummary>(tenantId, semestersFetcher);

  const mergedYears =
    yearsList.items.length > 0 ? yearsList.items : options.academicYears;

  const handleCreateYear = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId) return;
    setActionError(null);
    setActionSuccess(null);

    const problem = validateRequiredName(yearName, "Academic year name", 120);
    if (problem) {
      setActionError({ status: 422, code: "validation_error", message: problem });
      return;
    }

    setCreatingYear(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/academic-years", {
          params: { path: { tenant_id: tenantId } },
          body: { name: yearName.trim() },
        }),
      );
      if (res.data) {
        setYearName("");
        setActionSuccess(`Academic year "${res.data.name}" created.`);
        yearsList.resetToFirstPage();
        yearsList.reload();
      } else {
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to create academic year."),
        );
      }
    } catch {
      setActionError({
        status: 0,
        code: "network_error",
        message: "Unable to communicate with backend service.",
      });
    } finally {
      setCreatingYear(false);
    }
  };

  const handleSaveYear = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId || !editingYear) return;
    setActionError(null);
    setActionSuccess(null);
    const problem = validateRequiredName(editYearName, "Academic year name", 120);
    if (problem) {
      setActionError({ status: 422, code: "validation_error", message: problem });
      return;
    }

    setBusy(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.PATCH(
          "/api/v1/tenants/{tenant_id}/academic-years/{academic_year_id}",
          {
            params: { path: { tenant_id: tenantId, academic_year_id: editingYear.id } },
            body: { name: editYearName.trim() },
          },
        ),
      );
      if (res.data) {
        setEditingYear(null);
        setActionSuccess(`Academic year updated to "${res.data.name}".`);
        yearsList.reload();
      } else {
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to update academic year."),
        );
      }
    } catch {
      setActionError({
        status: 0,
        code: "network_error",
        message: "Unable to communicate with backend service.",
      });
    } finally {
      setBusy(false);
    }
  };

  const handleConfirmYearLifecycle = async () => {
    if (!tenantId || !lifecycleYear) return;
    setBusy(true);
    setActionError(null);
    setActionSuccess(null);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/academic-years/{academic_year_id}/lifecycle",
          {
            params: { path: { tenant_id: tenantId, academic_year_id: lifecycleYear.id } },
            body: { active: !lifecycleYear.active },
          },
        ),
      );
      if (res.data) {
        setActionSuccess(
          `Academic year "${res.data.name}" ${res.data.active ? "activated" : "archived"}.`,
        );
        setLifecycleYear(null);
        yearsList.reload();
      } else {
        setLifecycleYear(null);
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to update year status."),
        );
      }
    } catch {
      setLifecycleYear(null);
      setActionError({
        status: 0,
        code: "network_error",
        message: "Unable to communicate with backend service.",
      });
    } finally {
      setBusy(false);
    }
  };

  const handleCreateSemester = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId) return;
    setActionError(null);
    setActionSuccess(null);

    const nameErr = validateRequiredName(semesterName, "Semester name", 120);
    const yearErr = validateRequiredUuid(semesterYearId, "Academic year");
    if (nameErr || yearErr) {
      setActionError({
        status: 422,
        code: "validation_error",
        message: nameErr ?? yearErr ?? "Invalid semester inputs.",
      });
      return;
    }

    setCreatingSemester(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/semesters", {
          params: { path: { tenant_id: tenantId } },
          body: {
            name: semesterName.trim(),
            academic_year_id: semesterYearId,
          },
        }),
      );
      if (res.data) {
        setSemesterName("");
        setActionSuccess(`Semester "${res.data.name}" created.`);
        semestersList.resetToFirstPage();
        semestersList.reload();
      } else {
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to create semester."),
        );
      }
    } catch {
      setActionError({
        status: 0,
        code: "network_error",
        message: "Unable to communicate with backend service.",
      });
    } finally {
      setCreatingSemester(false);
    }
  };

  const handleSaveSemester = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId || !editingSemester) return;
    setActionError(null);
    setActionSuccess(null);

    const nameErr = validateRequiredName(editSemesterName, "Semester name", 120);
    if (nameErr) {
      setActionError({ status: 422, code: "validation_error", message: nameErr });
      return;
    }

    setBusy(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/semesters/{semester_id}", {
          params: { path: { tenant_id: tenantId, semester_id: editingSemester.id } },
          body: {
            name: editSemesterName.trim(),
            academic_year_id: editSemesterYearId || null,
          },
        }),
      );
      if (res.data) {
        setEditingSemester(null);
        setActionSuccess(`Semester updated to "${res.data.name}".`);
        semestersList.reload();
      } else {
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to update semester."),
        );
      }
    } catch {
      setActionError({
        status: 0,
        code: "network_error",
        message: "Unable to communicate with backend service.",
      });
    } finally {
      setBusy(false);
    }
  };

  const handleConfirmSemesterLifecycle = async () => {
    if (!tenantId || !lifecycleSemester) return;
    setBusy(true);
    setActionError(null);
    setActionSuccess(null);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/semesters/{semester_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, semester_id: lifecycleSemester.id } },
          body: { active: !lifecycleSemester.active },
        }),
      );
      if (res.data) {
        setActionSuccess(
          `Semester "${res.data.name}" ${res.data.active ? "activated" : "archived"}.`,
        );
        setLifecycleSemester(null);
        semestersList.reload();
      } else {
        setLifecycleSemester(null);
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to update semester status."),
        );
      }
    } catch {
      setLifecycleSemester(null);
      setActionError({
        status: 0,
        code: "network_error",
        message: "Unable to communicate with backend service.",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="viora-stack">
      <PageHeader
        title="Academic Years & Semesters"
        subtitle="Manage institutional academic calendar years and nested terms."
      />

      {actionSuccess && (
        <AlertBanner
          tone="success"
          message={actionSuccess}
          onDismiss={() => setActionSuccess(null)}
        />
      )}
      {actionError && (
        <AlertBanner
          tone="error"
          title="Operation failed"
          error={actionError}
          onDismiss={() => setActionError(null)}
        />
      )}

      <SectionCard
        title="Academic Years"
        actions={
          <select
            className="viora-select viora-select-inline"
            aria-label="Filter academic years by status"
            value={yearActiveFilter}
            onChange={(e) => {
              setYearActiveFilter(e.target.value as "all" | "active" | "archived");
              yearsList.resetToFirstPage();
            }}
          >
            <option value="all">All statuses</option>
            <option value="active">Active only</option>
            <option value="archived">Archived only</option>
          </select>
        }
      >
        <div className="viora-stack">
          {flags.isAdmin && (
            <form onSubmit={handleCreateYear} className="viora-row" style={{ alignItems: "flex-end" }}>
              <div style={{ flex: 1, minWidth: "220px", maxWidth: "380px" }}>
                <FormField label="New academic year" htmlFor="year-create-name">
                  <input
                    id="year-create-name"
                    type="text"
                    className="viora-input"
                    value={yearName}
                    onChange={(e) => setYearName(e.target.value)}
                    placeholder="2026-2027"
                    disabled={creatingYear}
                    required
                  />
                </FormField>
              </div>
              <button type="submit" className="viora-btn viora-btn-primary" disabled={creatingYear}>
                {creatingYear ? "Creating..." : "Create academic year"}
              </button>
            </form>
          )}

          {yearsList.loading && <LoadingSpinner message="Loading academic years..." />}
          {!yearsList.loading && yearsList.error && (
            <ErrorState
              title="Could not load academic years"
              message={`${yearsList.error.message} (${yearsList.error.code})`}
              onRetry={yearsList.reload}
            />
          )}
          {!yearsList.loading && !yearsList.error && (
            <>
              <DataTable
                rows={yearsList.items}
                rowKey={(y) => y.id}
                emptyTitle="No academic years configured"
                emptyDescription="Create an academic year above before adding semesters."
                columns={[
                  { key: "name", header: "Year", render: (y) => <strong>{y.name}</strong> },
                  { key: "id", header: "Year ID", render: (y) => <UuidChip value={y.id} /> },
                  { key: "status", header: "Status", render: (y) => <ActiveBadge active={y.active} /> },
                  {
                    key: "actions",
                    header: "Actions",
                    render: (y) => (
                      <div className="viora-row">
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
                        {flags.isAdmin && (
                          <>
                            <button
                              type="button"
                              className="viora-btn viora-btn-sm"
                              onClick={() => {
                                setEditingYear(y);
                                setEditYearName(y.name);
                              }}
                            >
                              Rename
                            </button>
                            <button
                              type="button"
                              className={`viora-btn viora-btn-sm ${y.active ? "viora-btn-danger" : ""}`}
                              onClick={() => setLifecycleYear(y)}
                            >
                              {y.active ? "Archive" : "Activate"}
                            </button>
                          </>
                        )}
                      </div>
                    ),
                  },
                ]}
              />
              <PaginationBar
                limit={yearsList.limit}
                onLimitChange={yearsList.setLimit}
                hasPrevious={yearsList.hasPrevious}
                hasNext={yearsList.hasNext}
                onPrevious={yearsList.goPrevious}
                onNext={yearsList.goNext}
                count={yearsList.items.length}
              />
            </>
          )}
        </div>
      </SectionCard>

      <SectionCard
        title="Semesters"
        actions={
          <div className="viora-row">
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
            <select
              className="viora-select viora-select-inline"
              aria-label="Filter semesters by status"
              value={semesterActiveFilter}
              onChange={(e) => {
                setSemesterActiveFilter(e.target.value as "all" | "active" | "archived");
                semestersList.resetToFirstPage();
              }}
            >
              <option value="all">All statuses</option>
              <option value="active">Active only</option>
              <option value="archived">Archived only</option>
            </select>
          </div>
        }
      >
        <div className="viora-stack">
          {flags.isAdmin && (
            <form onSubmit={handleCreateSemester} className="viora-row" style={{ alignItems: "flex-end" }}>
              <div style={{ flex: 1, minWidth: "200px" }}>
                <FormField label="Semester name" htmlFor="sem-create-name">
                  <input
                    id="sem-create-name"
                    type="text"
                    className="viora-input"
                    value={semesterName}
                    onChange={(e) => setSemesterName(e.target.value)}
                    placeholder="Fall Semester"
                    disabled={creatingSemester}
                    required
                  />
                </FormField>
              </div>
              <div style={{ flex: 1, minWidth: "200px" }}>
                <FormField label="Academic year (required)" htmlFor="sem-create-year">
                  <select
                    id="sem-create-year"
                    className="viora-select"
                    value={semesterYearId}
                    onChange={(e) => setSemesterYearId(e.target.value)}
                    disabled={creatingSemester}
                    required
                  >
                    <option value="">Select active academic year...</option>
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
              <button
                type="submit"
                className="viora-btn viora-btn-primary"
                disabled={creatingSemester}
              >
                {creatingSemester ? "Creating..." : "Create semester"}
              </button>
            </form>
          )}

          {semestersList.loading && <LoadingSpinner message="Loading semesters..." />}
          {!semestersList.loading && semestersList.error && (
            <ErrorState
              title="Could not load semesters"
              message={`${semestersList.error.message} (${semestersList.error.code})`}
              onRetry={semestersList.reload}
            />
          )}
          {!semestersList.loading && !semestersList.error && (
            <>
              <DataTable
                rows={semestersList.items}
                rowKey={(s) => s.id}
                emptyTitle="No semesters found"
                emptyDescription="Create a semester under an active academic year above."
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
                  { key: "id", header: "Semester ID", render: (s) => <UuidChip value={s.id} /> },
                  { key: "status", header: "Status", render: (s) => <ActiveBadge active={s.active} /> },
                  ...(flags.isAdmin
                    ? [
                        {
                          key: "actions",
                          header: "Actions",
                          render: (s: SemesterSummary) => (
                            <div className="viora-row">
                              <button
                                type="button"
                                className="viora-btn viora-btn-sm"
                                onClick={() => {
                                  setEditingSemester(s);
                                  setEditSemesterName(s.name);
                                  setEditSemesterYearId(s.academic_year_id);
                                }}
                              >
                                Edit
                              </button>
                              <button
                                type="button"
                                className={`viora-btn viora-btn-sm ${s.active ? "viora-btn-danger" : ""}`}
                                onClick={() => setLifecycleSemester(s)}
                              >
                                {s.active ? "Archive" : "Activate"}
                              </button>
                            </div>
                          ),
                        },
                      ]
                    : []),
                ]}
              />
              <PaginationBar
                limit={semestersList.limit}
                onLimitChange={semestersList.setLimit}
                hasPrevious={semestersList.hasPrevious}
                hasNext={semestersList.hasNext}
                onPrevious={semestersList.goPrevious}
                onNext={semestersList.goNext}
                count={semestersList.items.length}
              />
            </>
          )}
        </div>
      </SectionCard>

      <ModalDialog
        open={editingYear !== null}
        title="Rename Academic Year"
        onClose={() => setEditingYear(null)}
      >
        <form onSubmit={handleSaveYear} className="viora-stack">
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
          <div className="viora-row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="viora-btn" onClick={() => setEditingYear(null)}>
              Cancel
            </button>
            <button type="submit" className="viora-btn viora-btn-primary" disabled={busy}>
              Save changes
            </button>
          </div>
        </form>
      </ModalDialog>

      <ModalDialog
        open={editingSemester !== null}
        title="Edit Semester"
        onClose={() => setEditingSemester(null)}
      >
        <form onSubmit={handleSaveSemester} className="viora-stack">
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
          <div className="viora-row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="viora-btn" onClick={() => setEditingSemester(null)}>
              Cancel
            </button>
            <button type="submit" className="viora-btn viora-btn-primary" disabled={busy}>
              Save changes
            </button>
          </div>
        </form>
      </ModalDialog>

      <ConfirmDialog
        open={lifecycleYear !== null}
        title={lifecycleYear?.active ? "Archive academic year?" : "Activate academic year?"}
        message={`Change status for academic year "${lifecycleYear?.name ?? ""}"? Archiving blocks new semesters under this year.`}
        confirmLabel={lifecycleYear?.active ? "Archive" : "Activate"}
        danger={Boolean(lifecycleYear?.active)}
        busy={busy}
        onCancel={() => setLifecycleYear(null)}
        onConfirm={() => void handleConfirmYearLifecycle()}
      />

      <ConfirmDialog
        open={lifecycleSemester !== null}
        title={lifecycleSemester?.active ? "Archive semester?" : "Activate semester?"}
        message={`Change status for semester "${lifecycleSemester?.name ?? ""}"? Archiving blocks new sections under this semester.`}
        confirmLabel={lifecycleSemester?.active ? "Archive" : "Activate"}
        danger={Boolean(lifecycleSemester?.active)}
        busy={busy}
        onCancel={() => setLifecycleSemester(null)}
        onConfirm={() => void handleConfirmSemesterLifecycle()}
      />
    </div>
  );
}
