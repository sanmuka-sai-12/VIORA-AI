import { useCallback, useState } from "react";
import type { SubjectOfferingSummary } from "../../api/client";
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

export function OfferingsPage() {
  const { apiClient } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);
  const feedback = useActionFeedback();
  const options = useTenantOptions({ sections: true, subjects: true, members: true });

  const [sectionFilter, setSectionFilter] = useState("");
  const [subjectFilter, setSubjectFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState<ActiveFilterValue>("all");
  const [createName, setCreateName] = useState("");
  const [createSectionId, setCreateSectionId] = useState("");
  const [createSubjectId, setCreateSubjectId] = useState("");
  const [editName, setEditName] = useState("");
  const [editSectionId, setEditSectionId] = useState("");
  const [editSubjectId, setEditSubjectId] = useState("");
  const [assignmentMode, setAssignmentMode] = useState<{
    kind: "teachers" | "enrolments";
    offering: SubjectOfferingSummary;
  } | null>(null);

  const fetcher = useCallback(
    ({ cursor, limit }: { cursor?: string; limit: number }) =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/offerings", {
        params: {
          path: { tenant_id: tenantId! },
          query: {
            section_id: sectionFilter || undefined,
            subject_id: subjectFilter || undefined,
            active: activeFilter === "all" ? undefined : activeFilter === "active",
            cursor,
            limit,
          },
        },
      }),
    [activeFilter, apiClient, sectionFilter, subjectFilter, tenantId],
  );
  const list = useCursorList<SubjectOfferingSummary>(tenantId, fetcher);

  const canViewTeachers = flags.isAdmin || flags.isSubjectTeacher || flags.isClassCoordinator;
  const canViewEnrolments = flags.isAdmin || flags.isSubjectTeacher;

  const handleCreate = async () => {
    if (!tenantId) return false;
    const nameErr = validateRequiredName(createName, "Offering name", 160);
    const secErr = validateRequiredUuid(createSectionId, "Class section");
    const subErr = validateRequiredUuid(createSubjectId, "Subject");
    if (nameErr || secErr || subErr) {
      feedback.setValidationError(nameErr ?? secErr ?? subErr ?? "Invalid offering inputs.");
      return false;
    }
    return feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/offerings", {
          params: { path: { tenant_id: tenantId } },
          body: {
            name: createName.trim(),
            section_id: createSectionId,
            subject_id: createSubjectId,
          },
        }),
      fallbackError: "Failed to create offering.",
      onSuccess: (data) => {
        setCreateName("");
        feedback.setActionSuccess(`Subject offering "${data.name}" created.`);
        list.resetToFirstPage();
        list.reload();
      },
    });
  };

  const handleSaveEdit = async (target: SubjectOfferingSummary) => {
    if (!tenantId) return false;
    const nameErr = validateRequiredName(editName, "Offering name", 160);
    if (nameErr) {
      feedback.setValidationError(nameErr);
      return false;
    }
    return feedback.runApiAction({
      call: () =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/offerings/{offering_id}", {
          params: { path: { tenant_id: tenantId, offering_id: target.id } },
          body: {
            name: editName.trim(),
            section_id: editSectionId || null,
            subject_id: editSubjectId || null,
          },
        }),
      fallbackError: "Failed to update offering.",
      onSuccess: (data) => {
        feedback.setActionSuccess(`Offering "${data.name}" updated.`);
        list.reload();
      },
    });
  };

  const handleToggleLifecycle = async (target: SubjectOfferingSummary) => {
    if (!tenantId) return;
    await feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, offering_id: target.id } },
          body: { active: !target.active },
        }),
      fallbackError: "Failed to update offering status.",
      onSuccess: (data) => {
        feedback.setActionSuccess(`Offering "${data.name}" ${data.active ? "activated" : "archived"}.`);
        list.reload();
      },
    });
  };

  return (
    <div className="viora-stack">
      <PageHeader
        title="Subject Offerings, Teachers & Enrolments"
        subtitle={
          flags.isAdmin
            ? "Publish section-subject offerings, assign instructors, and enrol students."
            : flags.isStudent
              ? "Active subject offerings in which you are currently enrolled."
              : "Subject offerings authorized within your teaching or coordination scope."
        }
      />
      {feedback.alerts}

      <AcademicCrudSection
        entityLabel="Offering"
        list={list}
        activeFilter={activeFilter}
        onActiveFilterChange={setActiveFilter}
        showStatusFilter={flags.isAdmin}
        statusFilterAriaLabel="Filter offerings by status"
        canCreate={flags.isAdmin}
        createTitle="Create Subject Offering"
        createSubmitLabel="Create offering"
        createFormClassName="viora-grid-3"
        onCreate={handleCreate}
        renderCreateFields={(busy) => (
          <>
            <FormField label="Offering title" htmlFor="off-create-name">
              <input
                id="off-create-name"
                type="text"
                className="viora-input"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                placeholder="Algorithms (Fall CSE-A)"
                disabled={busy}
                required
              />
            </FormField>
            <FormField label="Class section (required)" htmlFor="off-create-sec">
              <select
                id="off-create-sec"
                className="viora-select"
                value={createSectionId}
                onChange={(e) => setCreateSectionId(e.target.value)}
                disabled={busy}
                required
              >
                <option value="">Select active section...</option>
                {options.sections
                  .filter((s) => s.active)
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
              </select>
            </FormField>
            <FormField label="Subject (required)" htmlFor="off-create-sub">
              <select
                id="off-create-sub"
                className="viora-select"
                value={createSubjectId}
                onChange={(e) => setCreateSubjectId(e.target.value)}
                disabled={busy}
                required
              >
                <option value="">Select active subject...</option>
                {options.subjects
                  .filter((s) => s.active)
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
              </select>
            </FormField>
          </>
        )}
        directoryTitle="Offerings"
        extraFilters={
          <>
            {options.sections.length > 0 && (
              <select
                className="viora-select viora-select-inline"
                aria-label="Filter offerings by section"
                value={sectionFilter}
                onChange={(e) => {
                  setSectionFilter(e.target.value);
                  list.resetToFirstPage();
                }}
              >
                <option value="">All sections</option>
                {options.sections.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            )}
            {options.subjects.length > 0 && (
              <select
                className="viora-select viora-select-inline"
                aria-label="Filter offerings by subject"
                value={subjectFilter}
                onChange={(e) => {
                  setSubjectFilter(e.target.value);
                  list.resetToFirstPage();
                }}
              >
                <option value="">All subjects</option>
                {options.subjects.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            )}
          </>
        }
        loadingMessage="Loading subject offerings..."
        errorTitle="Could not load subject offerings"
        emptyTitle="No offerings found"
        emptyDescription="No subject offerings are available in your current scope."
        columns={[
          { key: "name", header: "Offering", render: (o) => <strong>{o.name}</strong> },
          {
            key: "section",
            header: "Section",
            render: (o) =>
              resolveLabelById(options.sections, o.section_id) ?? <UuidChip value={o.section_id} />,
          },
          {
            key: "subject",
            header: "Subject",
            render: (o) =>
              resolveLabelById(options.subjects, o.subject_id) ?? <UuidChip value={o.subject_id} />,
          },
        ]}
        extraRowActions={
          canViewTeachers || canViewEnrolments
            ? (o) => (
              <>
                {canViewTeachers && (
                  <button
                    type="button"
                    className="viora-btn viora-btn-sm"
                    onClick={() => setAssignmentMode({ kind: "teachers", offering: o })}
                  >
                    Teachers
                  </button>
                )}
                {canViewEnrolments && (
                  <button
                    type="button"
                    className="viora-btn viora-btn-sm"
                    onClick={() => setAssignmentMode({ kind: "enrolments", offering: o })}
                  >
                    Enrolments
                  </button>
                )}
              </>
            )
            : undefined
        }
        canEditRow={() => flags.isAdmin}
        canToggleRow={() => flags.isAdmin}
        editModalTitle="Edit Offering"
        onOpenEdit={(o) => {
          setEditName(o.name);
          setEditSectionId(o.section_id);
          setEditSubjectId(o.subject_id);
        }}
        renderEditFields={(_, busy) => (
          <>
            <FormField label="Offering title" htmlFor="off-edit-name">
              <input
                id="off-edit-name"
                type="text"
                className="viora-input"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                disabled={busy}
                required
              />
            </FormField>
            <FormField label="Class section" htmlFor="off-edit-sec">
              <select
                id="off-edit-sec"
                className="viora-select"
                value={editSectionId}
                onChange={(e) => setEditSectionId(e.target.value)}
                disabled={busy}
              >
                {options.sections.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                    {s.active ? "" : " (Archived)"}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField label="Subject" htmlFor="off-edit-sub">
              <select
                id="off-edit-sub"
                className="viora-select"
                value={editSubjectId}
                onChange={(e) => setEditSubjectId(e.target.value)}
                disabled={busy}
              >
                {options.subjects.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                    {s.active ? "" : " (Archived)"}
                  </option>
                ))}
              </select>
            </FormField>
          </>
        )}
        onSaveEdit={handleSaveEdit}
        archiveMessage={(o) => `Change active status for offering "${o.name}"?`}
        onToggleLifecycle={handleToggleLifecycle}
      />

      {tenantId && assignmentMode && (
        <AssignmentModal
          open
          title={`${assignmentMode.kind === "teachers" ? "Teachers" : "Student Enrolments"} — ${assignmentMode.offering.name}`}
          subtitle={
            assignmentMode.kind === "teachers"
              ? "Assigned teachers must hold active membership with the subject_teacher role."
              : "Enrolled students must hold active membership with the student role."
          }
          canManage={flags.isAdmin}
          eligibleRoleFilter={assignmentMode.kind === "teachers" ? "subject_teacher" : "student"}
          assignActionLabel={assignmentMode.kind === "teachers" ? "Assign teacher" : "Enrol student"}
          revokeActionLabel={assignmentMode.kind === "teachers" ? "Revoke teacher" : "Withdraw"}
          members={options.members}
          onClose={() => setAssignmentMode(null)}
          listFn={() =>
            assignmentMode.kind === "teachers"
              ? apiClient.GET("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/teachers", {
                params: {
                  path: { tenant_id: tenantId, offering_id: assignmentMode.offering.id },
                  query: { limit: 100 },
                },
              })
              : apiClient.GET("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/enrolments", {
                params: {
                  path: { tenant_id: tenantId, offering_id: assignmentMode.offering.id },
                  query: { limit: 100 },
                },
              })
          }
          assignFn={(userId) =>
            assignmentMode.kind === "teachers"
              ? apiClient.POST("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/teachers", {
                params: { path: { tenant_id: tenantId, offering_id: assignmentMode.offering.id } },
                body: { user_id: userId },
              })
              : apiClient.POST("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/enrolments", {
                params: { path: { tenant_id: tenantId, offering_id: assignmentMode.offering.id } },
                body: { user_id: userId },
              })
          }
          revokeFn={(userId) =>
            assignmentMode.kind === "teachers"
              ? apiClient.POST(
                "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/teachers/{user_id}/revoke",
                {
                  params: {
                    path: {
                      tenant_id: tenantId,
                      offering_id: assignmentMode.offering.id,
                      user_id: userId,
                    },
                  },
                },
              )
              : apiClient.POST(
                "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/enrolments/{user_id}/withdraw",
                {
                  params: {
                    path: {
                      tenant_id: tenantId,
                      offering_id: assignmentMode.offering.id,
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
