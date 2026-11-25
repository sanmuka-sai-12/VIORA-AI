import { useCallback, useState, type FormEvent } from "react";
import {
  normalizeApiError,
  type NormalizedApiError,
  type ProgrammeSummary,
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
import { validateRequiredName } from "../../tenant/validation";
import { useCursorList } from "../common/useCursorList";
import { resolveLabelById, useTenantOptions } from "../common/useTenantOptions";

export function ProgrammesPage() {
  const { apiClient, controller: authController } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);

  const options = useTenantOptions({ departments: true });

  const [deptFilter, setDeptFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState<"all" | "active" | "archived">("all");
  const [actionError, setActionError] = useState<NormalizedApiError | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  const [createName, setCreateName] = useState("");
  const [createDeptId, setCreateDeptId] = useState("");
  const [creating, setCreating] = useState(false);

  const [editing, setEditing] = useState<ProgrammeSummary | null>(null);
  const [editName, setEditName] = useState("");
  const [editDeptId, setEditDeptId] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);

  const [lifecycleTarget, setLifecycleTarget] = useState<ProgrammeSummary | null>(null);
  const [toggling, setToggling] = useState(false);

  const fetcher = useCallback(
    ({ cursor, limit }: { cursor?: string; limit: number }) =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/programmes", {
        params: {
          path: { tenant_id: tenantId! },
          query: {
            department_id: deptFilter || undefined,
            active:
              activeFilter === "all"
                ? undefined
                : activeFilter === "active",
            cursor,
            limit,
          },
        },
      }),
    [activeFilter, apiClient, deptFilter, tenantId],
  );

  const list = useCursorList<ProgrammeSummary>(tenantId, fetcher);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId) return;
    setActionError(null);
    setActionSuccess(null);

    const problem = validateRequiredName(createName, "Programme name", 160);
    if (problem) {
      setActionError({ status: 422, code: "validation_error", message: problem });
      return;
    }

    setCreating(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/programmes", {
          params: { path: { tenant_id: tenantId } },
          body: {
            name: createName.trim(),
            department_id: createDeptId || null,
          },
        }),
      );
      if (res.data) {
        setCreateName("");
        setCreateDeptId("");
        setActionSuccess(`Programme "${res.data.name}" created.`);
        list.resetToFirstPage();
        list.reload();
      } else {
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to create programme."),
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

    const problem = validateRequiredName(editName, "Programme name", 160);
    if (problem) {
      setActionError({ status: 422, code: "validation_error", message: problem });
      return;
    }

    setSavingEdit(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/programmes/{programme_id}", {
          params: { path: { tenant_id: tenantId, programme_id: editing.id } },
          body: {
            name: editName.trim(),
            department_id: editDeptId || null,
          },
        }),
      );
      if (res.data) {
        setEditing(null);
        setActionSuccess(`Programme "${res.data.name}" updated.`);
        list.reload();
      } else {
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to update programme."),
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
    const nextActive = !lifecycleTarget.active;
    setToggling(true);
    setActionError(null);
    setActionSuccess(null);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/programmes/{programme_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, programme_id: lifecycleTarget.id } },
          body: { active: nextActive },
        }),
      );
      if (res.data) {
        setActionSuccess(
          `Programme "${res.data.name}" ${res.data.active ? "activated" : "archived"}.`,
        );
        setLifecycleTarget(null);
        list.reload();
      } else {
        setLifecycleTarget(null);
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to update programme status."),
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
        title="Programmes"
        subtitle="Academic degree or curriculum tracks within the institution."
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
        <SectionCard title="Create Programme">
          <form onSubmit={handleCreate} className="viora-row" style={{ alignItems: "flex-end" }}>
            <div style={{ flex: 1, minWidth: "220px" }}>
              <FormField label="Programme name" htmlFor="prog-create-name">
                <input
                  id="prog-create-name"
                  type="text"
                  className="viora-input"
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  placeholder="B.Tech Computer Science"
                  disabled={creating}
                  required
                />
              </FormField>
            </div>
            <div style={{ flex: 1, minWidth: "200px" }}>
              <FormField label="Department (optional)" htmlFor="prog-create-dept">
                <select
                  id="prog-create-dept"
                  className="viora-select"
                  value={createDeptId}
                  onChange={(e) => setCreateDeptId(e.target.value)}
                  disabled={creating}
                >
                  <option value="">No specific department</option>
                  {options.departments
                    .filter((d) => d.active)
                    .map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                </select>
              </FormField>
            </div>
            <button type="submit" className="viora-btn viora-btn-primary" disabled={creating}>
              {creating ? "Creating..." : "Create programme"}
            </button>
          </form>
        </SectionCard>
      )}

      <SectionCard
        title="Programme List"
        actions={
          <div className="viora-row">
            {options.departments.length > 0 && (
              <select
                className="viora-select viora-select-inline"
                aria-label="Filter by department"
                value={deptFilter}
                onChange={(e) => {
                  setDeptFilter(e.target.value);
                  list.resetToFirstPage();
                }}
              >
                <option value="">All departments</option>
                {options.departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            )}
            <select
              className="viora-select viora-select-inline"
              aria-label="Filter by status"
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
        {list.loading && <LoadingSpinner message="Loading programmes..." />}
        {!list.loading && list.error && (
          <ErrorState
            title="Could not load programmes"
            message={`${list.error.message} (${list.error.code})`}
            onRetry={list.reload}
          />
        )}
        {!list.loading && !list.error && (
          <>
            <DataTable
              rows={list.items}
              rowKey={(p) => p.id}
              emptyTitle="No programmes found"
              emptyDescription="No programmes match the selected filters."
              columns={[
                { key: "name", header: "Programme", render: (p) => <strong>{p.name}</strong> },
                {
                  key: "department",
                  header: "Department",
                  render: (p) => {
                    const label = resolveLabelById(options.departments, p.department_id);
                    if (label) return label;
                    return p.department_id ? <UuidChip value={p.department_id} /> : "Unassigned";
                  },
                },
                { key: "id", header: "Programme ID", render: (p) => <UuidChip value={p.id} /> },
                { key: "status", header: "Status", render: (p) => <ActiveBadge active={p.active} /> },
                ...(flags.isAdmin
                  ? [
                      {
                        key: "actions",
                        header: "Actions",
                        render: (p: ProgrammeSummary) => (
                          <div className="viora-row">
                            <button
                              type="button"
                              className="viora-btn viora-btn-sm"
                              onClick={() => {
                                setEditing(p);
                                setEditName(p.name);
                                setEditDeptId(p.department_id ?? "");
                              }}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className={`viora-btn viora-btn-sm ${p.active ? "viora-btn-danger" : ""}`}
                              onClick={() => setLifecycleTarget(p)}
                            >
                              {p.active ? "Archive" : "Activate"}
                            </button>
                          </div>
                        ),
                      },
                    ]
                  : []),
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

      <ModalDialog open={editing !== null} title="Edit Programme" onClose={() => setEditing(null)}>
        <form onSubmit={handleSaveEdit} className="viora-stack">
          <FormField label="Programme name" htmlFor="prog-edit-name">
            <input
              id="prog-edit-name"
              type="text"
              className="viora-input"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              disabled={savingEdit}
              required
            />
          </FormField>
          <FormField label="Department (optional)" htmlFor="prog-edit-dept">
            <select
              id="prog-edit-dept"
              className="viora-select"
              value={editDeptId}
              onChange={(e) => setEditDeptId(e.target.value)}
              disabled={savingEdit}
            >
              <option value="">No specific department</option>
              {options.departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                  {d.active ? "" : " (Archived)"}
                </option>
              ))}
            </select>
          </FormField>
          <div className="viora-row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="viora-btn" onClick={() => setEditing(null)}>
              Cancel
            </button>
            <button type="submit" className="viora-btn viora-btn-primary" disabled={savingEdit}>
              {savingEdit ? "Saving..." : "Save changes"}
            </button>
          </div>
        </form>
      </ModalDialog>

      <ConfirmDialog
        open={lifecycleTarget !== null}
        title={lifecycleTarget?.active ? "Archive programme?" : "Activate programme?"}
        message={`Update active status for "${lifecycleTarget?.name ?? ""}"?`}
        confirmLabel={lifecycleTarget?.active ? "Archive" : "Activate"}
        danger={Boolean(lifecycleTarget?.active)}
        busy={toggling}
        onCancel={() => setLifecycleTarget(null)}
        onConfirm={() => void handleConfirmLifecycle()}
      />
    </div>
  );
}
