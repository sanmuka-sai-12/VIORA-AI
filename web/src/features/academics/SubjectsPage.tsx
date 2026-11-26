import { useCallback, useState } from "react";
import type { SubjectSummary } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { FormField, PageHeader, UuidChip } from "../../components/common/UiPrimitives";
import { deriveRoleFlags } from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { validateRequiredName } from "../../tenant/validation";
import { AcademicCrudSection, type ActiveFilterValue } from "../common/AcademicCrudSection";
import { useActionFeedback } from "../common/useActionFeedback";
import { useCursorList } from "../common/useCursorList";
import { resolveLabelById, useTenantOptions } from "../common/useTenantOptions";

export function SubjectsPage() {
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
      apiClient.GET("/api/v1/tenants/{tenant_id}/subjects", {
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
  const list = useCursorList<SubjectSummary>(tenantId, fetcher);

  const handleCreate = async () => {
    if (!tenantId) return false;
    const err = validateRequiredName(createName, "Subject name", 160);
    if (err) {
      feedback.setValidationError(err);
      return false;
    }
    return feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/subjects", {
          params: { path: { tenant_id: tenantId } },
          body: { name: createName.trim(), department_id: createDeptId || null },
        }),
      fallbackError: "Failed to create subject.",
      onSuccess: (data) => {
        setCreateName("");
        setCreateDeptId("");
        feedback.setActionSuccess(`Subject "${data.name}" created.`);
        list.resetToFirstPage();
        list.reload();
      },
    });
  };

  const handleSaveEdit = async (target: SubjectSummary) => {
    if (!tenantId) return false;
    const err = validateRequiredName(editName, "Subject name", 160);
    if (err) {
      feedback.setValidationError(err);
      return false;
    }
    return feedback.runApiAction({
      call: () =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/subjects/{subject_id}", {
          params: { path: { tenant_id: tenantId, subject_id: target.id } },
          body: { name: editName.trim(), department_id: editDeptId || null },
        }),
      fallbackError: "Failed to update subject.",
      onSuccess: (data) => {
        feedback.setActionSuccess(`Subject "${data.name}" updated.`);
        list.reload();
      },
    });
  };

  const handleToggleLifecycle = async (target: SubjectSummary) => {
    if (!tenantId) return;
    await feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/subjects/{subject_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, subject_id: target.id } },
          body: { active: !target.active },
        }),
      fallbackError: "Failed to update subject status.",
      onSuccess: (data) => {
        feedback.setActionSuccess(`Subject "${data.name}" ${data.active ? "activated" : "archived"}.`);
        list.reload();
      },
    });
  };

  return (
    <div className="viora-stack">
      <PageHeader
        title="Subjects"
        subtitle="Manage catalog subjects before creating section offerings and teaching assignments."
      />
      {feedback.alerts}
      <AcademicCrudSection
        entityLabel="Subject"
        list={list}
        activeFilter={activeFilter}
        onActiveFilterChange={setActiveFilter}
        canCreate={flags.isAdmin}
        createTitle="Create Subject"
        createSubmitLabel="Create subject"
        onCreate={handleCreate}
        renderCreateFields={(busy) => (
          <>
            <div style={{ flex: 1, minWidth: "220px" }}>
              <FormField label="Subject name" htmlFor="sub-create-name">
                <input
                  id="sub-create-name"
                  type="text"
                  className="viora-input"
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  placeholder="Data Structures & Algorithms"
                  disabled={busy}
                  required
                />
              </FormField>
            </div>
            <div style={{ flex: 1, minWidth: "200px" }}>
              <FormField label="Department (optional)" htmlFor="sub-create-dept">
                <select
                  id="sub-create-dept"
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
        directoryTitle="Subjects"
        extraFilters={
          options.departments.length > 0 && (
            <select
              className="viora-select viora-select-inline"
              aria-label="Filter subjects by department"
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
        loadingMessage="Loading subjects..."
        errorTitle="Could not load subjects"
        emptyTitle="No subjects configured"
        emptyDescription="No subjects match the selected filters."
        columns={[
          { key: "name", header: "Subject", render: (s) => <strong>{s.name}</strong> },
          {
            key: "department",
            header: "Department",
            render: (s) =>
              resolveLabelById(options.departments, s.department_id) ??
              (s.department_id ? <UuidChip value={s.department_id} /> : "Unassigned"),
          },
          { key: "id", header: "Subject ID", render: (s) => <UuidChip value={s.id} /> },
        ]}
        canEditRow={() => flags.isAdmin}
        canToggleRow={() => flags.isAdmin}
        editModalTitle="Edit Subject"
        onOpenEdit={(s) => {
          setEditName(s.name);
          setEditDeptId(s.department_id ?? "");
        }}
        renderEditFields={(_, busy) => (
          <>
            <FormField label="Subject name" htmlFor="sub-edit-name">
              <input
                id="sub-edit-name"
                type="text"
                className="viora-input"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                disabled={busy}
                required
              />
            </FormField>
            <FormField label="Department (optional)" htmlFor="sub-edit-dept">
              <select
                id="sub-edit-dept"
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
        archiveMessage={(s) =>
          `Change lifecycle status for "${s.name}"? Archiving prevents creating new subject offerings for this subject.`
        }
        onToggleLifecycle={handleToggleLifecycle}
      />
    </div>
  );
}
