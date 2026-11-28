import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  normalizeApiError,
  type MemberOnboardResponse,
  type MembershipStatus,
  type NormalizedApiError,
  type StudentProfileSummary,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { ErrorState, LoadingSpinner } from "../../components/common/FeedbackStates";
import {
  AlertBanner,
  ConfirmDialog,
  DataTable,
  FormField,
  ModalDialog,
  PageHeader,
  SectionCard,
  StatusBadge,
} from "../../components/common/UiPrimitives";
import { deriveRoleFlags } from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { useActionFeedback } from "../common/useActionFeedback";
import { useTenantOptions } from "../common/useTenantOptions";

function statusTone(
  status: string,
): "success" | "warning" | "danger" | "neutral" {
  if (status === "active") return "success";
  if (status === "invited") return "warning";
  if (status === "revoked") return "danger";
  return "neutral";
}

function emptyToNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

export function CollegeStudentsPage() {
  const { apiClient, controller: authController } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);
  const feedback = useActionFeedback();
  const options = useTenantOptions({
    departments: true,
    academicYears: true,
    semesters: true,
    sections: true,
  });

  const [students, setStudents] = useState<StudentProfileSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<NormalizedApiError | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<MembershipStatus | "all">("all");
  const [deptFilter, setDeptFilter] = useState("");
  const [yearFilter, setYearFilter] = useState("");
  const [semesterFilter, setSemesterFilter] = useState("");
  const [sectionFilter, setSectionFilter] = useState("");

  // Onboarding form state
  const [onboardName, setOnboardName] = useState("");
  const [onboardEmail, setOnboardEmail] = useState("");
  const [onboardRoll, setOnboardRoll] = useState("");
  const [onboardDeptId, setOnboardDeptId] = useState("");
  const [onboardYearId, setOnboardYearId] = useState("");
  const [onboardSemesterId, setOnboardSemesterId] = useState("");
  const [onboardSectionId, setOnboardSectionId] = useState("");
  const [onboardPhone, setOnboardPhone] = useState("");
  const [onboardContactEmail, setOnboardContactEmail] = useState("");
  const [onboardGuardianContact, setOnboardGuardianContact] = useState("");
  const [onboardPassword, setOnboardPassword] = useState("");
  const [adding, setAdding] = useState(false);
  const [latestInvite, setLatestInvite] = useState<{
    name: string;
    email: string;
    token: string;
  } | null>(null);

  // Profile modal state
  const [selectedStudent, setSelectedStudent] =
    useState<StudentProfileSummary | null>(null);
  const [editingProfile, setEditingProfile] = useState(false);
  const [editName, setEditName] = useState("");
  const [editRoll, setEditRoll] = useState("");
  const [editDeptId, setEditDeptId] = useState("");
  const [editYearId, setEditYearId] = useState("");
  const [editSemesterId, setEditSemesterId] = useState("");
  const [editSectionId, setEditSectionId] = useState("");
  const [editPhone, setEditPhone] = useState("");
  const [editContactEmail, setEditContactEmail] = useState("");
  const [editGuardianContact, setEditGuardianContact] = useState("");

  const [busy, setBusy] = useState(false);
  const [confirmLifecycle, setConfirmLifecycle] = useState<{
    student: StudentProfileSummary;
    nextStatus: "active" | "disabled" | "revoked";
  } | null>(null);

  const reload = useCallback(() => setReloadTick((t) => t + 1), []);

  useEffect(() => {
    if (!tenantId) {
      setStudents([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    void authController
      .runWithFreshToken(() =>
        apiClient.GET("/api/v1/tenants/{tenant_id}/students", {
          params: {
            path: { tenant_id: tenantId },
            query: {
              q: search.trim() || undefined,
              status: statusFilter === "all" ? undefined : statusFilter,
              department_id: deptFilter || undefined,
              academic_year_id: yearFilter || undefined,
              semester_id: semesterFilter || undefined,
              section_id: sectionFilter || undefined,
              limit: 100,
            },
          },
        }),
      )
      .then((res) => {
        if (cancelled) return;
        if (res.data) {
          setStudents(res.data.items);
        } else {
          setLoadError(
            normalizeApiError(
              res.error,
              res.response.status,
              "Unable to load student directory.",
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
  }, [
    apiClient,
    authController,
    deptFilter,
    reloadTick,
    search,
    sectionFilter,
    semesterFilter,
    statusFilter,
    tenantId,
    yearFilter,
  ]);

  const openProfile = (student: StudentProfileSummary) => {
    setSelectedStudent(student);
    setEditingProfile(false);
    setEditName(student.display_name);
    setEditRoll(student.roll_number ?? "");
    setEditDeptId(student.department_id ?? "");
    setEditYearId(student.academic_year_id ?? "");
    setEditSemesterId(student.semester_id ?? "");
    setEditSectionId(student.section_id ?? "");
    setEditPhone(student.phone ?? "");
    setEditContactEmail(student.contact_email ?? "");
    setEditGuardianContact(student.guardian_contact ?? "");
  };

  const handleOnboardStudent = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId) return;
    if (!onboardName.trim() || !onboardEmail.trim() || !onboardEmail.includes("@")) {
      feedback.setValidationError("Valid student name and email are required.");
      return;
    }
    if (onboardPassword && onboardPassword.length < 12) {
      feedback.setValidationError(
        "Initial password must be at least 12 characters (or leave blank to send an invitation).",
      );
      return;
    }

    setLatestInvite(null);
    let onboarded: MemberOnboardResponse | null = null;
    const ok = await feedback.runApiAction({
      setBusy: setAdding,
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/memberships/onboard", {
          params: { path: { tenant_id: tenantId } },
          body: {
            email: onboardEmail.trim(),
            display_name: onboardName.trim(),
            role: "student",
            initial_password: onboardPassword ? onboardPassword : null,
          },
        }),
      fallbackError: "Unable to onboard student.",
      onSuccess: (data) => {
        onboarded = data;
      },
    });
    if (!ok || !onboarded) return;
    const onboardedMember: MemberOnboardResponse = onboarded;

    const hasProfileFields =
      onboardRoll.trim() ||
      onboardDeptId ||
      onboardYearId ||
      onboardSemesterId ||
      onboardSectionId ||
      onboardPhone.trim() ||
      onboardContactEmail.trim() ||
      onboardGuardianContact.trim();

    if (hasProfileFields) {
      await feedback.runApiAction({
        setBusy: setAdding,
        call: () =>
          apiClient.PATCH(
            "/api/v1/tenants/{tenant_id}/students/{user_id}/profile",
            {
              params: {
                path: { tenant_id: tenantId, user_id: onboardedMember.member.user_id },
              },
              body: {
                roll_number: emptyToNull(onboardRoll),
                department_id: onboardDeptId || null,
                academic_year_id: onboardYearId || null,
                semester_id: onboardSemesterId || null,
                section_id: onboardSectionId || null,
                phone: emptyToNull(onboardPhone),
                contact_email: emptyToNull(onboardContactEmail),
                guardian_contact: emptyToNull(onboardGuardianContact),
              },
            },
          ),
        fallbackError: "Student onboarded, but profile details failed to save.",
        onSuccess: () => undefined,
      });
    }

    if (onboardedMember.invite_token) {
      setLatestInvite({
        name: onboardedMember.member.display_name,
        email: onboardedMember.email,
        token: onboardedMember.invite_token,
      });
      feedback.setActionSuccess(
        `Invited student "${onboardedMember.member.display_name}". Share the one-time invitation token below.`,
      );
    } else {
      feedback.setActionSuccess(
        `Onboarded student "${onboardedMember.member.display_name}" (${onboardedMember.email}).`,
      );
    }

    setOnboardName("");
    setOnboardEmail("");
    setOnboardRoll("");
    setOnboardDeptId("");
    setOnboardYearId("");
    setOnboardSemesterId("");
    setOnboardSectionId("");
    setOnboardPhone("");
    setOnboardContactEmail("");
    setOnboardGuardianContact("");
    setOnboardPassword("");
    reload();
  };

  const handleSaveProfile = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId || !selectedStudent) return;
    if (!editName.trim()) {
      feedback.setValidationError("Student name cannot be empty.");
      return;
    }

    await feedback.runApiAction({
      setBusy,
      call: () =>
        apiClient.PATCH(
          "/api/v1/tenants/{tenant_id}/students/{user_id}/profile",
          {
            params: {
              path: { tenant_id: tenantId, user_id: selectedStudent.user_id },
            },
            body: {
              display_name: editName.trim(),
              roll_number: emptyToNull(editRoll),
              department_id: editDeptId || null,
              academic_year_id: editYearId || null,
              semester_id: editSemesterId || null,
              section_id: editSectionId || null,
              phone: emptyToNull(editPhone),
              contact_email: emptyToNull(editContactEmail),
              guardian_contact: emptyToNull(editGuardianContact),
            },
          },
        ),
      fallbackError: "Unable to update student profile.",
      onSuccess: (updated) => {
        setSelectedStudent(updated);
        setEditingProfile(false);
        feedback.setActionSuccess(
          `Updated academic profile for "${updated.display_name}".`,
        );
        reload();
      },
    });
  };

  const handleLifecycleChange = async () => {
    if (!tenantId || !confirmLifecycle) return;
    const { student, nextStatus } = confirmLifecycle;
    await feedback.runApiAction({
      setBusy,
      call: () =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/memberships/{user_id}/lifecycle",
          {
            params: { path: { tenant_id: tenantId, user_id: student.user_id } },
            body: { status: nextStatus },
          },
        ),
      fallbackError: "Unable to update student membership status.",
      onSuccess: (data) => {
        feedback.setActionSuccess(
          `Updated ${data.display_name} status to "${data.status}".`,
        );
        reload();
      },
      onFinally: () => setConfirmLifecycle(null),
    });
  };

  if (!tenantId) return null;

  const filteredSemesters = onboardYearId
    ? options.semesters.filter((s) => s.academic_year_id === onboardYearId)
    : options.semesters;
  const filteredSections = onboardSemesterId
    ? options.sections.filter((s) => s.semester_id === onboardSemesterId)
    : options.sections;

  const editFilteredSemesters = editYearId
    ? options.semesters.filter((s) => s.academic_year_id === editYearId)
    : options.semesters;
  const editFilteredSections = editSemesterId
    ? options.sections.filter((s) => s.semester_id === editSemesterId)
    : options.sections;

  return (
    <div className="viora-stack">
      <PageHeader
        title="Students"
        subtitle={`Student onboarding, academic profiles, and searchable directory for ${tenant.selectedInstitution?.name ?? "your institution"}.`}
      />

      {feedback.alerts}

      {latestInvite && (
        <AlertBanner
          tone="info"
          title={`Invitation ready for ${latestInvite.name} (${latestInvite.email})`}
          message={`Share this one-time activation link or token: ${window.location.origin}/?invite=${latestInvite.token}`}
        />
      )}

      {flags.isAdmin && (
        <SectionCard
          title="Onboard Student"
          subtitle="Create or associate a student account by email and assign their academic profile. Leave Initial Password blank to generate an invitation token."
        >
          <form onSubmit={handleOnboardStudent} className="viora-stack">
            <div className="viora-grid viora-grid-3">
              <FormField label="Student Name *" htmlFor="student-onboard-name">
                <input
                  id="student-onboard-name"
                  type="text"
                  className="viora-input"
                  placeholder="Aarav Sharma"
                  value={onboardName}
                  onChange={(e) => setOnboardName(e.target.value)}
                  disabled={adding}
                  required
                />
              </FormField>

              <FormField label="Login Email *" htmlFor="student-onboard-email">
                <input
                  id="student-onboard-email"
                  type="email"
                  className="viora-input"
                  placeholder="aarav.sharma@college.edu"
                  value={onboardEmail}
                  onChange={(e) => setOnboardEmail(e.target.value)}
                  disabled={adding}
                  required
                />
              </FormField>

              <FormField label="Roll Number" htmlFor="student-onboard-roll">
                <input
                  id="student-onboard-roll"
                  type="text"
                  className="viora-input"
                  placeholder="CS2026-042"
                  value={onboardRoll}
                  onChange={(e) => setOnboardRoll(e.target.value)}
                  disabled={adding}
                />
              </FormField>

              <FormField label="Department" htmlFor="student-onboard-dept">
                <select
                  id="student-onboard-dept"
                  className="viora-select"
                  value={onboardDeptId}
                  onChange={(e) => setOnboardDeptId(e.target.value)}
                  disabled={adding}
                >
                  <option value="">Select department...</option>
                  {options.departments.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </FormField>

              <FormField label="Academic Year" htmlFor="student-onboard-year">
                <select
                  id="student-onboard-year"
                  className="viora-select"
                  value={onboardYearId}
                  onChange={(e) => {
                    setOnboardYearId(e.target.value);
                    setOnboardSemesterId("");
                    setOnboardSectionId("");
                  }}
                  disabled={adding}
                >
                  <option value="">Select academic year...</option>
                  {options.academicYears.map((y) => (
                    <option key={y.id} value={y.id}>
                      {y.name}
                    </option>
                  ))}
                </select>
              </FormField>

              <FormField label="Semester" htmlFor="student-onboard-sem">
                <select
                  id="student-onboard-sem"
                  className="viora-select"
                  value={onboardSemesterId}
                  onChange={(e) => {
                    setOnboardSemesterId(e.target.value);
                    setOnboardSectionId("");
                  }}
                  disabled={adding}
                >
                  <option value="">Select semester...</option>
                  {filteredSemesters.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </FormField>

              <FormField label="Section" htmlFor="student-onboard-section">
                <select
                  id="student-onboard-section"
                  className="viora-select"
                  value={onboardSectionId}
                  onChange={(e) => setOnboardSectionId(e.target.value)}
                  disabled={adding}
                >
                  <option value="">Select section...</option>
                  {filteredSections.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </FormField>

              <FormField label="Phone Number" htmlFor="student-onboard-phone">
                <input
                  id="student-onboard-phone"
                  type="tel"
                  className="viora-input"
                  placeholder="+91 98765 43210"
                  value={onboardPhone}
                  onChange={(e) => setOnboardPhone(e.target.value)}
                  disabled={adding}
                />
              </FormField>

              <FormField label="Contact Email" htmlFor="student-onboard-cemail">
                <input
                  id="student-onboard-cemail"
                  type="email"
                  className="viora-input"
                  placeholder="Optional secondary email"
                  value={onboardContactEmail}
                  onChange={(e) => setOnboardContactEmail(e.target.value)}
                  disabled={adding}
                />
              </FormField>

              <FormField label="Guardian Contact" htmlFor="student-onboard-guardian">
                <input
                  id="student-onboard-guardian"
                  type="text"
                  className="viora-input"
                  placeholder="Parent/guardian phone or email"
                  value={onboardGuardianContact}
                  onChange={(e) => setOnboardGuardianContact(e.target.value)}
                  disabled={adding}
                />
              </FormField>

              <FormField
                label="Initial Password (optional, 12+ chars)"
                htmlFor="student-onboard-password"
              >
                <input
                  id="student-onboard-password"
                  type="password"
                  className="viora-input"
                  placeholder="Leave blank to generate invite"
                  value={onboardPassword}
                  onChange={(e) => setOnboardPassword(e.target.value)}
                  disabled={adding}
                />
              </FormField>
            </div>

            <div className="viora-row">
              <button
                type="submit"
                className="viora-btn viora-btn-primary"
                disabled={adding}
              >
                {adding
                  ? "Onboarding..."
                  : onboardPassword
                    ? "Create Student Account"
                    : "Invite Student"}
              </button>
            </div>
          </form>
        </SectionCard>
      )}

      <SectionCard
        title={`Student Directory (${students.length})`}
        actions={
          <div className="viora-row" style={{ flexWrap: "wrap" }}>
            <input
              type="search"
              className="viora-input"
              style={{ width: "220px" }}
              placeholder="Search name, roll no, email..."
              aria-label="Search students"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select
              className="viora-select viora-select-inline"
              aria-label="Filter by department"
              value={deptFilter}
              onChange={(e) => setDeptFilter(e.target.value)}
            >
              <option value="">All departments</option>
              {options.departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
            <select
              className="viora-select viora-select-inline"
              aria-label="Filter by academic year"
              value={yearFilter}
              onChange={(e) => setYearFilter(e.target.value)}
            >
              <option value="">All years</option>
              {options.academicYears.map((y) => (
                <option key={y.id} value={y.id}>
                  {y.name}
                </option>
              ))}
            </select>
            <select
              className="viora-select viora-select-inline"
              aria-label="Filter by semester"
              value={semesterFilter}
              onChange={(e) => setSemesterFilter(e.target.value)}
            >
              <option value="">All semesters</option>
              {options.semesters.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            <select
              className="viora-select viora-select-inline"
              aria-label="Filter by section"
              value={sectionFilter}
              onChange={(e) => setSectionFilter(e.target.value)}
            >
              <option value="">All sections</option>
              {options.sections.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            <select
              className="viora-select viora-select-inline"
              aria-label="Filter students by status"
              value={statusFilter}
              onChange={(e) =>
                setStatusFilter(e.target.value as MembershipStatus | "all")
              }
            >
              <option value="all">All statuses</option>
              <option value="active">Active</option>
              <option value="invited">Invited</option>
              <option value="disabled">Disabled</option>
              <option value="revoked">Revoked</option>
            </select>
          </div>
        }
      >
        {loading && <LoadingSpinner message="Loading students..." />}
        {!loading && loadError && (
          <ErrorState
            title="Could not load students"
            message={loadError.message}
            onRetry={reload}
          />
        )}
        {!loading && !loadError && (
          <DataTable
            rows={students}
            rowKey={(s) => s.user_id}
            emptyTitle="No students found"
            emptyDescription="No students match your current search or filter."
            columns={[
              {
                key: "name",
                header: "Student",
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
                key: "roll",
                header: "Roll No.",
                render: (s) => s.roll_number ?? <span className="viora-muted">—</span>,
              },
              {
                key: "academics",
                header: "Department / Section",
                render: (s) => (
                  <div>
                    <div>{s.department_name ?? <span className="viora-muted">No dept</span>}</div>
                    <div className="viora-muted" style={{ fontSize: "0.8rem" }}>
                      {[s.academic_year_name, s.semester_name, s.section_name]
                        .filter(Boolean)
                        .join(" · ") || "Unassigned"}
                    </div>
                  </div>
                ),
              },
              {
                key: "membership",
                header: "Status",
                render: (s) => (
                  <StatusBadge tone={statusTone(s.membership_status)}>
                    {s.membership_status}
                  </StatusBadge>
                ),
              },
              {
                key: "actions",
                header: "Actions",
                render: (s) => (
                  <div className="viora-row">
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      onClick={() => openProfile(s)}
                    >
                      Profile
                    </button>
                    {flags.isAdmin && s.membership_status !== "active" && (
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm"
                        onClick={() =>
                          setConfirmLifecycle({ student: s, nextStatus: "active" })
                        }
                      >
                        Activate
                      </button>
                    )}
                    {flags.isAdmin && s.membership_status === "active" && (
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm"
                        onClick={() =>
                          setConfirmLifecycle({ student: s, nextStatus: "disabled" })
                        }
                      >
                        Suspend
                      </button>
                    )}
                    {flags.isAdmin && s.membership_status !== "revoked" && (
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm viora-btn-danger"
                        onClick={() =>
                          setConfirmLifecycle({ student: s, nextStatus: "revoked" })
                        }
                      >
                        Revoke
                      </button>
                    )}
                  </div>
                ),
              },
            ]}
          />
        )}
      </SectionCard>

      <ModalDialog
        open={selectedStudent !== null}
        title={
          selectedStudent
            ? `Student Profile — ${selectedStudent.display_name}`
            : "Student Profile"
        }
        onClose={() => setSelectedStudent(null)}
      >
        {selectedStudent && !editingProfile && (
          <div className="viora-stack">
            <div className="viora-grid viora-grid-2">
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Login Email</div>
                <div>{selectedStudent.email ?? "—"}</div>
              </div>
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Roll Number</div>
                <div>{selectedStudent.roll_number ?? "—"}</div>
              </div>
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Department</div>
                <div>{selectedStudent.department_name ?? "—"}</div>
              </div>
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Academic Year</div>
                <div>{selectedStudent.academic_year_name ?? "—"}</div>
              </div>
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Semester</div>
                <div>{selectedStudent.semester_name ?? "—"}</div>
              </div>
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Section</div>
                <div>{selectedStudent.section_name ?? "—"}</div>
              </div>
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Phone</div>
                <div>{selectedStudent.phone ?? "—"}</div>
              </div>
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Contact Email</div>
                <div>{selectedStudent.contact_email ?? "—"}</div>
              </div>
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Guardian Contact</div>
                <div>{selectedStudent.guardian_contact ?? "—"}</div>
              </div>
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Membership Status</div>
                <StatusBadge tone={statusTone(selectedStudent.membership_status)}>
                  {selectedStudent.membership_status}
                </StatusBadge>
              </div>
            </div>

            {flags.isAdmin && (
              <div className="viora-row">
                <button
                  type="button"
                  className="viora-btn viora-btn-primary"
                  onClick={() => setEditingProfile(true)}
                >
                  Edit Profile
                </button>
              </div>
            )}
          </div>
        )}

        {selectedStudent && editingProfile && (
          <form onSubmit={handleSaveProfile} className="viora-stack">
            <div className="viora-grid viora-grid-2">
              <FormField label="Student Name *" htmlFor="edit-student-name">
                <input
                  id="edit-student-name"
                  type="text"
                  className="viora-input"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  disabled={busy}
                  required
                />
              </FormField>
              <FormField label="Roll Number" htmlFor="edit-student-roll">
                <input
                  id="edit-student-roll"
                  type="text"
                  className="viora-input"
                  value={editRoll}
                  onChange={(e) => setEditRoll(e.target.value)}
                  disabled={busy}
                />
              </FormField>
              <FormField label="Department" htmlFor="edit-student-dept">
                <select
                  id="edit-student-dept"
                  className="viora-select"
                  value={editDeptId}
                  onChange={(e) => setEditDeptId(e.target.value)}
                  disabled={busy}
                >
                  <option value="">Unassigned</option>
                  {options.departments.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </FormField>
              <FormField label="Academic Year" htmlFor="edit-student-year">
                <select
                  id="edit-student-year"
                  className="viora-select"
                  value={editYearId}
                  onChange={(e) => {
                    setEditYearId(e.target.value);
                    setEditSemesterId("");
                    setEditSectionId("");
                  }}
                  disabled={busy}
                >
                  <option value="">Unassigned</option>
                  {options.academicYears.map((y) => (
                    <option key={y.id} value={y.id}>
                      {y.name}
                    </option>
                  ))}
                </select>
              </FormField>
              <FormField label="Semester" htmlFor="edit-student-sem">
                <select
                  id="edit-student-sem"
                  className="viora-select"
                  value={editSemesterId}
                  onChange={(e) => {
                    setEditSemesterId(e.target.value);
                    setEditSectionId("");
                  }}
                  disabled={busy}
                >
                  <option value="">Unassigned</option>
                  {editFilteredSemesters.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </FormField>
              <FormField label="Section" htmlFor="edit-student-section">
                <select
                  id="edit-student-section"
                  className="viora-select"
                  value={editSectionId}
                  onChange={(e) => setEditSectionId(e.target.value)}
                  disabled={busy}
                >
                  <option value="">Unassigned</option>
                  {editFilteredSections.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </FormField>
              <FormField label="Phone" htmlFor="edit-student-phone">
                <input
                  id="edit-student-phone"
                  type="tel"
                  className="viora-input"
                  value={editPhone}
                  onChange={(e) => setEditPhone(e.target.value)}
                  disabled={busy}
                />
              </FormField>
              <FormField label="Contact Email" htmlFor="edit-student-cemail">
                <input
                  id="edit-student-cemail"
                  type="email"
                  className="viora-input"
                  value={editContactEmail}
                  onChange={(e) => setEditContactEmail(e.target.value)}
                  disabled={busy}
                />
              </FormField>
              <FormField label="Guardian Contact" htmlFor="edit-student-guardian">
                <input
                  id="edit-student-guardian"
                  type="text"
                  className="viora-input"
                  value={editGuardianContact}
                  onChange={(e) => setEditGuardianContact(e.target.value)}
                  disabled={busy}
                />
              </FormField>
            </div>
            <div className="viora-row">
              <button
                type="submit"
                className="viora-btn viora-btn-primary"
                disabled={busy}
              >
                {busy ? "Saving..." : "Save Changes"}
              </button>
              <button
                type="button"
                className="viora-btn"
                onClick={() => setEditingProfile(false)}
                disabled={busy}
              >
                Cancel
              </button>
            </div>
          </form>
        )}
      </ModalDialog>

      <ConfirmDialog
        open={confirmLifecycle !== null}
        title={`Set student membership status to "${confirmLifecycle?.nextStatus ?? ""}"?`}
        message={
          confirmLifecycle
            ? `Change ${confirmLifecycle.student.display_name}'s membership from "${confirmLifecycle.student.membership_status}" to "${confirmLifecycle.nextStatus}"?`
            : ""
        }
        confirmLabel="Update status"
        danger={
          confirmLifecycle?.nextStatus === "revoked" ||
          confirmLifecycle?.nextStatus === "disabled"
        }
        busy={busy}
        onCancel={() => setConfirmLifecycle(null)}
        onConfirm={() => void handleLifecycleChange()}
      />
    </div>
  );
}
