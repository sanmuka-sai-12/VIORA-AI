import { useCallback, useState, type FormEvent } from "react";
import {
  normalizeApiError,
  type DepartmentSummary,
  type NormalizedApiError,
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
import { AssignmentModal } from "../common/AssignmentModal";
import { useCursorList } from "../common/useCursorList";
import { resolveLabelById, useTenantOptions } from "../common/useTenantOptions";

export function DepartmentsPage() {
  const { apiClient, controller: authController } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);

  const options = useTenantOptions({ campuses: true, members: true });

  const [campusFilter, setCampusFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState<"all" | "active" | "archived">("all");
  const [actionError, setActionError] = useState<NormalizedApiError | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  const [createName, setCreateName] = useState("");
  const [createCampusId, setCreateCampusId] = useState("");
  const [creating, setCreating] = useState(false);

  const [editing, setEditing] = useState<DepartmentSummary | null>(null);
  const [editName, setEditName] = useState("");
  const [editCampusId, setEditCampusId] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);

  const [lifecycleTarget, setLifecycleTarget] = useState<DepartmentSummary | null>(null);
  const [toggling, setToggling] = useState(false);

  const [headsDept, setHeadsDept] = useState<DepartmentSummary | null>(null);

  const fetcher = useCallback(
    ({ cursor, limit }: { cursor?: string; limit: number }) =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/departments", {
        params: {
          path: { tenant_id: tenantId! },
          query: {
            campus_id: campusFilter || undefined,
            active:
              activeFilter === "all"
                ? undefined
                : activeFilter === "active",
            cursor,
            limit,
          },
        },
      }),
    [activeFilter, apiClient, campusFilter, tenantId],
  );

  const list = useCursorList<DepartmentSummary>(tenantId, fetcher);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId) return;
    setActionError(null);
    setActionSuccess(null);

    const problem = validateRequiredName(createName, "Department name", 160);
    if (problem) {
      setActionError({ status: 422, code: "validation_error", message: problem });
      return;
    }

    setCreating(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/departments", {
          params: { path: { tenant_id: tenantId } },
          body: {
            name: createName.trim(),
            campus_id: createCampusId || null,
          },
        }),
      );
      if (res.data) {
        setCreateName("");
        setCreateCampusId("");
        setActionSuccess(`Department "${res.data.name}" created.`);
        list.resetToFirstPage();
        list.reload();
      } else {
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to create department."),
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

    const problem = validateRequiredName(editName, "Department name", 160);
    if (problem) {
      setActionError({ status: 422, code: "validation_error", message: problem });
      return;
    }

    setSavingEdit(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/departments/{department_id}", {
          params: { path: { tenant_id: tenantId, department_id: editing.id } },
          body: flags.isAdmin
            ? { name: editName.trim(), campus_id: editCampusId || null }
            : { name: editName.trim() },
        }),
      );
      if (res.data) {
        setEditing(null);
        setActionSuccess(`Department "${res.data.name}" updated.`);
        list.reload();
      } else {
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to update department."),
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
        apiClient.POST("/api/v1/tenants/{tenant_id}/departments/{department_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, department_id: lifecycleTarget.id } },
          body: { active: nextActive },
        }),
      );
      if (res.data) {
        setActionSuccess(
          `Department "${res.data.name}" ${res.data.active ? "activated" : "archived"}.`,
        );
        setLifecycleTarget(null);
        list.reload();
      } else {
        setLifecycleTarget(null);
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to update department status."),
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
        title="Departments & Department Heads"
        subtitle={
          flags.isAdmin
            ? "Organize academic departments under campuses and assign department heads."
            : "Departments assigned to your leadership scope."
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
        <SectionCard title="Create Department">
          <form onSubmit={handleCreate} className="viora-row" style={{ alignItems: "flex-end" }}>
            <div style={{ flex: 1, minWidth: "220px" }}>
              <FormField label="Department name" htmlFor="dept-create-name">
                <input
                  id="dept-create-name"
                  type="text"
                  className="viora-input"
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  placeholder="Computer Science"
                  disabled={creating}
                  required
                />
              </FormField>
            </div>
            <div style={{ flex: 1, minWidth: "200px" }}>
              <FormField label="Campus (optional)" htmlFor="dept-create-campus">
                <select
                  id="dept-create-campus"
                  className="viora-select"
                  value={createCampusId}
                  onChange={(e) => setCreateCampusId(e.target.value)}
                  disabled={creating}
                >
                  <option value="">No specific campus</option>
                  {options.campuses
                    .filter((c) => c.active)
                    .map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                </select>
              </FormField>
            </div>
            <button
              type="submit"
              className="viora-btn viora-btn-primary"
              disabled={creating}
            >
              {creating ? "Creating..." : "Create department"}
            </button>
          </form>
        </SectionCard>
      )}

      <SectionCard
        title="Departments"
        actions={
          <div className="viora-row">
            {flags.isAdmin && options.campuses.length > 0 && (
              <select
                className="viora-select viora-select-inline"
                aria-label="Filter by campus"
                value={campusFilter}
                onChange={(e) => {
                  setCampusFilter(e.target.value);
                  list.resetToFirstPage();
                }}
              >
                <option value="">All campuses</option>
                {options.campuses.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
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
        {list.loading && <LoadingSpinner message="Loading departments..." />}
        {!list.loading && list.error && (
          <ErrorState
            title="Could not load departments"
            message={`${list.error.message} (${list.error.code})`}
            onRetry={list.reload}
          />
        )}
        {!list.loading && !list.error && (
          <>
            <DataTable
              rows={list.items}
              rowKey={(d) => d.id}
              emptyTitle="No departments available"
              emptyDescription="No departments match your scope or current filter."
              columns={[
                {
                  key: "name",
                  header: "Department",
                  render: (d) => <strong>{d.name}</strong>,
                },
                {
                  key: "campus",
                  header: "Campus",
                  render: (d) => {
                    const label = resolveLabelById(options.campuses, d.campus_id);
                    if (label) return label;
                    return d.campus_id ? <UuidChip value={d.campus_id} /> : "Institution-wide";
                  },
                },
                {
                  key: "id",
                  header: "Department ID",
                  render: (d) => <UuidChip value={d.id} />,
                },
                {
                  key: "status",
                  header: "Status",
                  render: (d) => <ActiveBadge active={d.active} />,
                },
                {
                  key: "actions",
                  header: "Actions",
                  render: (d) => (
                    <div className="viora-row">
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm"
                        onClick={() => setHeadsDept(d)}
                      >
                        Heads
                      </button>
                      {(flags.isAdmin || flags.isDepartmentHead) && (
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm"
                          onClick={() => {
                            setEditing(d);
                            setEditName(d.name);
                            setEditCampusId(d.campus_id ?? "");
                          }}
                        >
                          Edit
                        </button>
                      )}
                      {flags.isAdmin && (
                        <button
                          type="button"
                          className={`viora-btn viora-btn-sm ${d.active ? "viora-btn-danger" : ""}`}
                          onClick={() => setLifecycleTarget(d)}
                        >
                          {d.active ? "Archive" : "Activate"}
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

      <ModalDialog
        open={editing !== null}
        title="Edit Department"
        onClose={() => setEditing(null)}
      >
        <form onSubmit={handleSaveEdit} className="viora-stack">
          <FormField label="Department name" htmlFor="dept-edit-name">
            <input
              id="dept-edit-name"
              type="text"
              className="viora-input"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              disabled={savingEdit}
              required
            />
          </FormField>
          {flags.isAdmin && (
            <FormField label="Campus (optional)" htmlFor="dept-edit-campus">
              <select
                id="dept-edit-campus"
                className="viora-select"
                value={editCampusId}
                onChange={(e) => setEditCampusId(e.target.value)}
                disabled={savingEdit}
              >
                <option value="">No specific campus</option>
                {options.campuses.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                    {c.active ? "" : " (Archived)"}
                  </option>
                ))}
              </select>
            </FormField>
          )}
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
        title={lifecycleTarget?.active ? "Archive department?" : "Activate department?"}
        message={
          lifecycleTarget?.active
            ? `Archiving "${lifecycleTarget.name}" blocks new programme, subject, and head assignments under it.`
            : `Reactivating "${lifecycleTarget?.name ?? ""}" restores active status.`
        }
        confirmLabel={lifecycleTarget?.active ? "Archive" : "Activate"}
        danger={Boolean(lifecycleTarget?.active)}
        busy={toggling}
        onCancel={() => setLifecycleTarget(null)}
        onConfirm={() => void handleConfirmLifecycle()}
      />

      {tenantId && headsDept && (
        <AssignmentModal
          open={headsDept !== null}
          title={`Department Heads — ${headsDept.name}`}
          subtitle="Users must hold the department_head role within an active institutional membership."
          canManage={flags.isAdmin}
          eligibleRoleFilter="department_head"
          assignActionLabel="Assign department head"
          revokeActionLabel="Revoke head"
          members={options.members}
          onClose={() => setHeadsDept(null)}
          listFn={() =>
            apiClient.GET("/api/v1/tenants/{tenant_id}/departments/{department_id}/heads", {
              params: {
                path: { tenant_id: tenantId, department_id: headsDept.id },
                query: { limit: 100 },
              },
            })
          }
          assignFn={(userId) =>
            apiClient.POST("/api/v1/tenants/{tenant_id}/departments/{department_id}/heads", {
              params: { path: { tenant_id: tenantId, department_id: headsDept.id } },
              body: { user_id: userId },
            })
          }
          revokeFn={(userId) =>
            apiClient.POST(
              "/api/v1/tenants/{tenant_id}/departments/{department_id}/heads/{user_id}/revoke",
              {
                params: {
                  path: {
                    tenant_id: tenantId,
                    department_id: headsDept.id,
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
