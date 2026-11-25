import { useCallback, useState, type FormEvent } from "react";
import {
  normalizeApiError,
  type NormalizedApiError,
  type SubjectOfferingSummary,
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
import { validateRequiredName, validateRequiredUuid } from "../../tenant/validation";
import { AssignmentModal } from "../common/AssignmentModal";
import { useCursorList } from "../common/useCursorList";
import { resolveLabelById, useTenantOptions } from "../common/useTenantOptions";

export function OfferingsPage() {
  const { apiClient, controller: authController } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);

  const options = useTenantOptions({
    sections: true,
    subjects: true,
    members: true,
  });

  const [sectionFilter, setSectionFilter] = useState("");
  const [subjectFilter, setSubjectFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState<"all" | "active" | "archived">("all");
  const [actionError, setActionError] = useState<NormalizedApiError | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  const [createName, setCreateName] = useState("");
  const [createSectionId, setCreateSectionId] = useState("");
  const [createSubjectId, setCreateSubjectId] = useState("");
  const [creating, setCreating] = useState(false);

  const [editing, setEditing] = useState<SubjectOfferingSummary | null>(null);
  const [editName, setEditName] = useState("");
  const [editSectionId, setEditSectionId] = useState("");
  const [editSubjectId, setEditSubjectId] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);

  const [lifecycleTarget, setLifecycleTarget] =
    useState<SubjectOfferingSummary | null>(null);
  const [toggling, setToggling] = useState(false);

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

  const canViewTeachers =
    flags.isAdmin || flags.isSubjectTeacher || flags.isClassCoordinator;
  const canViewEnrolments = flags.isAdmin || flags.isSubjectTeacher;

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId) return;
    setActionError(null);
    setActionSuccess(null);

    const nameErr = validateRequiredName(createName, "Offering name", 160);
    const secErr = validateRequiredUuid(createSectionId, "Class section");
    const subErr = validateRequiredUuid(createSubjectId, "Subject");
    if (nameErr || secErr || subErr) {
      setActionError({
        status: 422,
        code: "validation_error",
        message: nameErr ?? secErr ?? subErr ?? "Invalid offering inputs.",
      });
      return;
    }

    setCreating(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/offerings", {
          params: { path: { tenant_id: tenantId } },
          body: {
            name: createName.trim(),
            section_id: createSectionId,
            subject_id: createSubjectId,
          },
        }),
      );
      if (res.data) {
        setCreateName("");
        setActionSuccess(`Subject offering "${res.data.name}" created.`);
        list.resetToFirstPage();
        list.reload();
      } else {
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to create offering."),
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

    const nameErr = validateRequiredName(editName, "Offering name", 160);
    if (nameErr) {
      setActionError({ status: 422, code: "validation_error", message: nameErr });
      return;
    }

    setSavingEdit(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/offerings/{offering_id}", {
          params: { path: { tenant_id: tenantId, offering_id: editing.id } },
          body: {
            name: editName.trim(),
            section_id: editSectionId || null,
            subject_id: editSubjectId || null,
          },
        }),
      );
      if (res.data) {
        setEditing(null);
        setActionSuccess(`Offering "${res.data.name}" updated.`);
        list.reload();
      } else {
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to update offering."),
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
    setToggling(true);
    setActionError(null);
    setActionSuccess(null);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, offering_id: lifecycleTarget.id } },
          body: { active: !lifecycleTarget.active },
        }),
      );
      if (res.data) {
        setActionSuccess(
          `Offering "${res.data.name}" ${res.data.active ? "activated" : "archived"}.`,
        );
        setLifecycleTarget(null);
        list.reload();
      } else {
        setLifecycleTarget(null);
        setActionError(
          normalizeApiError(res.error, res.response.status, "Failed to update offering status."),
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
        title="Subject Offerings, Teachers & Enrolments"
        subtitle={
          flags.isAdmin
            ? "Publish section-subject offerings, assign instructors, and enrol students."
            : flags.isStudent
              ? "Active subject offerings in which you are currently enrolled."
              : "Subject offerings authorized within your teaching or coordination scope."
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
        <SectionCard title="Create Subject Offering">
          <form onSubmit={handleCreate} className="viora-grid-3" style={{ alignItems: "end" }}>
            <FormField label="Offering title" htmlFor="off-create-name">
              <input
                id="off-create-name"
                type="text"
                className="viora-input"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                placeholder="Algorithms (Fall CSE-A)"
                disabled={creating}
                required
              />
            </FormField>

            <FormField label="Class section (required)" htmlFor="off-create-sec">
              <select
                id="off-create-sec"
                className="viora-select"
                value={createSectionId}
                onChange={(e) => setCreateSectionId(e.target.value)}
                disabled={creating}
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
                disabled={creating}
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

            <div>
              <button type="submit" className="viora-btn viora-btn-primary" disabled={creating}>
                {creating ? "Creating..." : "Create offering"}
              </button>
            </div>
          </form>
        </SectionCard>
      )}

      <SectionCard
        title="Offerings"
        actions={
          <div className="viora-row">
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
            {flags.isAdmin && (
              <select
                className="viora-select viora-select-inline"
                aria-label="Filter offerings by status"
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
            )}
          </div>
        }
      >
        {list.loading && <LoadingSpinner message="Loading subject offerings..." />}
        {!list.loading && list.error && (
          <ErrorState
            title="Could not load subject offerings"
            message={`${list.error.message} (${list.error.code})`}
            onRetry={list.reload}
          />
        )}
        {!list.loading && !list.error && (
          <>
            <DataTable
              rows={list.items}
              rowKey={(o) => o.id}
              emptyTitle="No offerings found"
              emptyDescription="No subject offerings are available in your current scope."
              columns={[
                { key: "name", header: "Offering", render: (o) => <strong>{o.name}</strong> },
                {
                  key: "section",
                  header: "Section",
                  render: (o) =>
                    resolveLabelById(options.sections, o.section_id) ?? (
                      <UuidChip value={o.section_id} />
                    ),
                },
                {
                  key: "subject",
                  header: "Subject",
                  render: (o) =>
                    resolveLabelById(options.subjects, o.subject_id) ?? (
                      <UuidChip value={o.subject_id} />
                    ),
                },
                { key: "status", header: "Status", render: (o) => <ActiveBadge active={o.active} /> },
                ...(canViewTeachers || canViewEnrolments || flags.isAdmin
                  ? [
                      {
                        key: "actions",
                        header: "Actions",
                        render: (o: SubjectOfferingSummary) => (
                          <div className="viora-row">
                            {canViewTeachers && (
                              <button
                                type="button"
                                className="viora-btn viora-btn-sm"
                                onClick={() =>
                                  setAssignmentMode({ kind: "teachers", offering: o })
                                }
                              >
                                Teachers
                              </button>
                            )}
                            {canViewEnrolments && (
                              <button
                                type="button"
                                className="viora-btn viora-btn-sm"
                                onClick={() =>
                                  setAssignmentMode({ kind: "enrolments", offering: o })
                                }
                              >
                                Enrolments
                              </button>
                            )}
                            {flags.isAdmin && (
                              <>
                                <button
                                  type="button"
                                  className="viora-btn viora-btn-sm"
                                  onClick={() => {
                                    setEditing(o);
                                    setEditName(o.name);
                                    setEditSectionId(o.section_id);
                                    setEditSubjectId(o.subject_id);
                                  }}
                                >
                                  Edit
                                </button>
                                <button
                                  type="button"
                                  className={`viora-btn viora-btn-sm ${o.active ? "viora-btn-danger" : ""}`}
                                  onClick={() => setLifecycleTarget(o)}
                                >
                                  {o.active ? "Archive" : "Activate"}
                                </button>
                              </>
                            )}
                          </div>
                        ),
                      },
                    ]
                  : [
                      {
                        key: "id",
                        header: "Offering ID",
                        render: (o: SubjectOfferingSummary) => <UuidChip value={o.id} />,
                      },
                    ]),
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

      <ModalDialog open={editing !== null} title="Edit Subject Offering" onClose={() => setEditing(null)}>
        <form onSubmit={handleSaveEdit} className="viora-stack">
          <FormField label="Offering title" htmlFor="off-edit-name">
            <input
              id="off-edit-name"
              type="text"
              className="viora-input"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              disabled={savingEdit}
              required
            />
          </FormField>
          <FormField label="Class section" htmlFor="off-edit-sec">
            <select
              id="off-edit-sec"
              className="viora-select"
              value={editSectionId}
              onChange={(e) => setEditSectionId(e.target.value)}
              disabled={savingEdit}
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
              disabled={savingEdit}
            >
              {options.subjects.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                  {s.active ? "" : " (Archived)"}
                </option>
              ))}
            </select>
          </FormField>
          <div className="viora-row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="viora-btn" onClick={() => setEditing(null)}>
              Cancel
            </button>
            <button type="submit" className="viora-btn viora-btn-primary" disabled={savingEdit}>
              Save changes
            </button>
          </div>
        </form>
      </ModalDialog>

      <ConfirmDialog
        open={lifecycleTarget !== null}
        title={lifecycleTarget?.active ? "Archive offering?" : "Activate offering?"}
        message={`Change active status for offering "${lifecycleTarget?.name ?? ""}"?`}
        confirmLabel={lifecycleTarget?.active ? "Archive" : "Activate"}
        danger={Boolean(lifecycleTarget?.active)}
        busy={toggling}
        onCancel={() => setLifecycleTarget(null)}
        onConfirm={() => void handleConfirmLifecycle()}
      />

      {tenantId && assignmentMode?.kind === "teachers" && (
        <AssignmentModal
          open
          title={`Teachers — ${assignmentMode.offering.name}`}
          subtitle="Assigned teachers must hold active membership with the subject_teacher role."
          canManage={flags.isAdmin}
          eligibleRoleFilter="subject_teacher"
          assignActionLabel="Assign teacher"
          revokeActionLabel="Revoke teacher"
          members={options.members}
          onClose={() => setAssignmentMode(null)}
          listFn={() =>
            apiClient.GET("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/teachers", {
              params: {
                path: { tenant_id: tenantId, offering_id: assignmentMode.offering.id },
                query: { limit: 100 },
              },
            })
          }
          assignFn={(userId) =>
            apiClient.POST("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/teachers", {
              params: { path: { tenant_id: tenantId, offering_id: assignmentMode.offering.id } },
              body: { user_id: userId },
            })
          }
          revokeFn={(userId) =>
            apiClient.POST(
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
          }
        />
      )}

      {tenantId && assignmentMode?.kind === "enrolments" && (
        <AssignmentModal
          open
          title={`Student Enrolments — ${assignmentMode.offering.name}`}
          subtitle="Enrolled students must hold active membership with the student role."
          canManage={flags.isAdmin}
          eligibleRoleFilter="student"
          assignActionLabel="Enrol student"
          revokeActionLabel="Withdraw"
          members={options.members}
          onClose={() => setAssignmentMode(null)}
          listFn={() =>
            apiClient.GET("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/enrolments", {
              params: {
                path: { tenant_id: tenantId, offering_id: assignmentMode.offering.id },
                query: { limit: 100 },
              },
            })
          }
          assignFn={(userId) =>
            apiClient.POST("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/enrolments", {
              params: { path: { tenant_id: tenantId, offering_id: assignmentMode.offering.id } },
              body: { user_id: userId },
            })
          }
          revokeFn={(userId) =>
            apiClient.POST(
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
