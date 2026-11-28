import { useCallback, useEffect, useState } from "react";
import {
  normalizeApiError,
  type AssignedSectionSummary,
  type NormalizedApiError,
  type StudentProfileSummary,
  type SubjectOfferingSummary,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { ErrorState, LoadingSpinner } from "../../components/common/FeedbackStates";
import {
  ActiveBadge,
  DataTable,
  FormField,
  ModalDialog,
  PageHeader,
  SectionCard,
} from "../../components/common/UiPrimitives";
import { useTenant } from "../../tenant/TenantContext";
import { validateRequiredName } from "../../tenant/validation";
import { AssignmentModal } from "../common/AssignmentModal";
import { useActionFeedback } from "../common/useActionFeedback";
import { useTenantOptions } from "../common/useTenantOptions";

export function TeacherClassPage() {
  const { apiClient, controller: authController } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const feedback = useActionFeedback();
  const options = useTenantOptions({ members: true });

  const [homerooms, setHomerooms] = useState<AssignedSectionSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<NormalizedApiError | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  const [selectedSection, setSelectedSection] =
    useState<AssignedSectionSummary | null>(null);

  const [roster, setRoster] = useState<StudentProfileSummary[]>([]);
  const [rosterLoading, setRosterLoading] = useState(false);
  const [rosterSearch, setRosterSearch] = useState("");
  const [profileStudent, setProfileStudent] =
    useState<StudentProfileSummary | null>(null);

  const [sectionOfferings, setSectionOfferings] = useState<
    SubjectOfferingSummary[]
  >([]);
  const [offeringsLoading, setOfferingsLoading] = useState(false);

  const [editingSection, setEditingSection] =
    useState<AssignedSectionSummary | null>(null);
  const [editName, setEditName] = useState("");
  const [saving, setSaving] = useState(false);

  const [teachersOffering, setTeachersOffering] =
    useState<SubjectOfferingSummary | null>(null);

  const reload = useCallback(() => setReloadTick((t) => t + 1), []);

  useEffect(() => {
    if (!tenantId) {
      setHomerooms([]);
      setSelectedSection(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    void authController
      .runWithFreshToken(() =>
        apiClient.GET("/api/v1/tenants/{tenant_id}/my-responsibilities", {
          params: { path: { tenant_id: tenantId } },
        }),
      )
      .then((res) => {
        if (cancelled) return;
        if (res.data) {
          const list = res.data.homeroom_sections;
          setHomerooms(list);
          setSelectedSection((prev) =>
            prev && list.some((s) => s.section_id === prev.section_id)
              ? list.find((s) => s.section_id === prev.section_id) ?? null
              : list[0] ?? null,
          );
        } else {
          setLoadError(
            normalizeApiError(
              res.error,
              res.response.status,
              "Unable to load your assigned homeroom classes.",
            ),
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, authController, reloadTick, tenantId]);

  useEffect(() => {
    if (!tenantId || !selectedSection) {
      setRoster([]);
      return;
    }
    let cancelled = false;
    setRosterLoading(true);
    void authController
      .runWithFreshToken(() =>
        apiClient.GET("/api/v1/tenants/{tenant_id}/sections/{section_id}/roster", {
          params: {
            path: { tenant_id: tenantId, section_id: selectedSection.section_id },
            query: { q: rosterSearch.trim() || undefined, limit: 200 },
          },
        }),
      )
      .then((res) => {
        if (!cancelled && res.data) {
          setRoster(res.data.items);
        }
      })
      .finally(() => {
        if (!cancelled) setRosterLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, authController, rosterSearch, selectedSection, tenantId]);

  useEffect(() => {
    if (!tenantId || !selectedSection) {
      setSectionOfferings([]);
      return;
    }
    let cancelled = false;
    setOfferingsLoading(true);
    void authController
      .runWithFreshToken(() =>
        apiClient.GET("/api/v1/tenants/{tenant_id}/offerings", {
          params: {
            path: { tenant_id: tenantId },
            query: { section_id: selectedSection.section_id, limit: 100 },
          },
        }),
      )
      .then((res) => {
        if (!cancelled && res.data) {
          setSectionOfferings(res.data.items);
        }
      })
      .finally(() => {
        if (!cancelled) setOfferingsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, authController, selectedSection, tenantId]);

  const handleOpenProfile = async (student: StudentProfileSummary) => {
    if (!tenantId) return;
    setProfileStudent(student);
    const res = await authController.runWithFreshToken(() =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/students/{user_id}", {
        params: { path: { tenant_id: tenantId, user_id: student.user_id } },
      }),
    );
    if (res.data) {
      setProfileStudent(res.data);
    }
  };

  const handleRename = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tenantId || !editingSection) return;
    const err = validateRequiredName(editName, "Class section name", 160);
    if (err) {
      feedback.setValidationError(err);
      return;
    }
    await feedback.runApiAction({
      setBusy: setSaving,
      call: () =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/sections/{section_id}", {
          params: {
            path: { tenant_id: tenantId, section_id: editingSection.section_id },
          },
          body: { name: editName.trim() },
        }),
      fallbackError: "Unable to rename class section.",
      onSuccess: (updated) => {
        feedback.setActionSuccess(`Updated section name to "${updated.name}".`);
        setEditingSection(null);
        reload();
      },
    });
  };

  if (!tenantId) return null;

  return (
    <div className="viora-stack">
      <PageHeader
        title="My Class"
        subtitle="Homeroom class roster ordered by roll number, student profiles, and section subject offerings."
      />

      {feedback.alerts}

      <SectionCard title="Assigned Homeroom Classes">
        {loading && <LoadingSpinner message="Loading your assigned classes..." />}
        {!loading && loadError && (
          <ErrorState
            title="Could not load assigned classes"
            message={loadError.message}
            onRetry={reload}
          />
        )}
        {!loading && !loadError && (
          <DataTable
            rows={homerooms}
            rowKey={(s) => s.section_id}
            emptyTitle="No homeroom classes assigned"
            emptyDescription="You are not currently assigned as homeroom coordinator for any active class section."
            columns={[
              {
                key: "name",
                header: "Class Section",
                render: (s) => (
                  <div>
                    <strong>{s.section_name}</strong>
                    <div className="viora-muted" style={{ fontSize: "0.8rem" }}>
                      {[s.department_name, s.programme_name, s.academic_year_name, s.semester_name]
                        .filter(Boolean)
                        .join(" · ")}
                    </div>
                  </div>
                ),
              },
              {
                key: "actions",
                header: "Actions",
                render: (s) => (
                  <div className="viora-row">
                    <button
                      type="button"
                      className={`viora-btn viora-btn-sm ${
                        selectedSection?.section_id === s.section_id
                          ? "viora-btn-primary"
                          : ""
                      }`}
                      onClick={() => setSelectedSection(s)}
                    >
                      {selectedSection?.section_id === s.section_id
                        ? "Selected"
                        : "Open Class"}
                    </button>
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      onClick={() => {
                        setEditName(s.section_name);
                        setEditingSection(s);
                      }}
                    >
                      Rename
                    </button>
                  </div>
                ),
              },
            ]}
          />
        )}
      </SectionCard>

      {selectedSection && (
        <>
          <SectionCard
            title={`Class Roster — ${selectedSection.section_name}`}
            subtitle={`${selectedSection.academic_year_name} · ${selectedSection.semester_name} · Ordered by roll number`}
            actions={
              <input
                type="search"
                className="viora-input"
                style={{ width: "220px" }}
                placeholder="Search roll no. or name..."
                aria-label="Search class roster"
                value={rosterSearch}
                onChange={(e) => setRosterSearch(e.target.value)}
              />
            }
          >
            {rosterLoading ? (
              <LoadingSpinner message="Loading class roster..." />
            ) : (
              <DataTable
                rows={roster}
                rowKey={(s) => s.user_id}
                emptyTitle="No students in this class"
                emptyDescription="No active students are currently assigned to this class section."
                columns={[
                  {
                    key: "roll",
                    header: "Roll Number",
                    render: (s) =>
                      s.roll_number ? (
                        <strong>{s.roll_number}</strong>
                      ) : (
                        <span className="viora-muted">—</span>
                      ),
                  },
                  {
                    key: "name",
                    header: "Student Name",
                    render: (s) => (
                      <div>
                        <strong>{s.display_name}</strong>
                        {s.email && (
                          <div className="viora-muted" style={{ fontSize: "0.8rem" }}>
                            {s.email}
                          </div>
                        )}
                      </div>
                    ),
                  },
                  {
                    key: "contact",
                    header: "Contact",
                    render: (s) => s.phone ?? s.contact_email ?? "—",
                  },
                  {
                    key: "actions",
                    header: "Profile",
                    render: (s) => (
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm"
                        onClick={() => void handleOpenProfile(s)}
                      >
                        View Profile
                      </button>
                    ),
                  },
                ]}
              />
            )}
          </SectionCard>

          <SectionCard
            title={`Subject Offerings — ${selectedSection.section_name}`}
            subtitle="Subjects taught in this class section and their assigned teachers."
          >
            {offeringsLoading ? (
              <LoadingSpinner message="Loading class subject offerings..." />
            ) : (
              <DataTable
                rows={sectionOfferings}
                rowKey={(o) => o.id}
                emptyTitle="No offerings in this class"
                emptyDescription="No subject offerings have been created for this class section yet."
                columns={[
                  {
                    key: "name",
                    header: "Subject Offering",
                    render: (o) => <strong>{o.name}</strong>,
                  },
                  {
                    key: "status",
                    header: "Status",
                    render: (o) => <ActiveBadge active={o.active} />,
                  },
                  {
                    key: "teachers",
                    header: "Teachers",
                    render: (o) => (
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm"
                        onClick={() => setTeachersOffering(o)}
                      >
                        View Teachers
                      </button>
                    ),
                  },
                ]}
              />
            )}
          </SectionCard>
        </>
      )}

      <ModalDialog
        open={profileStudent !== null}
        title={
          profileStudent
            ? `Student Profile — ${profileStudent.display_name}`
            : "Student Profile"
        }
        onClose={() => setProfileStudent(null)}
      >
        {profileStudent && (
          <div className="viora-grid viora-grid-2">
            <div>
              <div className="viora-muted" style={{ fontSize: "0.78rem" }}>
                Roll Number
              </div>
              <div>{profileStudent.roll_number ?? "—"}</div>
            </div>
            <div>
              <div className="viora-muted" style={{ fontSize: "0.78rem" }}>
                Login Email
              </div>
              <div>{profileStudent.email ?? "—"}</div>
            </div>
            <div>
              <div className="viora-muted" style={{ fontSize: "0.78rem" }}>
                Department
              </div>
              <div>{profileStudent.department_name ?? "—"}</div>
            </div>
            <div>
              <div className="viora-muted" style={{ fontSize: "0.78rem" }}>
                Academic Year & Semester
              </div>
              <div>
                {[profileStudent.academic_year_name, profileStudent.semester_name]
                  .filter(Boolean)
                  .join(" · ") || "—"}
              </div>
            </div>
            <div>
              <div className="viora-muted" style={{ fontSize: "0.78rem" }}>
                Section
              </div>
              <div>{profileStudent.section_name ?? "—"}</div>
            </div>
            <div>
              <div className="viora-muted" style={{ fontSize: "0.78rem" }}>
                Phone
              </div>
              <div>{profileStudent.phone ?? "—"}</div>
            </div>
            <div>
              <div className="viora-muted" style={{ fontSize: "0.78rem" }}>
                Contact Email
              </div>
              <div>{profileStudent.contact_email ?? "—"}</div>
            </div>
            <div>
              <div className="viora-muted" style={{ fontSize: "0.78rem" }}>
                Guardian Contact
              </div>
              <div>{profileStudent.guardian_contact ?? "—"}</div>
            </div>
          </div>
        )}
      </ModalDialog>

      <ModalDialog
        open={editingSection !== null}
        title="Rename Class Section"
        onClose={() => setEditingSection(null)}
      >
        <form onSubmit={(e) => void handleRename(e)} className="viora-stack">
          <FormField label="Section name" htmlFor="coord-sec-name">
            <input
              id="coord-sec-name"
              className="viora-input"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              disabled={saving}
              required
            />
          </FormField>
          <div className="viora-row" style={{ justifyContent: "flex-end" }}>
            <button
              type="button"
              className="viora-btn"
              onClick={() => setEditingSection(null)}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="viora-btn viora-btn-primary"
              disabled={saving}
            >
              {saving ? "Saving..." : "Save changes"}
            </button>
          </div>
        </form>
      </ModalDialog>

      {tenantId && teachersOffering && (
        <AssignmentModal
          open
          title={`Assigned Teachers — ${teachersOffering.name}`}
          subtitle="Subject teachers assigned to this offering."
          canManage={false}
          eligibleRoleFilter="subject_teacher"
          assignActionLabel="Assign"
          revokeActionLabel="Revoke"
          members={options.members}
          onClose={() => setTeachersOffering(null)}
          listFn={() =>
            apiClient.GET(
              "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/teachers",
              {
                params: {
                  path: { tenant_id: tenantId, offering_id: teachersOffering.id },
                  query: { limit: 100 },
                },
              },
            )
          }
          assignFn={async () => ({ response: new Response(null, { status: 403 }) })}
          revokeFn={async () => ({ response: new Response(null, { status: 403 }) })}
        />
      )}
    </div>
  );
}
