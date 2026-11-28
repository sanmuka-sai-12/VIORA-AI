import { useCallback, useState } from "react";
import type { DepartmentSummary } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { FormField, PageHeader, UuidChip } from "../../components/common/UiPrimitives";
import { deriveRoleFlags } from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { validateRequiredName } from "../../tenant/validation";
import { AcademicCrudSection, type ActiveFilterValue } from "../common/AcademicCrudSection";
import { AssignmentModal } from "../common/AssignmentModal";
import { useActionFeedback } from "../common/useActionFeedback";
import { useCursorList } from "../common/useCursorList";
import { resolveLabelById, useTenantOptions } from "../common/useTenantOptions";

export function DepartmentsPage({ embedded = false }: { embedded?: boolean }) {
  const { apiClient } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);
  const feedback = useActionFeedback();
  const options = useTenantOptions({ campuses: true, members: true });

  const [campusFilter, setCampusFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState<ActiveFilterValue>("all");
  const [createName, setCreateName] = useState("");
  const [createCampusId, setCreateCampusId] = useState("");
  const [editName, setEditName] = useState("");
  const [editCampusId, setEditCampusId] = useState("");
  const [headsDept, setHeadsDept] = useState<DepartmentSummary | null>(null);

  const fetcher = useCallback(
    ({ cursor, limit }: { cursor?: string; limit: number }) =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/departments", {
        params: {
          path: { tenant_id: tenantId! },
          query: {
            campus_id: campusFilter || undefined,
            active: activeFilter === "all" ? undefined : activeFilter === "active",
            cursor,
            limit,
          },
        },
      }),
    [activeFilter, apiClient, campusFilter, tenantId],
  );
  const list = useCursorList<DepartmentSummary>(tenantId, fetcher);

  const handleCreate = async () => {
    if (!tenantId) return false;
    const err = validateRequiredName(createName, "Department name", 160);
    if (err) {
      feedback.setValidationError(err);
      return false;
    }
    return feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/departments", {
          params: { path: { tenant_id: tenantId } },
          body: { name: createName.trim(), campus_id: createCampusId || null },
        }),
      fallbackError: "Failed to create department.",
      onSuccess: (data) => {
        setCreateName("");
        setCreateCampusId("");
        feedback.setActionSuccess(`Department "${data.name}" created.`);
        list.resetToFirstPage();
        list.reload();
      },
    });
  };

  const handleSaveEdit = async (target: DepartmentSummary) => {
    if (!tenantId) return false;
    const err = validateRequiredName(editName, "Department name", 160);
    if (err) {
      feedback.setValidationError(err);
      return false;
    }
    return feedback.runApiAction({
      call: () =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/departments/{department_id}", {
          params: { path: { tenant_id: tenantId, department_id: target.id } },
          body: flags.isAdmin
            ? { name: editName.trim(), campus_id: editCampusId || null }
            : { name: editName.trim() },
        }),
      fallbackError: "Failed to update department.",
      onSuccess: (data) => {
        feedback.setActionSuccess(`Department "${data.name}" updated.`);
        list.reload();
      },
    });
  };

  const handleToggleLifecycle = async (target: DepartmentSummary) => {
    if (!tenantId) return;
    await feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/departments/{department_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, department_id: target.id } },
          body: { active: !target.active },
        }),
      fallbackError: "Failed to update department status.",
      onSuccess: (data) => {
        feedback.setActionSuccess(
          `Department "${data.name}" ${data.active ? "activated" : "archived"}.`,
        );
        list.reload();
      },
    });
  };

  return (
    <div className="viora-stack">
      {!embedded && (
        <PageHeader
          title="Departments & Department Heads"
          subtitle={
            flags.isAdmin
              ? "Create academic departments and assign department heads."
              : "Departments within your administrative scope."
          }
        />
      )}
      {feedback.alerts}
      <AcademicCrudSection
        entityLabel="Department"
        list={list}
        activeFilter={activeFilter}
        onActiveFilterChange={setActiveFilter}
        canCreate={flags.isAdmin}
        createTitle="Create Department"
        createSubmitLabel="Create department"
        onCreate={handleCreate}
        renderCreateFields={(busy) => (
          <>
            <div style={{ flex: 1, minWidth: "220px" }}>
              <FormField label="Department name" htmlFor="dept-create-name">
                <input
                  id="dept-create-name"
                  type="text"
                  className="viora-input"
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  placeholder="Computer Science"
                  disabled={busy}
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
                  disabled={busy}
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
          </>
        )}
        directoryTitle="Departments"
        extraFilters={
          flags.isAdmin &&
          options.campuses.length > 0 && (
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
          )
        }
        loadingMessage="Loading departments..."
        errorTitle="Could not load departments"
        emptyTitle="No departments available"
        emptyDescription="No departments match your scope or current filter."
        columns={[
          { key: "name", header: "Department", render: (d) => <strong>{d.name}</strong> },
          {
            key: "campus",
            header: "Campus",
            render: (d) =>
              resolveLabelById(options.campuses, d.campus_id) ??
              (d.campus_id ? <UuidChip value={d.campus_id} /> : "Institution-wide"),
          },
        ]}
        extraRowActions={(d) => (
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            onClick={() => setHeadsDept(d)}
          >
            Heads
          </button>
        )}
        canEditRow={() => flags.isAdmin || flags.isDepartmentHead}
        canToggleRow={() => flags.isAdmin}
        editModalTitle="Edit Department"
        onOpenEdit={(d) => {
          setEditName(d.name);
          setEditCampusId(d.campus_id ?? "");
        }}
        renderEditFields={(_, busy) => (
          <>
            <FormField label="Department name" htmlFor="dept-edit-name">
              <input
                id="dept-edit-name"
                type="text"
                className="viora-input"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                disabled={busy}
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
                  disabled={busy}
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
          </>
        )}
        onSaveEdit={handleSaveEdit}
        archiveMessage={(d) =>
          `Archiving "${d.name}" blocks new programme, subject, and head assignments under it.`
        }
        onToggleLifecycle={handleToggleLifecycle}
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
                  path: { tenant_id: tenantId, department_id: headsDept.id, user_id: userId },
                },
              },
            )
          }
        />
      )}
    </div>
  );
}
