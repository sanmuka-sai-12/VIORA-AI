import { useCallback, useState } from "react";
import type { CampusSummary } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { FormField, PageHeader, UuidChip } from "../../components/common/UiPrimitives";
import { deriveRoleFlags } from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { validateRequiredName } from "../../tenant/validation";
import { AcademicCrudSection, type ActiveFilterValue } from "../common/AcademicCrudSection";
import { useActionFeedback } from "../common/useActionFeedback";
import { useCursorList } from "../common/useCursorList";

export function CampusesPage() {
  const { apiClient } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);
  const feedback = useActionFeedback();

  const [activeFilter, setActiveFilter] = useState<ActiveFilterValue>("all");
  const [createName, setCreateName] = useState("");
  const [editName, setEditName] = useState("");

  const fetcher = useCallback(
    ({ cursor, limit }: { cursor?: string; limit: number }) =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/campuses", {
        params: {
          path: { tenant_id: tenantId! },
          query: {
            active: activeFilter === "all" ? undefined : activeFilter === "active",
            cursor,
            limit,
          },
        },
      }),
    [activeFilter, apiClient, tenantId],
  );
  const list = useCursorList<CampusSummary>(tenantId, fetcher);

  const handleCreate = async () => {
    if (!tenantId) return false;
    const err = validateRequiredName(createName, "Campus name", 160);
    if (err) {
      feedback.setValidationError(err);
      return false;
    }
    return feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/campuses", {
          params: { path: { tenant_id: tenantId } },
          body: { name: createName.trim() },
        }),
      fallbackError: "Failed to create campus.",
      onSuccess: (data) => {
        setCreateName("");
        feedback.setActionSuccess(`Campus "${data.name}" created.`);
        list.resetToFirstPage();
        list.reload();
      },
    });
  };

  const handleSaveEdit = async (target: CampusSummary) => {
    if (!tenantId) return false;
    const err = validateRequiredName(editName, "Campus name", 160);
    if (err) {
      feedback.setValidationError(err);
      return false;
    }
    return feedback.runApiAction({
      call: () =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/campuses/{campus_id}", {
          params: { path: { tenant_id: tenantId, campus_id: target.id } },
          body: { name: editName.trim() },
        }),
      fallbackError: "Failed to update campus.",
      onSuccess: (data) => {
        feedback.setActionSuccess(`Campus updated to "${data.name}".`);
        list.reload();
      },
    });
  };

  const handleToggleLifecycle = async (target: CampusSummary) => {
    if (!tenantId) return;
    await feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/campuses/{campus_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, campus_id: target.id } },
          body: { active: !target.active },
        }),
      fallbackError: "Failed to update campus status.",
      onSuccess: (data) => {
        feedback.setActionSuccess(`Campus "${data.name}" ${data.active ? "activated" : "archived"}.`);
        list.reload();
      },
    });
  };

  return (
    <div className="viora-stack">
      <PageHeader
        title="Campuses"
        subtitle="Manage physical and organizational campuses for this institution."
      />
      {feedback.alerts}
      <AcademicCrudSection
        entityLabel="Campus"
        list={list}
        activeFilter={activeFilter}
        onActiveFilterChange={setActiveFilter}
        canCreate={flags.isAdmin}
        createTitle="Add Campus"
        createSubmitLabel="Create campus"
        onCreate={handleCreate}
        renderCreateFields={(busy) => (
          <div style={{ flex: 1, minWidth: "240px", maxWidth: "420px" }}>
            <FormField label="Campus name" htmlFor="campus-create-name">
              <input
                id="campus-create-name"
                type="text"
                className="viora-input"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                placeholder="North Campus"
                disabled={busy}
                required
              />
            </FormField>
          </div>
        )}
        directoryTitle="Campus Directory"
        loadingMessage="Loading campuses..."
        errorTitle="Could not load campuses"
        emptyTitle="No campuses configured"
        emptyDescription="Create a campus above or switch the status filter."
        columns={[
          { key: "name", header: "Campus Name", render: (c) => <strong>{c.name}</strong> },
          { key: "id", header: "Campus ID", render: (c) => <UuidChip value={c.id} /> },
        ]}
        canEditRow={() => flags.isAdmin}
        canToggleRow={() => flags.isAdmin}
        editButtonLabel="Rename"
        editModalTitle="Rename Campus"
        onOpenEdit={(c) => setEditName(c.name)}
        renderEditFields={(_, busy) => (
          <FormField label="Campus name" htmlFor="campus-edit-name">
            <input
              id="campus-edit-name"
              type="text"
              className="viora-input"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              disabled={busy}
              required
            />
          </FormField>
        )}
        onSaveEdit={handleSaveEdit}
        archiveMessage={(c) =>
          `Archiving "${c.name}" prevents creating new departments under this campus while preserving existing records.`
        }
        onToggleLifecycle={handleToggleLifecycle}
      />
    </div>
  );
}
