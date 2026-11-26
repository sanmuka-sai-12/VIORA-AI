import { useCallback, useState } from "react";
import type { ProgrammeSummary } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { FormField, PageHeader, UuidChip } from "../../components/common/UiPrimitives";
import { deriveRoleFlags } from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { validateRequiredName } from "../../tenant/validation";
import { AcademicCrudSection, type ActiveFilterValue } from "../common/AcademicCrudSection";
import { useActionFeedback } from "../common/useActionFeedback";
import { useCursorList } from "../common/useCursorList";
import { resolveLabelById, useTenantOptions } from "../common/useTenantOptions";

export function ProgrammesPage() {
  const { apiClient } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);
  const feedback = useActionFeedback();
  const options = useTenantOptions({ departments: true });

  const [deptFilter, setDeptFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState<ActiveFilterValue>("all");
  const [createName, setCreateName] = useState("");
  const [createDeptId, setCreateDeptId] = useState("");
  const [editName, setEditName] = useState("");
  const [editDeptId, setEditDeptId] = useState("");

  const fetcher = useCallback(
    ({ cursor, limit }: { cursor?: string; limit: number }) =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/programmes", {
        params: {
          path: { tenant_id: tenantId! },
          query: {
            department_id: deptFilter || undefined,
            active: activeFilter === "all" ? undefined : activeFilter === "active",
            cursor,
            limit,
          },
        },
      }),
    [activeFilter, apiClient, deptFilter, tenantId],
  );
  const list = useCursorList<ProgrammeSummary>(tenantId, fetcher);

  const handleCreate = async () => {
    if (!tenantId) return false;
    const err = validateRequiredName(createName, "Programme name", 160);
    if (err) {
      feedback.setValidationError(err);
      return false;
    }
    return feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/programmes", {
          params: { path: { tenant_id: tenantId } },
          body: { name: createName.trim(), department_id: createDeptId || null },
        }),
      fallbackError: "Failed to create programme.",
      onSuccess: (data) => {
        setCreateName("");
        setCreateDeptId("");
        feedback.setActionSuccess(`Programme "${data.name}" created.`);
        list.resetToFirstPage();
        list.reload();
      },
    });
  };

  const handleSaveEdit = async (target: ProgrammeSummary) => {
    if (!tenantId) return false;
    const err = validateRequiredName(editName, "Programme name", 160);
    if (err) {
      feedback.setValidationError(err);
      return false;
    }
    return feedback.runApiAction({
      call: () =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/programmes/{programme_id}", {
          params: { path: { tenant_id: tenantId, programme_id: target.id } },
          body: { name: editName.trim(), department_id: editDeptId || null },
        }),
      fallbackError: "Failed to update programme.",
      onSuccess: (data) => {
        feedback.setActionSuccess(`Programme "${data.name}" updated.`);
        list.reload();
      },
    });
  };

  const handleToggleLifecycle = async (target: ProgrammeSummary) => {
    if (!tenantId) return;
    await feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/programmes/{programme_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, programme_id: target.id } },
          body: { active: !target.active },
        }),
      fallbackError: "Failed to update programme status.",
      onSuccess: (data) => {
        feedback.setActionSuccess(
          `Programme "${data.name}" ${data.active ? "activated" : "archived"}.`,
        );
        list.reload();
      },
    });
  };

  return (
    <div className="viora-stack">
      <PageHeader
        title="Programmes"
        subtitle="Manage degree or curriculum programmes optionally scoped to a department."
      />
      {feedback.alerts}
      <AcademicCrudSection
        entityLabel="Programme"
        list={list}
        activeFilter={activeFilter}
        onActiveFilterChange={setActiveFilter}
        canCreate={flags.isAdmin}
        createTitle="Create Programme"
        createSubmitLabel="Create programme"
        onCreate={handleCreate}
        renderCreateFields={(busy) => (
          <>
            <div style={{ flex: 1, minWidth: "220px" }}>
              <FormField label="Programme name" htmlFor="prog-create-name">
                <input
                  id="prog-create-name"
                  type="text"
                  className="viora-input"
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  placeholder="B.Tech Computer Science"
                  disabled={busy}
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
                  disabled={busy}
                >
                  <option value="">No department</option>
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
          </>
        )}
        directoryTitle="Programme List"
        extraFilters={
          options.departments.length > 0 && (
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
          )
        }
        loadingMessage="Loading programmes..."
        errorTitle="Could not load programmes"
        emptyTitle="No programmes found"
        emptyDescription="No programmes match the selected filters."
        columns={[
          { key: "name", header: "Programme", render: (p) => <strong>{p.name}</strong> },
          {
            key: "department",
            header: "Department",
            render: (p) =>
              resolveLabelById(options.departments, p.department_id) ??
              (p.department_id ? <UuidChip value={p.department_id} /> : "Unassigned"),
          },
          { key: "id", header: "Programme ID", render: (p) => <UuidChip value={p.id} /> },
        ]}
        canEditRow={() => flags.isAdmin}
        canToggleRow={() => flags.isAdmin}
        editModalTitle="Edit Programme"
        onOpenEdit={(p) => {
          setEditName(p.name);
          setEditDeptId(p.department_id ?? "");
        }}
        renderEditFields={(_, busy) => (
          <>
            <FormField label="Programme name" htmlFor="prog-edit-name">
              <input
                id="prog-edit-name"
                type="text"
                className="viora-input"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                disabled={busy}
                required
              />
            </FormField>
            <FormField label="Department (optional)" htmlFor="prog-edit-dept">
              <select
                id="prog-edit-dept"
                className="viora-select"
                value={editDeptId}
                onChange={(e) => setEditDeptId(e.target.value)}
                disabled={busy}
              >
                <option value="">No department</option>
                {options.departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                    {d.active ? "" : " (Archived)"}
                  </option>
                ))}
              </select>
            </FormField>
          </>
        )}
        onSaveEdit={handleSaveEdit}
        archiveMessage={(p) =>
          `Change lifecycle status for "${p.name}"? Archiving prevents linking new sections to it.`
        }
        onToggleLifecycle={handleToggleLifecycle}
      />
    </div>
  );
}
