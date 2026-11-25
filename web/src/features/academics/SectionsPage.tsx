import { useCallback, useState, type FormEvent } from "react";
import {
  normalizeApiError,
  type NormalizedApiError,
  type SectionSummary,
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
import { AssignmentModal } from "../common/AssignmentModal";
import { useCursorList } from "../common/useCursorList";
import { resolveLabelById, useTenantOptions } from "../common/useTenantOptions";

export function SectionsPage() {
  const { apiClient, controller: authController } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);

  const options = useTenantOptions({
    semesters: true,
    programmes: true,
    members: true,
  });

  const [semesterFilter, setSemesterFilter] = useState("");
  const [programmeFilter, setProgrammeFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState<"all" | "active" | "archived">("all");
  const [actionError, setActionError] = useState<NormalizedApiError | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  const [createName, setCreateName] = useState("");
  const [createSemesterId, setCreateSemesterId] = useState("");
  const [createProgrammeId, setCreateProgrammeId] = useState("");
  const [creating, setCreating] = useState(false);

  const [editing, setEditing] = useState<SectionSummary | null>(null);
  const [editName, setEditName] = useState("");
  const [editSemesterId, setEditSemesterId] = useState("");
  const [editProgrammeId, setEditProgrammeId] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);

  const [lifecycleTarget, setLifecycleTarget] = useState<SectionSummary | null>(null);
  const [toggling, setToggling] = useState(false);
  const [coordsSection, setCoordsSection] = useState<SectionSummary | null>(null);

  const fetcher = useCallback(
    ({ cursor, limit }: { cursor?: string; limit: number }) =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/sections", {
        params: {
          path: { tenant_id: tenantId! },
          query: {
            semester_id: semesterFilter || undefined,
            programme_id: programmeFilter || undefined,
            active: activeFilter === "all" ? undefined : activeFilter === "active",
            cursor,
            limit,
          },
        },
      }),
    [activeFilter, apiClient, programmeFilter, semesterFilter, tenantId],
  );

  const list = useCursorList<SectionSummary>(tenantId, fetcher);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId) return;
    setActionError(null);
    setActionSuccess(null);

    const nameErr = validateRequiredName(createName, "Section name", 160);
    const semErr = validateRequiredUuid(createSemesterId, "Semester");
    if (nameErr || semErr) {
      setActionError({
        status: 422,
        code: "validation_error",
        message: nameErr ?? semErr ?? "Invalid section inputs.",
      });
      return;
    }

    setCreating(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/sections", {
          params: { path: { tenant_id: tenantId } },
          body: {
            name: createName.trim(),
            semester_id: createSemesterId,
            programme_id: createProgrammeId || null,
          },
        }),
      );
      if (res.data) {
        setCreateName("");
        setActionSuccess(`Class section "${res.data.name}" created.`);
        list.resetToFirstPage();
        list.reload();
      } else {
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to create class section."),
        );
      }
    } catch {
      setActionError({
        status: 0,
        code: "network_error",
        message: "Unable to communicate with backend service.",
      });
    } finally {
      setCreating(false);
    }
  };

  const handleSaveEdit = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId || !editing) return;
    setActionError(null);
    setActionSuccess(null);

    const problem = validateRequiredName(editName, "Section name", 160);
    if (problem) {
      setActionError({ status: 422, code: "validation_error", message: problem });
      return;
    }

    setSavingEdit(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/sections/{section_id}", {
          params: { path: { tenant_id: tenantId, section_id: editing.id } },
          body: flags.isAdmin
            ? {
                name: editName.trim(),
                semester_id: editSemesterId || null,
                programme_id: editProgrammeId || null,
              }
            : { name: editName.trim() },
        }),
      );
      if (res.data) {
        setEditing(null);
        setActionSuccess(`Class section "${res.data.name}" updated.`);
        list.reload();
      } else {
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to update class section."),
        );
      }
    } catch {
      setActionError({
        status: 0,
        code: "network_error",
        message: "Unable to communicate with backend service.",
      });
    } finally {
      setSavingEdit(false);
    }
  };

  const handleConfirmLifecycle = async () => {
    if (!tenantId || !lifecycleTarget) return;
    setToggling(true);
    setActionError(null);
    setActionSuccess(null);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/sections/{section_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, section_id: lifecycleTarget.id } },
          body: { active: !lifecycleTarget.active },
        }),
      );
      if (res.data) {
        setActionSuccess(
          `Section "${res.data.name}" ${res.data.active ? "activated" : "archived"}.`,
        );
        setLifecycleTarget(null);
        list.reload();
      } else {
        setLifecycleTarget(null);
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to update section status."),
        );
      }
    } catch {
      setLifecycleTarget(null);
      setActionError({
        status: 0,
        code: "network_error",
        message: "Unable to communicate with backend service.",
      });
    } finally {
      setToggling(false);
    }
  };

  return (
    <div className="viora-stack">
      <PageHeader
        title="Class Sections & Coordinators"
        subtitle={
          flags.isAdmin
            ? "Create student cohorts tied to semesters/programmes and assign class coordinators."
            : "Class sections assigned to your coordination scope."
        }
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

      {flags.isAdmin && (
        <SectionCard title="Create Class Section">
          <form onSubmit={handleCreate} className="viora-grid-3" style={{ alignItems: "end" }}>
            <FormField label="Section name" htmlFor="sec-create-name">
              <input
                id="sec-create-name"
                type="text"
                className="viora-input"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                placeholder="CSE-A"
                disabled={creating}
                required
              />
            </FormField>

            <FormField label="Semester (required)" htmlFor="sec-create-sem">
              <select
                id="sec-create-sem"
                className="viora-select"
                value={createSemesterId}
                onChange={(e) => setCreateSemesterId(e.target.value)}
                disabled={creating}
                required
              >
                <option value="">Select active semester...</option>
                {options.semesters
                  .filter((s) => s.active)
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
              </select>
            </FormField>

            <FormField label="Programme (optional)" htmlFor="sec-create-prog">
              <select
                id="sec-create-prog"
                className="viora-select"
                value={createProgrammeId}
                onChange={(e) => setCreateProgrammeId(e.target.value)}
                disabled={creating}
              >
                <option value="">None</option>
                {options.programmes
                  .filter((p) => p.active)
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
              </select>
            </FormField>

            <div>
              <button type="submit" className="viora-btn viora-btn-primary" disabled={creating}>
                {creating ? "Creating..." : "Create section"}
              </button>
            </div>
          </form>
        </SectionCard>
      )}

      <SectionCard
        title="Sections"
        actions={
          <div className="viora-row">
            {flags.isAdmin && options.semesters.length > 0 && (
              <select
                className="viora-select viora-select-inline"
                aria-label="Filter sections by semester"
                value={semesterFilter}
                onChange={(e) => {
                  setSemesterFilter(e.target.value);
                  list.resetToFirstPage();
                }}
              >
                <option value="">All semesters</option>
                {options.semesters.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            )}
            {options.programmes.length > 0 && (
              <select
                className="viora-select viora-select-inline"
                aria-label="Filter sections by programme"
                value={programmeFilter}
                onChange={(e) => {
                  setProgrammeFilter(e.target.value);
                  list.resetToFirstPage();
                }}
              >
                <option value="">All programmes</option>
                {options.programmes.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            )}
            <select
              className="viora-select viora-select-inline"
              aria-label="Filter sections by status"
              value={activeFilter}
              onChange={(e) => {
                setActiveFilter(e.target.value as "all" | "active" | "archived");
                list.resetToFirstPage();
              }}
            >
              <option value="all">All statuses</option>
              <option value="active">Active only</option>
              <option value="archived">Archived only</option>
            </select>
          </div>
        }
      >
        {list.loading && <LoadingSpinner message="Loading class sections..." />}
        {!list.loading && list.error && (
          <ErrorState
            title="Could not load class sections"
            message={`${list.error.message} (${list.error.code})`}
            onRetry={list.reload}
          />
        )}
        {!list.loading && !list.error && (
          <>
            <DataTable
              rows={list.items}
              rowKey={(s) => s.id}
              emptyTitle="No class sections available"
              emptyDescription="No sections match your scope or filter."
              columns={[
                { key: "name", header: "Section", render: (s) => <strong>{s.name}</strong> },
                {
                  key: "semester",
                  header: "Semester",
                  render: (s) =>
                    resolveLabelById(options.semesters, s.semester_id) ?? (
                      <UuidChip value={s.semester_id} />
                    ),
                },
                {
                  key: "programme",
                  header: "Programme",
                  render: (s) => {
                    const label = resolveLabelById(options.programmes, s.programme_id);
                    if (label) return label;
                    return s.programme_id ? <UuidChip value={s.programme_id} /> : "Unassigned";
                  },
                },
                { key: "status", header: "Status", render: (s) => <ActiveBadge active={s.active} /> },
                {
                  key: "actions",
                  header: "Actions",
                  render: (s) => (
                    <div className="viora-row">
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm"
                        onClick={() => setCoordsSection(s)}
                      >
                        Coordinators
                      </button>
                      {(flags.isAdmin || flags.isClassCoordinator) && (
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm"
                          onClick={() => {
                            setEditing(s);
                            setEditName(s.name);
                            setEditSemesterId(s.semester_id);
                            setEditProgrammeId(s.programme_id ?? "");
                          }}
                        >
                          Edit
                        </button>
                      )}
                      {flags.isAdmin && (
                        <button
                          type="button"
                          className={`viora-btn viora-btn-sm ${s.active ? "viora-btn-danger" : ""}`}
                          onClick={() => setLifecycleTarget(s)}
                        >
                          {s.active ? "Archive" : "Activate"}
                        </button>
                      )}
                    </div>
                  ),
                },
              ]}
            />
            <PaginationBar
              limit={list.limit}
              onLimitChange={list.setLimit}
              hasPrevious={list.hasPrevious}
              hasNext={list.hasNext}
              onPrevious={list.goPrevious}
              onNext={list.goNext}
              count={list.items.length}
            />
          </>
        )}
      </SectionCard>

      <ModalDialog open={editing !== null} title="Edit Section" onClose={() => setEditing(null)}>
        <form onSubmit={handleSaveEdit} className="viora-stack">
          <FormField label="Section name" htmlFor="sec-edit-name">
            <input
              id="sec-edit-name"
              type="text"
              className="viora-input"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              disabled={savingEdit}
              required
            />
          </FormField>
          {flags.isAdmin && (
            <>
              <FormField label="Semester" htmlFor="sec-edit-sem">
                <select
                  id="sec-edit-sem"
                  className="viora-select"
                  value={editSemesterId}
                  onChange={(e) => setEditSemesterId(e.target.value)}
                  disabled={savingEdit}
                >
                  {options.semesters.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                      {s.active ? "" : " (Archived)"}
                    </option>
                  ))}
                </select>
              </FormField>
              <FormField label="Programme (optional)" htmlFor="sec-edit-prog">
                <select
                  id="sec-edit-prog"
                  className="viora-select"
                  value={editProgrammeId}
                  onChange={(e) => setEditProgrammeId(e.target.value)}
                  disabled={savingEdit}
                >
                  <option value="">None</option>
                  {options.programmes.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                      {p.active ? "" : " (Archived)"}
                    </option>
                  ))}
                </select>
              </FormField>
            </>
          )}
          <div className="viora-row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="viora-btn" onClick={() => setEditing(null)}>
              Cancel
            </button>
            <button type="submit" className="viora-btn viora-btn-primary" disabled={savingEdit}>
              Save changes
            </button>
          </div>
        </form>
      </ModalDialog>

      <ConfirmDialog
        open={lifecycleTarget !== null}
        title={lifecycleTarget?.active ? "Archive section?" : "Activate section?"}
        message={`Update status for section "${lifecycleTarget?.name ?? ""}"? Archiving blocks new offerings and coordinator assignments.`}
        confirmLabel={lifecycleTarget?.active ? "Archive" : "Activate"}
        danger={Boolean(lifecycleTarget?.active)}
        busy={toggling}
        onCancel={() => setLifecycleTarget(null)}
        onConfirm={() => void handleConfirmLifecycle()}
      />

      {tenantId && coordsSection && (
        <AssignmentModal
          open={coordsSection !== null}
          title={`Class Coordinators — ${coordsSection.name}`}
          subtitle="Users must hold the class_coordinator role within an active institutional membership."
          canManage={flags.isAdmin}
          eligibleRoleFilter="class_coordinator"
          assignActionLabel="Assign coordinator"
          revokeActionLabel="Revoke coordinator"
          members={options.members}
          onClose={() => setCoordsSection(null)}
          listFn={() =>
            apiClient.GET("/api/v1/tenants/{tenant_id}/sections/{section_id}/coordinators", {
              params: {
                path: { tenant_id: tenantId, section_id: coordsSection.id },
                query: { limit: 100 },
              },
            })
          }
          assignFn={(userId) =>
            apiClient.POST("/api/v1/tenants/{tenant_id}/sections/{section_id}/coordinators", {
              params: { path: { tenant_id: tenantId, section_id: coordsSection.id } },
              body: { user_id: userId },
            })
          }
          revokeFn={(userId) =>
            apiClient.POST(
              "/api/v1/tenants/{tenant_id}/sections/{section_id}/coordinators/{user_id}/revoke",
              {
                params: {
                  path: {
                    tenant_id: tenantId,
                    section_id: coordsSection.id,
                    user_id: userId,
                  },
                },
              },
            )
          }
        />
      )}
    </div>
  );
}
