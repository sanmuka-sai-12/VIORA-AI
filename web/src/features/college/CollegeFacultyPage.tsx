import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  normalizeApiError,
  type FacultyProfileSummary,
  type FacultyResponsibilitiesSummary,
  type InstitutionRole,
  type MemberOnboardResponse,
  type MemberSummary,
  type MembershipStatus,
  type NormalizedApiError,
  type SubjectOfferingSummary,
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
import {
  deriveRoleFlags,
  FACULTY_ROLES,
  ROLE_LABELS,
} from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { useActionFeedback } from "../common/useActionFeedback";
import { resolveLabelById, useTenantOptions } from "../common/useTenantOptions";

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

export function CollegeFacultyPage() {
  const { apiClient, controller: authController, snapshot: auth } = useAuth();
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

  const [faculty, setFaculty] = useState<FacultyProfileSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<NormalizedApiError | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  const [roleFilter, setRoleFilter] = useState<InstitutionRole | "all">("all");
  const [statusFilter, setStatusFilter] = useState<MembershipStatus | "all">("all");
  const [deptFilter, setDeptFilter] = useState("");
  const [search, setSearch] = useState("");

  // Onboarding form state
  const [onboardName, setOnboardName] = useState("");
  const [onboardEmail, setOnboardEmail] = useState("");
  const [initialRole, setInitialRole] = useState<InstitutionRole>("subject_teacher");
  const [onboardEmpCode, setOnboardEmpCode] = useState("");
  const [onboardDeptId, setOnboardDeptId] = useState("");
  const [onboardDesignation, setOnboardDesignation] = useState("");
  const [onboardPhone, setOnboardPhone] = useState("");
  const [onboardContactEmail, setOnboardContactEmail] = useState("");
  const [onboardOffice, setOnboardOffice] = useState("");
  const [onboardPassword, setOnboardPassword] = useState("");
  const [adding, setAdding] = useState(false);
  const [latestInvite, setLatestInvite] = useState<{
    name: string;
    email: string;
    token: string;
  } | null>(null);

  // Profile modal state
  const [selectedFaculty, setSelectedFaculty] =
    useState<FacultyProfileSummary | null>(null);
  const [editingProfile, setEditingProfile] = useState(false);
  const [editName, setEditName] = useState("");
  const [editEmpCode, setEditEmpCode] = useState("");
  const [editDeptId, setEditDeptId] = useState("");
  const [editDesignation, setEditDesignation] = useState("");
  const [editPhone, setEditPhone] = useState("");
  const [editContactEmail, setEditContactEmail] = useState("");
  const [editOffice, setEditOffice] = useState("");

  // Role modal state
  const [roleModalMember, setRoleModalMember] = useState<MemberSummary | null>(null);
  const [roleToGrant, setRoleToGrant] = useState<InstitutionRole>("subject_teacher");
  const [busy, setBusy] = useState(false);

  // Responsibilities modal state
  const [respModalFaculty, setRespModalFaculty] =
    useState<FacultyProfileSummary | null>(null);
  const [respData, setRespData] = useState<FacultyResponsibilitiesSummary | null>(null);
  const [respLoading, setRespLoading] = useState(false);
  const [allOfferings, setAllOfferings] = useState<SubjectOfferingSummary[]>([]);
  const [assignSectionId, setAssignSectionId] = useState("");
  const [assignOfferingId, setAssignOfferingId] = useState("");
  const [respBusy, setRespBusy] = useState(false);

  const [confirmLifecycle, setConfirmLifecycle] = useState<{
    member: FacultyProfileSummary;
    nextStatus: "active" | "disabled" | "revoked";
  } | null>(null);

  const reload = useCallback(() => setReloadTick((t) => t + 1), []);

  const loadFacultyResponsibilities = useCallback(
    async (userId: string) => {
      if (!tenantId) return;
      setRespLoading(true);
      try {
        const [respRes, offRes] = await Promise.all([
          authController.runWithFreshToken(() =>
            apiClient.GET(
              "/api/v1/tenants/{tenant_id}/faculty/{user_id}/responsibilities",
              { params: { path: { tenant_id: tenantId, user_id: userId } } },
            ),
          ),
          authController.runWithFreshToken(() =>
            apiClient.GET("/api/v1/tenants/{tenant_id}/offerings", {
              params: { path: { tenant_id: tenantId }, query: { active: true, limit: 100 } },
            }),
          ),
        ]);
        if (respRes.data) setRespData(respRes.data);
        if (offRes.data) setAllOfferings(offRes.data.items);
      } finally {
        setRespLoading(false);
      }
    },
    [apiClient, authController, tenantId],
  );

  const openResponsibilities = (member: FacultyProfileSummary) => {
    setRespModalFaculty(member);
    setRespData(null);
    setAssignSectionId("");
    setAssignOfferingId("");
    void loadFacultyResponsibilities(member.user_id);
  };

  const ensureFacultyRole = async (
    member: FacultyProfileSummary,
    requiredRole: "class_coordinator" | "subject_teacher",
  ): Promise<boolean> => {
    if (!tenantId) return false;
    if (member.roles.includes(requiredRole)) return true;
    const ok = await feedback.runApiAction({
      setBusy: setRespBusy,
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/memberships/{user_id}/roles", {
          params: { path: { tenant_id: tenantId, user_id: member.user_id } },
          body: { role: requiredRole },
        }),
      fallbackError: `Unable to grant ${ROLE_LABELS[requiredRole]} role.`,
      onSuccess: () => undefined,
    });
    return ok;
  };

  const handleAssignHomeroom = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId || !respModalFaculty || !assignSectionId) return;
    const roleOk = await ensureFacultyRole(respModalFaculty, "class_coordinator");
    if (!roleOk) return;
    await feedback.runApiAction({
      setBusy: setRespBusy,
      call: () =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/sections/{section_id}/coordinators",
          {
            params: { path: { tenant_id: tenantId, section_id: assignSectionId } },
            body: { user_id: respModalFaculty.user_id },
          },
        ),
      fallbackError: "Unable to assign homeroom class.",
      onSuccess: () => {
        setAssignSectionId("");
        feedback.setActionSuccess(
          `Assigned homeroom class to ${respModalFaculty.display_name}.`,
        );
        void loadFacultyResponsibilities(respModalFaculty.user_id);
        reload();
      },
    });
  };

  const handleRevokeHomeroom = async (sectionId: string, sectionName: string) => {
    if (!tenantId || !respModalFaculty) return;
    await feedback.runApiAction({
      setBusy: setRespBusy,
      call: () =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/sections/{section_id}/coordinators/{user_id}/revoke",
          {
            params: {
              path: {
                tenant_id: tenantId,
                section_id: sectionId,
                user_id: respModalFaculty.user_id,
              },
            },
          },
        ),
      fallbackError: "Unable to revoke homeroom class.",
      onSuccess: () => {
        feedback.setActionSuccess(
          `Revoked homeroom class "${sectionName}" from ${respModalFaculty.display_name}.`,
        );
        void loadFacultyResponsibilities(respModalFaculty.user_id);
        reload();
      },
    });
  };

  const handleAssignTeaching = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId || !respModalFaculty || !assignOfferingId) return;
    const roleOk = await ensureFacultyRole(respModalFaculty, "subject_teacher");
    if (!roleOk) return;
    await feedback.runApiAction({
      setBusy: setRespBusy,
      call: () =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/teachers",
          {
            params: { path: { tenant_id: tenantId, offering_id: assignOfferingId } },
            body: { user_id: respModalFaculty.user_id },
          },
        ),
      fallbackError: "Unable to assign teaching subject.",
      onSuccess: () => {
        setAssignOfferingId("");
        feedback.setActionSuccess(
          `Assigned teaching subject to ${respModalFaculty.display_name}.`,
        );
        void loadFacultyResponsibilities(respModalFaculty.user_id);
        reload();
      },
    });
  };

  const handleRevokeTeaching = async (offeringId: string, offeringName: string) => {
    if (!tenantId || !respModalFaculty) return;
    await feedback.runApiAction({
      setBusy: setRespBusy,
      call: () =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/teachers/{user_id}/revoke",
          {
            params: {
              path: {
                tenant_id: tenantId,
                offering_id: offeringId,
                user_id: respModalFaculty.user_id,
              },
            },
          },
        ),
      fallbackError: "Unable to revoke teaching subject.",
      onSuccess: () => {
        feedback.setActionSuccess(
          `Revoked teaching subject "${offeringName}" from ${respModalFaculty.display_name}.`,
        );
        void loadFacultyResponsibilities(respModalFaculty.user_id);
        reload();
      },
    });
  };

  useEffect(() => {
    if (!tenantId) {
      setFaculty([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    void authController
      .runWithFreshToken(() =>
        apiClient.GET("/api/v1/tenants/{tenant_id}/faculty", {
          params: {
            path: { tenant_id: tenantId },
            query: {
              q: search.trim() || undefined,
              role: roleFilter === "all" ? undefined : roleFilter,
              status: statusFilter === "all" ? undefined : statusFilter,
              department_id: deptFilter || undefined,
              limit: 100,
            },
          },
        }),
      )
      .then((res) => {
        if (cancelled) return;
        if (res.data) {
          setFaculty(res.data.items);
        } else {
          setLoadError(
            normalizeApiError(
              res.error,
              res.response.status,
              "Unable to load faculty directory.",
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
    roleFilter,
    search,
    statusFilter,
    tenantId,
  ]);

  const openProfile = (member: FacultyProfileSummary) => {
    setSelectedFaculty(member);
    setEditingProfile(false);
    setEditName(member.display_name);
    setEditEmpCode(member.employee_code ?? "");
    setEditDeptId(member.department_id ?? "");
    setEditDesignation(member.designation ?? "");
    setEditPhone(member.phone ?? "");
    setEditContactEmail(member.contact_email ?? "");
    setEditOffice(member.office_location ?? "");
  };

  const handleOnboardFaculty = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId) return;
    if (!onboardName.trim() || !onboardEmail.trim() || !onboardEmail.includes("@")) {
      feedback.setValidationError("Valid faculty name and email are required.");
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
            role: initialRole,
            initial_password: onboardPassword ? onboardPassword : null,
          },
        }),
      fallbackError: "Unable to onboard faculty member.",
      onSuccess: (data) => {
        onboarded = data;
      },
    });
    if (!ok || !onboarded) return;
    const onboardedMember: MemberOnboardResponse = onboarded;

    const hasProfileFields =
      onboardEmpCode.trim() ||
      onboardDeptId ||
      onboardDesignation.trim() ||
      onboardPhone.trim() ||
      onboardContactEmail.trim() ||
      onboardOffice.trim();

    if (hasProfileFields) {
      await feedback.runApiAction({
        setBusy: setAdding,
        call: () =>
          apiClient.PATCH(
            "/api/v1/tenants/{tenant_id}/faculty/{user_id}/profile",
            {
              params: {
                path: { tenant_id: tenantId, user_id: onboardedMember.member.user_id },
              },
              body: {
                employee_code: emptyToNull(onboardEmpCode),
                department_id: onboardDeptId || null,
                designation: emptyToNull(onboardDesignation),
                phone: emptyToNull(onboardPhone),
                contact_email: emptyToNull(onboardContactEmail),
                office_location: emptyToNull(onboardOffice),
              },
            },
          ),
        fallbackError: "Faculty onboarded, but profile details failed to save.",
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
        `Invited faculty "${onboardedMember.member.display_name}" as ${ROLE_LABELS[initialRole]}. Share the invitation token below.`,
      );
    } else {
      feedback.setActionSuccess(
        `Onboarded "${onboardedMember.member.display_name}" as ${ROLE_LABELS[initialRole]}.`,
      );
    }

    setOnboardName("");
    setOnboardEmail("");
    setOnboardEmpCode("");
    setOnboardDeptId("");
    setOnboardDesignation("");
    setOnboardPhone("");
    setOnboardContactEmail("");
    setOnboardOffice("");
    setOnboardPassword("");
    reload();
  };

  const handleSaveProfile = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId || !selectedFaculty) return;
    if (!editName.trim()) {
      feedback.setValidationError("Faculty name cannot be empty.");
      return;
    }
    await feedback.runApiAction({
      setBusy,
      call: () =>
        apiClient.PATCH(
          "/api/v1/tenants/{tenant_id}/faculty/{user_id}/profile",
          {
            params: {
              path: { tenant_id: tenantId, user_id: selectedFaculty.user_id },
            },
            body: {
              display_name: editName.trim(),
              employee_code: emptyToNull(editEmpCode),
              department_id: editDeptId || null,
              designation: emptyToNull(editDesignation),
              phone: emptyToNull(editPhone),
              contact_email: emptyToNull(editContactEmail),
              office_location: emptyToNull(editOffice),
            },
          },
        ),
      fallbackError: "Unable to update faculty profile.",
      onSuccess: (updated) => {
        setSelectedFaculty(updated);
        setEditingProfile(false);
        feedback.setActionSuccess(
          `Updated profile for "${updated.display_name}".`,
        );
        reload();
      },
    });
  };

  const handleGrantRole = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId || !roleModalMember) return;
    await feedback.runApiAction({
      setBusy,
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/memberships/{user_id}/roles", {
          params: { path: { tenant_id: tenantId, user_id: roleModalMember.user_id } },
          body: { role: roleToGrant },
        }),
      fallbackError: "Unable to assign faculty role.",
      onSuccess: (data) => {
        setRoleModalMember(data);
        feedback.setActionSuccess(
          `Assigned "${ROLE_LABELS[roleToGrant]}" to ${data.display_name}.`,
        );
        reload();
      },
    });
  };

  const handleRevokeFacultyRole = async (
    member: MemberSummary,
    role: InstitutionRole,
  ) => {
    if (!tenantId) return;
    await feedback.runApiAction({
      setBusy,
      call: () =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/memberships/{user_id}/roles/{role}/revoke",
          {
            params: {
              path: { tenant_id: tenantId, user_id: member.user_id, role },
            },
          },
        ),
      fallbackError: "Unable to revoke role.",
      onSuccess: (data) => {
        setRoleModalMember(data);
        feedback.setActionSuccess(
          `Removed "${ROLE_LABELS[role]}" from ${data.display_name}.`,
        );
        reload();
      },
    });
  };

  const handleLifecycleChange = async () => {
    if (!tenantId || !confirmLifecycle) return;
    const { member, nextStatus } = confirmLifecycle;
    await feedback.runApiAction({
      setBusy,
      call: () =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/memberships/{user_id}/lifecycle",
          {
            params: { path: { tenant_id: tenantId, user_id: member.user_id } },
            body: { status: nextStatus },
          },
        ),
      fallbackError: "Unable to update faculty membership status.",
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

  return (
    <div className="viora-stack">
      <PageHeader
        title="Faculty"
        subtitle={`Faculty onboarding, profiles, and role directory for ${tenant.selectedInstitution?.name ?? "your institution"}.`}
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
          title="Onboard Faculty Member"
          subtitle="Create or associate a faculty account by email, assign an academic role, and set their department profile."
        >
          <form onSubmit={handleOnboardFaculty} className="viora-stack">
            <div className="viora-grid viora-grid-3">
              <FormField label="Faculty Name *" htmlFor="faculty-onboard-name">
                <input
                  id="faculty-onboard-name"
                  type="text"
                  className="viora-input"
                  placeholder="Dr. Meera Nair"
                  value={onboardName}
                  onChange={(e) => setOnboardName(e.target.value)}
                  disabled={adding}
                  required
                />
              </FormField>

              <FormField label="Login Email *" htmlFor="faculty-onboard-email">
                <input
                  id="faculty-onboard-email"
                  type="email"
                  className="viora-input"
                  placeholder="meera.nair@college.edu"
                  value={onboardEmail}
                  onChange={(e) => setOnboardEmail(e.target.value)}
                  disabled={adding}
                  required
                />
              </FormField>

              <FormField label="Initial Faculty Role *" htmlFor="faculty-initial-role">
                <select
                  id="faculty-initial-role"
                  className="viora-select"
                  value={initialRole}
                  onChange={(e) => setInitialRole(e.target.value as InstitutionRole)}
                  disabled={adding}
                >
                  {FACULTY_ROLES.map((r) => (
                    <option key={r} value={r}>
                      {ROLE_LABELS[r]}
                    </option>
                  ))}
                </select>
              </FormField>

              <FormField label="Employee Code" htmlFor="faculty-onboard-code">
                <input
                  id="faculty-onboard-code"
                  type="text"
                  className="viora-input"
                  placeholder="FAC-104"
                  value={onboardEmpCode}
                  onChange={(e) => setOnboardEmpCode(e.target.value)}
                  disabled={adding}
                />
              </FormField>

              <FormField label="Department" htmlFor="faculty-onboard-dept">
                <select
                  id="faculty-onboard-dept"
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

              <FormField label="Designation" htmlFor="faculty-onboard-desig">
                <input
                  id="faculty-onboard-desig"
                  type="text"
                  className="viora-input"
                  placeholder="Associate Professor"
                  value={onboardDesignation}
                  onChange={(e) => setOnboardDesignation(e.target.value)}
                  disabled={adding}
                />
              </FormField>

              <FormField label="Phone Number" htmlFor="faculty-onboard-phone">
                <input
                  id="faculty-onboard-phone"
                  type="tel"
                  className="viora-input"
                  placeholder="+91 98765 00000"
                  value={onboardPhone}
                  onChange={(e) => setOnboardPhone(e.target.value)}
                  disabled={adding}
                />
              </FormField>

              <FormField label="Contact Email" htmlFor="faculty-onboard-cemail">
                <input
                  id="faculty-onboard-cemail"
                  type="email"
                  className="viora-input"
                  placeholder="Optional secondary email"
                  value={onboardContactEmail}
                  onChange={(e) => setOnboardContactEmail(e.target.value)}
                  disabled={adding}
                />
              </FormField>

              <FormField label="Office Location" htmlFor="faculty-onboard-office">
                <input
                  id="faculty-onboard-office"
                  type="text"
                  className="viora-input"
                  placeholder="Block B, Room 204"
                  value={onboardOffice}
                  onChange={(e) => setOnboardOffice(e.target.value)}
                  disabled={adding}
                />
              </FormField>

              <FormField
                label="Initial Password (optional, 12+ chars)"
                htmlFor="faculty-onboard-password"
              >
                <input
                  id="faculty-onboard-password"
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
                    ? "Create Faculty Account"
                    : "Invite Faculty Member"}
              </button>
            </div>
          </form>
        </SectionCard>
      )}

      <SectionCard
        title={`Faculty Directory (${faculty.length})`}
        actions={
          <div className="viora-row" style={{ flexWrap: "wrap" }}>
            <input
              type="search"
              className="viora-input"
              style={{ width: "220px" }}
              placeholder="Search name, code, email..."
              aria-label="Search faculty"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select
              className="viora-select viora-select-inline"
              aria-label="Filter faculty by department"
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
              aria-label="Filter faculty by role"
              value={roleFilter}
              onChange={(e) =>
                setRoleFilter(e.target.value as InstitutionRole | "all")
              }
            >
              <option value="all">All faculty roles</option>
              {FACULTY_ROLES.map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABELS[r]}
                </option>
              ))}
            </select>
            <select
              className="viora-select viora-select-inline"
              aria-label="Filter faculty by status"
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
        {loading && <LoadingSpinner message="Loading faculty..." />}
        {!loading && loadError && (
          <ErrorState
            title="Could not load faculty"
            message={loadError.message}
            onRetry={reload}
          />
        )}
        {!loading && !loadError && (
          <DataTable
            rows={faculty}
            rowKey={(m) => m.user_id}
            emptyTitle="No faculty members found"
            emptyDescription="No faculty members match your current search or filter."
            columns={[
              {
                key: "name",
                header: "Faculty Member",
                render: (m) => (
                  <div>
                    <strong>{m.display_name}</strong>
                    {m.email && (
                      <div className="viora-muted" style={{ fontSize: "0.8rem" }}>
                        {m.email}
                      </div>
                    )}
                  </div>
                ),
              },
              {
                key: "dept",
                header: "Department & Designation",
                render: (m) => (
                  <div>
                    <div>{m.department_name ?? <span className="viora-muted">No dept</span>}</div>
                    <div className="viora-muted" style={{ fontSize: "0.8rem" }}>
                      {[m.employee_code, m.designation].filter(Boolean).join(" · ") || "—"}
                    </div>
                  </div>
                ),
              },
              {
                key: "roles",
                header: "Academic Roles",
                render: (m) => (
                  <div className="viora-row" style={{ gap: "0.35rem" }}>
                    {m.roles.map((r) => (
                      <StatusBadge key={r} tone="brand">
                        {ROLE_LABELS[r as InstitutionRole] ?? r}
                      </StatusBadge>
                    ))}
                  </div>
                ),
              },
              {
                key: "status",
                header: "Status",
                render: (m) => (
                  <StatusBadge tone={statusTone(m.membership_status)}>
                    {m.membership_status}
                  </StatusBadge>
                ),
              },
              {
                key: "actions",
                header: "Actions",
                render: (m) => (
                  <div className="viora-row">
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      onClick={() => openProfile(m)}
                    >
                      Profile
                    </button>
                    {flags.isAdmin && (
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm"
                        onClick={() => openResponsibilities(m)}
                      >
                        Responsibilities
                      </button>
                    )}
                    {flags.isAdmin && (
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm"
                        onClick={() =>
                          setRoleModalMember({
                            tenant_id: m.tenant_id,
                            user_id: m.user_id,
                            display_name: m.display_name,
                            account_status: m.account_status as MemberSummary["account_status"],
                            status: m.membership_status as MemberSummary["status"],
                            roles: m.roles as InstitutionRole[],
                          })
                        }
                      >
                        Manage Roles
                      </button>
                    )}
                    {flags.isAdmin && m.membership_status !== "active" && (
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm"
                        onClick={() =>
                          setConfirmLifecycle({ member: m, nextStatus: "active" })
                        }
                      >
                        Activate
                      </button>
                    )}
                    {flags.isAdmin &&
                      m.membership_status === "active" &&
                      m.user_id !== auth.user?.user_id && (
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm"
                          onClick={() =>
                            setConfirmLifecycle({ member: m, nextStatus: "disabled" })
                          }
                        >
                          Suspend
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
        open={selectedFaculty !== null}
        title={
          selectedFaculty
            ? `Faculty Profile — ${selectedFaculty.display_name}`
            : "Faculty Profile"
        }
        onClose={() => setSelectedFaculty(null)}
      >
        {selectedFaculty && !editingProfile && (
          <div className="viora-stack">
            <div className="viora-grid viora-grid-2">
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Login Email</div>
                <div>{selectedFaculty.email ?? "—"}</div>
              </div>
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Employee Code</div>
                <div>{selectedFaculty.employee_code ?? "—"}</div>
              </div>
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Department</div>
                <div>{selectedFaculty.department_name ?? "—"}</div>
              </div>
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Designation</div>
                <div>{selectedFaculty.designation ?? "—"}</div>
              </div>
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Phone</div>
                <div>{selectedFaculty.phone ?? "—"}</div>
              </div>
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Contact Email</div>
                <div>{selectedFaculty.contact_email ?? "—"}</div>
              </div>
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Office Location</div>
                <div>{selectedFaculty.office_location ?? "—"}</div>
              </div>
              <div>
                <div className="viora-muted" style={{ fontSize: "0.78rem" }}>Membership Status</div>
                <StatusBadge tone={statusTone(selectedFaculty.membership_status)}>
                  {selectedFaculty.membership_status}
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

        {selectedFaculty && editingProfile && (
          <form onSubmit={handleSaveProfile} className="viora-stack">
            <div className="viora-grid viora-grid-2">
              <FormField label="Faculty Name *" htmlFor="edit-faculty-name">
                <input
                  id="edit-faculty-name"
                  type="text"
                  className="viora-input"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  disabled={busy}
                  required
                />
              </FormField>
              <FormField label="Employee Code" htmlFor="edit-faculty-code">
                <input
                  id="edit-faculty-code"
                  type="text"
                  className="viora-input"
                  value={editEmpCode}
                  onChange={(e) => setEditEmpCode(e.target.value)}
                  disabled={busy}
                />
              </FormField>
              <FormField label="Department" htmlFor="edit-faculty-dept">
                <select
                  id="edit-faculty-dept"
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
              <FormField label="Designation" htmlFor="edit-faculty-desig">
                <input
                  id="edit-faculty-desig"
                  type="text"
                  className="viora-input"
                  value={editDesignation}
                  onChange={(e) => setEditDesignation(e.target.value)}
                  disabled={busy}
                />
              </FormField>
              <FormField label="Phone" htmlFor="edit-faculty-phone">
                <input
                  id="edit-faculty-phone"
                  type="tel"
                  className="viora-input"
                  value={editPhone}
                  onChange={(e) => setEditPhone(e.target.value)}
                  disabled={busy}
                />
              </FormField>
              <FormField label="Contact Email" htmlFor="edit-faculty-cemail">
                <input
                  id="edit-faculty-cemail"
                  type="email"
                  className="viora-input"
                  value={editContactEmail}
                  onChange={(e) => setEditContactEmail(e.target.value)}
                  disabled={busy}
                />
              </FormField>
              <FormField label="Office Location" htmlFor="edit-faculty-office">
                <input
                  id="edit-faculty-office"
                  type="text"
                  className="viora-input"
                  value={editOffice}
                  onChange={(e) => setEditOffice(e.target.value)}
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

      <ModalDialog
        open={roleModalMember !== null}
        title={
          roleModalMember
            ? `Faculty Roles for ${roleModalMember.display_name}`
            : "Faculty Roles"
        }
        onClose={() => setRoleModalMember(null)}
      >
        {roleModalMember && (
          <div className="viora-stack">
            <div>
              <strong>Current Roles</strong>
              <div
                className="viora-row"
                style={{ marginTop: "0.5rem", gap: "0.5rem" }}
              >
                {roleModalMember.roles.length === 0 ? (
                  <span className="viora-muted">No roles assigned.</span>
                ) : (
                  roleModalMember.roles.map((r) => (
                    <span
                      key={r}
                      className="viora-row"
                      style={{ gap: "0.25rem" }}
                    >
                      <StatusBadge tone="brand">
                        {ROLE_LABELS[r] ?? r}
                      </StatusBadge>
                      {FACULTY_ROLES.includes(r) && (
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm viora-btn-danger"
                          disabled={busy}
                          onClick={() =>
                            void handleRevokeFacultyRole(roleModalMember, r)
                          }
                        >
                          Remove
                        </button>
                      )}
                    </span>
                  ))
                )}
              </div>
            </div>

            {roleModalMember.status === "active" && (
              <form
                onSubmit={handleGrantRole}
                className="viora-row"
                style={{ alignItems: "flex-end" }}
              >
                <div style={{ flex: 1 }}>
                  <FormField
                    label="Assign faculty role"
                    htmlFor="faculty-grant-role"
                  >
                    <select
                      id="faculty-grant-role"
                      className="viora-select"
                      value={roleToGrant}
                      onChange={(e) =>
                        setRoleToGrant(e.target.value as InstitutionRole)
                      }
                      disabled={busy}
                    >
                      {FACULTY_ROLES.map((r) => (
                        <option key={r} value={r}>
                          {ROLE_LABELS[r]}
                        </option>
                      ))}
                    </select>
                  </FormField>
                </div>
                <button
                  type="submit"
                  className="viora-btn viora-btn-primary"
                  disabled={busy}
                >
                  Assign
                </button>
              </form>
            )}
          </div>
        )}
      </ModalDialog>

      <ModalDialog
        open={respModalFaculty !== null}
        title={
          respModalFaculty
            ? `Responsibilities — ${respModalFaculty.display_name}`
            : "Faculty Responsibilities"
        }
        description="Assign and revoke homeroom classes and subject-section offerings independently."
        onClose={() => setRespModalFaculty(null)}
      >
        {respLoading && <LoadingSpinner message="Loading faculty responsibilities..." />}
        {!respLoading && respModalFaculty && (
          <div className="viora-stack">
            <div>
              <strong>Homeroom Classes (Class Coordinator)</strong>
              <div className="viora-stack" style={{ marginTop: "0.5rem", gap: "0.4rem" }}>
                {(!respData || respData.homeroom_sections.length === 0) ? (
                  <span className="viora-muted">No homeroom classes assigned.</span>
                ) : (
                  respData.homeroom_sections.map((sec) => (
                    <div
                      key={sec.section_id}
                      className="viora-row"
                      style={{ justifyContent: "space-between", alignItems: "center" }}
                    >
                      <span>
                        <strong>{sec.section_name}</strong>{" "}
                        <span className="viora-muted">
                          ({sec.semester_name} · {sec.academic_year_name})
                        </span>
                      </span>
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm viora-btn-danger"
                        disabled={respBusy}
                        onClick={() =>
                          void handleRevokeHomeroom(sec.section_id, sec.section_name)
                        }
                      >
                        Revoke
                      </button>
                    </div>
                  ))
                )}
              </div>
              {respModalFaculty.membership_status === "active" && (
                <form
                  onSubmit={handleAssignHomeroom}
                  className="viora-row"
                  style={{ marginTop: "0.75rem", alignItems: "flex-end" }}
                >
                  <div style={{ flex: 1 }}>
                    <FormField label="Assign homeroom class" htmlFor="resp-assign-sec">
                      <select
                        id="resp-assign-sec"
                        className="viora-select"
                        value={assignSectionId}
                        onChange={(e) => setAssignSectionId(e.target.value)}
                        disabled={respBusy}
                        required
                      >
                        <option value="">Select class section...</option>
                        {options.sections
                          .filter((s) => s.active)
                          .map((s) => {
                            const sem = options.semesters.find((x) => x.id === s.semester_id);
                            const yr = sem
                              ? resolveLabelById(options.academicYears, sem.academic_year_id)
                              : null;
                            return (
                              <option key={s.id} value={s.id}>
                                {s.name}
                                {sem ? ` · ${sem.name}` : ""}
                                {yr ? ` (${yr})` : ""}
                              </option>
                            );
                          })}
                      </select>
                    </FormField>
                  </div>
                  <button
                    type="submit"
                    className="viora-btn viora-btn-primary"
                    disabled={respBusy || !assignSectionId}
                  >
                    Assign Homeroom
                  </button>
                </form>
              )}
            </div>

            <hr style={{ border: "none", borderTop: "1px solid var(--color-border)" }} />

            <div>
              <strong>Teaching Subjects (Subject-Section Offerings)</strong>
              <div className="viora-stack" style={{ marginTop: "0.5rem", gap: "0.4rem" }}>
                {(!respData || respData.teaching_offerings.length === 0) ? (
                  <span className="viora-muted">No teaching subjects assigned.</span>
                ) : (
                  respData.teaching_offerings.map((off) => (
                    <div
                      key={off.offering_id}
                      className="viora-row"
                      style={{ justifyContent: "space-between", alignItems: "center" }}
                    >
                      <span>
                        <strong>{off.offering_name}</strong>{" "}
                        <span className="viora-muted">
                          ({off.section_name} · {off.semester_name})
                        </span>
                      </span>
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm viora-btn-danger"
                        disabled={respBusy}
                        onClick={() =>
                          void handleRevokeTeaching(off.offering_id, off.offering_name)
                        }
                      >
                        Revoke
                      </button>
                    </div>
                  ))
                )}
              </div>
              {respModalFaculty.membership_status === "active" && (
                <form
                  onSubmit={handleAssignTeaching}
                  className="viora-row"
                  style={{ marginTop: "0.75rem", alignItems: "flex-end" }}
                >
                  <div style={{ flex: 1 }}>
                    <FormField label="Assign teaching subject" htmlFor="resp-assign-off">
                      <select
                        id="resp-assign-off"
                        className="viora-select"
                        value={assignOfferingId}
                        onChange={(e) => setAssignOfferingId(e.target.value)}
                        disabled={respBusy}
                        required
                      >
                        <option value="">Select subject offering...</option>
                        {allOfferings.map((o) => {
                          const secName = resolveLabelById(options.sections, o.section_id);
                          return (
                            <option key={o.id} value={o.id}>
                              {o.name}
                              {secName ? ` — ${secName}` : ""}
                            </option>
                          );
                        })}
                      </select>
                    </FormField>
                  </div>
                  <button
                    type="submit"
                    className="viora-btn viora-btn-primary"
                    disabled={respBusy || !assignOfferingId}
                  >
                    Assign Subject
                  </button>
                </form>
              )}
            </div>
          </div>
        )}
      </ModalDialog>

      <ConfirmDialog
        open={confirmLifecycle !== null}
        title={`Set faculty membership status to "${confirmLifecycle?.nextStatus ?? ""}"?`}
        message={
          confirmLifecycle
            ? `Change ${confirmLifecycle.member.display_name}'s membership from "${confirmLifecycle.member.membership_status}" to "${confirmLifecycle.nextStatus}"?`
            : ""
        }
        confirmLabel="Update status"
        danger={confirmLifecycle?.nextStatus !== "active"}
        busy={busy}
        onCancel={() => setConfirmLifecycle(null)}
        onConfirm={() => void handleLifecycleChange()}
      />
    </div>
  );
}
