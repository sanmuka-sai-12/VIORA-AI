import { useCallback, useState, type ReactNode } from "react";
import { useAuth } from "../../auth/AuthContext";
import { FormField, PageHeader, UuidChip } from "../../components/common/UiPrimitives";
import { deriveRoleFlags } from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { validateRequiredName } from "../../tenant/validation";
import { AcademicCrudSection, type ActiveFilterValue } from "../common/AcademicCrudSection";
import { useActionFeedback, type ApiResultLike } from "../common/useActionFeedback";
import { useCursorList, type CursorPageResponse } from "../common/useCursorList";
import { resolveLabelById, useTenantOptions } from "../common/useTenantOptions";

export interface DepartmentScopedEntity {
  id: string;
  name: string;
  department_id?: string | null;
  active: boolean;
}

interface DepartmentScopedCrudConfig<T extends DepartmentScopedEntity> {
  pageTitle: string;
  pageSubtitle: string;
  entityLabel: string;
  entityLower: string;
  directoryTitle: string;
  namePlaceholder: string;
  filterAriaLabel: string;
  emptyTitle: string;
  idColumnHeader: string;
  idPrefix: string;
  archiveMessage: (item: T) => string;
  listCall: (
    tenantId: string,
    query: { department_id?: string; active?: boolean; cursor?: string; limit: number },
  ) => Promise<ApiResultLike<CursorPageResponse<T>>>;
  createCall: (
    tenantId: string,
    body: { name: string; department_id: string | null },
  ) => Promise<ApiResultLike<T>>;
  updateCall: (
    tenantId: string,
    id: string,
    body: { name: string; department_id: string | null },
  ) => Promise<ApiResultLike<T>>;
  lifecycleCall: (
    tenantId: string,
    id: string,
    body: { active: boolean },
  ) => Promise<ApiResultLike<T>>;
}

export function DepartmentScopedCrudPage<T extends DepartmentScopedEntity>({
  config,
}: {
  config: DepartmentScopedCrudConfig<T>;
}) {
  useAuth();
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
      config.listCall(tenantId!, {
        department_id: deptFilter || undefined,
        active: activeFilter === "all" ? undefined : activeFilter === "active",
        cursor,
        limit,
      }),
    [activeFilter, config, deptFilter, tenantId],
  );
  const list = useCursorList<T>(tenantId, fetcher);

  const handleCreate = async () => {
    if (!tenantId) return false;
    const err = validateRequiredName(createName, `${config.entityLabel} name`, 160);
    if (err) {
      feedback.setValidationError(err);
      return false;
    }
    return feedback.runApiAction({
      call: () => config.createCall(tenantId, { name: createName.trim(), department_id: createDeptId || null }),
      fallbackError: `Failed to create ${config.entityLower}.`,
      onSuccess: (data) => {
        setCreateName("");
        setCreateDeptId("");
        feedback.setActionSuccess(`${config.entityLabel} "${data.name}" created.`);
        list.resetToFirstPage();
        list.reload();
      },
    });
  };

  const handleSaveEdit = async (target: T) => {
    if (!tenantId) return false;
    const err = validateRequiredName(editName, `${config.entityLabel} name`, 160);
    if (err) {
      feedback.setValidationError(err);
      return false;
    }
    return feedback.runApiAction({
      call: () => config.updateCall(tenantId, target.id, { name: editName.trim(), department_id: editDeptId || null }),
      fallbackError: `Failed to update ${config.entityLower}.`,
      onSuccess: (data) => {
        feedback.setActionSuccess(`${config.entityLabel} "${data.name}" updated.`);
        list.reload();
      },
    });
  };

  const handleToggleLifecycle = async (target: T) => {
    if (!tenantId) return;
    await feedback.runApiAction({
      call: () => config.lifecycleCall(tenantId, target.id, { active: !target.active }),
      fallbackError: `Failed to update ${config.entityLower} status.`,
      onSuccess: (data) => {
        feedback.setActionSuccess(
          `${config.entityLabel} "${data.name}" ${data.active ? "activated" : "archived"}.`,
        );
        list.reload();
      },
    });
  };

  const renderFields = (
    prefix: string,
    nameVal: string,
    onName: (v: string) => void,
    deptVal: string,
    onDept: (v: string) => void,
    busy: boolean,
    isEdit: boolean,
  ): ReactNode => (
    <>
      <div style={isEdit ? undefined : { flex: 1, minWidth: "220px" }}>
        <FormField label={`${config.entityLabel} name`} htmlFor={`${prefix}-name`}>
          <input
            id={`${prefix}-name`}
            type="text"
            className="viora-input"
            value={nameVal}
            onChange={(e) => onName(e.target.value)}
            placeholder={isEdit ? undefined : config.namePlaceholder}
            disabled={busy}
            required
          />
        </FormField>
      </div>
      <div style={isEdit ? undefined : { flex: 1, minWidth: "200px" }}>
        <FormField label="Department (optional)" htmlFor={`${prefix}-dept`}>
          <select
            id={`${prefix}-dept`}
            className="viora-select"
            value={deptVal}
            onChange={(e) => onDept(e.target.value)}
            disabled={busy}
          >
            <option value="">No department</option>
            {(isEdit ? options.departments : options.departments.filter((d) => d.active)).map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
                {isEdit && !d.active ? " (Archived)" : ""}
              </option>
            ))}
          </select>
        </FormField>
      </div>
    </>
  );

  return (
    <div className="viora-stack">
      <PageHeader title={config.pageTitle} subtitle={config.pageSubtitle} />
      {feedback.alerts}
      <AcademicCrudSection
        entityLabel={config.entityLabel}
        list={list}
        activeFilter={activeFilter}
        onActiveFilterChange={setActiveFilter}
        canCreate={flags.isAdmin}
        createTitle={`Create ${config.entityLabel}`}
        createSubmitLabel={`Create ${config.entityLower}`}
        onCreate={handleCreate}
        renderCreateFields={(busy) =>
          renderFields(`${config.idPrefix}-create`, createName, setCreateName, createDeptId, setCreateDeptId, busy, false)
        }
        directoryTitle={config.directoryTitle}
        extraFilters={
          options.departments.length > 0 && (
            <select
              className="viora-select viora-select-inline"
              aria-label={config.filterAriaLabel}
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
        loadingMessage={`Loading ${config.entityLower}s...`}
        errorTitle={`Could not load ${config.entityLower}s`}
        emptyTitle={config.emptyTitle}
        emptyDescription={`No ${config.entityLower}s match the selected filters.`}
        columns={[
          { key: "name", header: config.entityLabel, render: (item) => <strong>{item.name}</strong> },
          {
            key: "department",
            header: "Department",
            render: (item) =>
              resolveLabelById(options.departments, item.department_id) ??
              (item.department_id ? <UuidChip value={item.department_id} /> : "Unassigned"),
          },
          { key: "id", header: config.idColumnHeader, render: (item) => <UuidChip value={item.id} /> },
        ]}
        canEditRow={() => flags.isAdmin}
        canToggleRow={() => flags.isAdmin}
        editModalTitle={`Edit ${config.entityLabel}`}
        onOpenEdit={(item) => {
          setEditName(item.name);
          setEditDeptId(item.department_id ?? "");
        }}
        renderEditFields={(_, busy) =>
          renderFields(`${config.idPrefix}-edit`, editName, setEditName, editDeptId, setEditDeptId, busy, true)
        }
        onSaveEdit={handleSaveEdit}
        archiveMessage={config.archiveMessage}
        onToggleLifecycle={handleToggleLifecycle}
      />
    </div>
  );
}
