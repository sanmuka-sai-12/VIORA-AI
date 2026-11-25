import { useCallback, useState, type FormEvent } from "react";
import {
  normalizeApiError,
  type CampusSummary,
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
import { useCursorList } from "../common/useCursorList";

export function CampusesPage() {
  const { apiClient, controller: authController } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);

  const [activeFilter, setActiveFilter] = useState<"all" | "active" | "archived">("all");
  const [actionError, setActionError] = useState<NormalizedApiError | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  const [createName, setCreateName] = useState("");
  const [creating, setCreating] = useState(false);

  const [editing, setEditing] = useState<CampusSummary | null>(null);
  const [editName, setEditName] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);

  const [lifecycleTarget, setLifecycleTarget] = useState<CampusSummary | null>(null);
  const [toggling, setToggling] = useState(false);

  const fetcher = useCallback(
    ({ cursor, limit }: { cursor?: string; limit: number }) =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/campuses", {
        params: {
          path: { tenant_id: tenantId! },
          query: {
            active:
              activeFilter === "all"
                ? undefined
                : activeFilter === "active",
            cursor,
            limit,
          },
        },
      }),
    [activeFilter, apiClient, tenantId],
  );

  const list = useCursorList<CampusSummary>(tenantId, fetcher);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId) return;
    setActionError(null);
    setActionSuccess(null);

    const problem = validateRequiredName(createName, "Campus name", 160);
    if (problem) {
      setActionError({ status: 422, code: "validation_error", message: problem });
      return;
    }

    setCreating(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/campuses", {
          params: { path: { tenant_id: tenantId } },
          body: { name: createName.trim() },
        }),
      );
      if (res.data) {
        setCreateName("");
        setActionSuccess(`Campus "${res.data.name}" created.`);
        list.resetToFirstPage();
        list.reload();
      } else {
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to create campus."),
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

    const problem = validateRequiredName(editName, "Campus name", 160);
    if (problem) {
      setActionError({ status: 422, code: "validation_error", message: problem });
      return;
    }

    setSavingEdit(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/campuses/{campus_id}", {
          params: { path: { tenant_id: tenantId, campus_id: editing.id } },
          body: { name: editName.trim() },
        }),
      );
      if (res.data) {
        setEditing(null);
        setActionSuccess(`Campus updated to "${res.data.name}".`);
        list.reload();
      } else {
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to update campus."),
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
        apiClient.POST("/api/v1/tenants/{tenant_id}/campuses/{campus_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, campus_id: lifecycleTarget.id } },
          body: { active: nextActive },
        }),
      );
      if (res.data) {
        setActionSuccess(
          `Campus "${res.data.name}" ${res.data.active ? "activated" : "archived"}.`,
        );
        setLifecycleTarget(null);
        list.reload();
      } else {
        setLifecycleTarget(null);
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to update campus status."),
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
        title="Campuses"
        subtitle="Manage physical and organizational campuses for this institution."
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
        <SectionCard title="Add Campus">
          <form onSubmit={handleCreate} className="viora-row" style={{ alignItems: "flex-end" }}>
            <div style={{ flex: 1, minWidth: "240px", maxWidth: "420px" }}>
              <FormField label="Campus name" htmlFor="campus-create-name">
                <input
                  id="campus-create-name"
                  type="text"
                  className="viora-input"
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  placeholder="North Campus"
                  disabled={creating}
                  required
                />
              </FormField>
            </div>
            <button
              type="submit"
              className="viora-btn viora-btn-primary"
              disabled={creating}
            >
              {creating ? "Creating..." : "Create campus"}
            </button>
          </form>
        </SectionCard>
      )}

      <SectionCard
        title="Campus Directory"
        actions={
          <div className="viora-row">
            <label htmlFor="campus-active-filter" className="viora-field-hint">
              Status:
            </label>
            <select
              id="campus-active-filter"
              className="viora-select viora-select-inline"
              value={activeFilter}
              onChange={(e) => {
                setActiveFilter(e.target.value as "all" | "active" | "archived");
                list.resetToFirstPage();
              }}
            >
              <option value="all">All</option>
              <option value="active">Active only</option>
              <option value="archived">Archived only</option>
            </select>
          </div>
        }
      >
        {list.loading && <LoadingSpinner message="Loading campuses..." />}
        {!list.loading && list.error && (
          <ErrorState
            title="Could not load campuses"
            message={`${list.error.message} (${list.error.code})`}
            onRetry={list.reload}
          />
        )}
        {!list.loading && !list.error && (
          <>
            <DataTable
              rows={list.items}
              rowKey={(c) => c.id}
              emptyTitle="No campuses configured"
              emptyDescription="Create a campus above or switch the status filter."
              columns={[
                { key: "name", header: "Campus Name", render: (c) => <strong>{c.name}</strong> },
                { key: "id", header: "Campus ID", render: (c) => <UuidChip value={c.id} /> },
                { key: "status", header: "Status", render: (c) => <ActiveBadge active={c.active} /> },
                ...(flags.isAdmin
                  ? [
                      {
                        key: "actions",
                        header: "Actions",
                        render: (c: CampusSummary) => (
                          <div className="viora-row">
                            <button
                              type="button"
                              className="viora-btn viora-btn-sm"
                              onClick={() => {
                                setEditing(c);
                                setEditName(c.name);
                              }}
                            >
                              Rename
                            </button>
                            <button
                              type="button"
                              className={`viora-btn viora-btn-sm ${c.active ? "viora-btn-danger" : ""}`}
                              onClick={() => setLifecycleTarget(c)}
                            >
                              {c.active ? "Archive" : "Activate"}
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

      <ModalDialog
        open={editing !== null}
        title="Rename Campus"
        onClose={() => setEditing(null)}
      >
        <form onSubmit={handleSaveEdit} className="viora-stack">
          <FormField label="Campus name" htmlFor="campus-edit-name">
            <input
              id="campus-edit-name"
              type="text"
              className="viora-input"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              disabled={savingEdit}
              required
            />
          </FormField>
          <div className="viora-row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="viora-btn" onClick={() => setEditing(null)}>
              Cancel
            </button>
            <button
              type="submit"
              className="viora-btn viora-btn-primary"
              disabled={savingEdit}
            >
              {savingEdit ? "Saving..." : "Save changes"}
            </button>
          </div>
        </form>
      </ModalDialog>

      <ConfirmDialog
        open={lifecycleTarget !== null}
        title={lifecycleTarget?.active ? "Archive campus?" : "Activate campus?"}
        message={
          lifecycleTarget?.active
            ? `Archiving "${lifecycleTarget.name}" prevents creating new departments under this campus while preserving existing records.`
            : `Reactivating "${lifecycleTarget?.name ?? ""}" restores active status.`
        }
        confirmLabel={lifecycleTarget?.active ? "Archive" : "Activate"}
        danger={Boolean(lifecycleTarget?.active)}
        busy={toggling}
        onCancel={() => setLifecycleTarget(null)}
        onConfirm={() => void handleConfirmLifecycle()}
      />
    </div>
  );
}
