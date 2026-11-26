import { useCallback, useState } from "react";
import type { SectionSummary } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { FormField, PageHeader, UuidChip } from "../../components/common/UiPrimitives";
import { deriveRoleFlags } from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { validateRequiredName, validateRequiredUuid } from "../../tenant/validation";
import { AcademicCrudSection, type ActiveFilterValue } from "../common/AcademicCrudSection";
import { AssignmentModal } from "../common/AssignmentModal";
import { useActionFeedback } from "../common/useActionFeedback";
import { useCursorList } from "../common/useCursorList";
import { resolveLabelById, useTenantOptions } from "../common/useTenantOptions";

export function SectionsPage() {
  const { apiClient } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);
  const feedback = useActionFeedback();
  const options = useTenantOptions({ semesters: true, programmes: true, members: true });

  const [semesterFilter, setSemesterFilter] = useState("");
  const [programmeFilter, setProgrammeFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState<ActiveFilterValue>("all");
  const [createName, setCreateName] = useState("");
  const [createSemesterId, setCreateSemesterId] = useState("");
  const [createProgrammeId, setCreateProgrammeId] = useState("");
  const [editName, setEditName] = useState("");
  const [editSemesterId, setEditSemesterId] = useState("");
  const [editProgrammeId, setEditProgrammeId] = useState("");
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

  const handleCreate = async () => {
    if (!tenantId) return false;
    const nameErr = validateRequiredName(createName, "Section name", 160);
    const semErr = validateRequiredUuid(createSemesterId, "Semester");
    if (nameErr || semErr) {
      feedback.setValidationError(nameErr ?? semErr ?? "Invalid section inputs.");
      return false;
    }
    return feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/sections", {
          params: { path: { tenant_id: tenantId } },
          body: {
            name: createName.trim(),
            semester_id: createSemesterId,
            programme_id: createProgrammeId || null,
          },
        }),
      fallbackError: "Failed to create section.",
      onSuccess: (data) => {
        setCreateName("");
        setCreateProgrammeId("");
        feedback.setActionSuccess(`Section "${data.name}" created.`);
        list.resetToFirstPage();
        list.reload();
      },
    });
  };

  const handleSaveEdit = async (target: SectionSummary) => {
    if (!tenantId) return false;
    const nameErr = validateRequiredName(editName, "Section name", 160);
    if (nameErr) {
      feedback.setValidationError(nameErr);
      return false;
    }
    return feedback.runApiAction({
      call: () =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/sections/{section_id}", {
          params: { path: { tenant_id: tenantId, section_id: target.id } },
          body: flags.isAdmin
            ? {
              name: editName.trim(),
              semester_id: editSemesterId || null,
              programme_id: editProgrammeId || null,
            }
            : { name: editName.trim() },
        }),
      fallbackError: "Failed to update section.",
      onSuccess: (data) => {
        feedback.setActionSuccess(`Section "${data.name}" updated.`);
        list.reload();
      },
    });
  };

  const handleToggleLifecycle = async (target: SectionSummary) => {
    if (!tenantId) return;
    await feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/sections/{section_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, section_id: target.id } },
          body: { active: !target.active },
        }),
      fallbackError: "Failed to update section status.",
      onSuccess: (data) => {
        feedback.setActionSuccess(`Section "${data.name}" ${data.active ? "activated" : "archived"}.`);
        list.reload();
      },
    });
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
      {feedback.alerts}
      <AcademicCrudSection
        entityLabel="Section"
        list={list}
        activeFilter={activeFilter}
        onActiveFilterChange={setActiveFilter}
        canCreate={flags.isAdmin}
        createTitle="Create Class Section"
        createSubmitLabel="Create section"
        createFormClassName="viora-grid-3"
        onCreate={handleCreate}
        renderCreateFields={(busy) => (
          <>
            <FormField label="Section name" htmlFor="sec-create-name">
              <input
                id="sec-create-name"
                type="text"
                className="viora-input"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                placeholder="CSE-A"
                disabled={busy}
                required
              />
            </FormField>
            <FormField label="Semester (required)" htmlFor="sec-create-sem">
              <select
                id="sec-create-sem"
                className="viora-select"
                value={createSemesterId}
                onChange={(e) => setCreateSemesterId(e.target.value)}
                disabled={busy}
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
                disabled={busy}
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
          </>
        )}
        directoryTitle="Sections"
        extraFilters={
          <>
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
          </>
        }
        loadingMessage="Loading class sections..."
        errorTitle="Could not load class sections"
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
            render: (s) =>
              resolveLabelById(options.programmes, s.programme_id) ??
              (s.programme_id ? <UuidChip value={s.programme_id} /> : "Unassigned"),
          },
        ]}
        extraRowActions={(s) => (
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            onClick={() => setCoordsSection(s)}
          >
            Coordinators
          </button>
        )}
        canEditRow={() => flags.isAdmin || flags.isClassCoordinator}
        canToggleRow={() => flags.isAdmin}
        editModalTitle="Edit Section"
        onOpenEdit={(s) => {
          setEditName(s.name);
          setEditSemesterId(s.semester_id);
          setEditProgrammeId(s.programme_id ?? "");
        }}
        renderEditFields={(_, busy) => (
          <>
            <FormField label="Section name" htmlFor="sec-edit-name">
              <input
                id="sec-edit-name"
                type="text"
                className="viora-input"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                disabled={busy}
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
                    disabled={busy}
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
                    disabled={busy}
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
          </>
        )}
        onSaveEdit={handleSaveEdit}
        archiveMessage={(s) =>
          `Update status for section "${s.name}"? Archiving blocks new offerings and coordinator assignments.`
        }
        onToggleLifecycle={handleToggleLifecycle}
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
                  path: { tenant_id: tenantId, section_id: coordsSection.id, user_id: userId },
                },
              },
            )
          }
        />
      )}
    </div>
  );
}
