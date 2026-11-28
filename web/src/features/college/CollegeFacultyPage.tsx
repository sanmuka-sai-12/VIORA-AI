import { useState, type FormEvent } from "react";
import type {
  InstitutionRole,
  MemberSummary,
  MembershipStatus,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { ErrorState, LoadingSpinner } from "../../components/common/FeedbackStates";
import {
  ConfirmDialog,
  DataTable,
  FormField,
  ModalDialog,
  PageHeader,
  SectionCard,
  StatusBadge,
} from "../../components/common/UiPrimitives";
import { FACULTY_ROLES, ROLE_LABELS } from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { validateRequiredUuid } from "../../tenant/validation";
import { useActionFeedback } from "../common/useActionFeedback";
import { useAllMembers } from "./useAllMembers";

function statusTone(
  status: MembershipStatus,
): "success" | "warning" | "danger" | "neutral" {
  if (status === "active") return "success";
  if (status === "invited") return "warning";
  if (status === "revoked") return "danger";
  return "neutral";
}

export function CollegeFacultyPage() {
  const { apiClient, snapshot: auth } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const feedback = useActionFeedback();
  const directory = useAllMembers(tenantId);

  const [roleFilter, setRoleFilter] = useState<InstitutionRole | "all">("all");
  const [statusFilter, setStatusFilter] = useState<MembershipStatus | "all">("all");
  const [search, setSearch] = useState("");

  const [addUserId, setAddUserId] = useState("");
  const [initialRole, setInitialRole] = useState<InstitutionRole>("subject_teacher");
  const [adding, setAdding] = useState(false);

  const [roleModalMember, setRoleModalMember] = useState<MemberSummary | null>(null);
  const [roleToGrant, setRoleToGrant] = useState<InstitutionRole>("subject_teacher");
  const [busy, setBusy] = useState(false);

  const [confirmLifecycle, setConfirmLifecycle] = useState<{
    member: MemberSummary;
    nextStatus: "active" | "disabled" | "revoked";
  } | null>(null);

  const faculty = directory.members.filter((m) => {
    const hasFacultyRole = m.roles.some((r) => FACULTY_ROLES.includes(r));
    if (!hasFacultyRole) return false;
    if (roleFilter !== "all" && !m.roles.includes(roleFilter)) return false;
    if (statusFilter !== "all" && m.status !== statusFilter) return false;
    if (
      search.trim() &&
      !m.display_name.toLowerCase().includes(search.trim().toLowerCase())
    ) {
      return false;
    }
    return true;
  });

  const handleAddFaculty = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId) return;
    const problem = validateRequiredUuid(addUserId, "User ID");
    if (problem) {
      feedback.setValidationError(problem);
      return;
    }
    const targetId = addUserId.trim();
    const existing = directory.members.find((m) => m.user_id === targetId);

    if (!existing) {
      const created = await feedback.runApiAction({
        setBusy: setAdding,
        call: () =>
          apiClient.POST("/api/v1/tenants/{tenant_id}/memberships", {
            params: { path: { tenant_id: tenantId } },
            body: { user_id: targetId, status: "active" },
          }),
        fallbackError: "Unable to add faculty membership.",
        onSuccess: () => undefined,
      });
      if (!created) return;
    }

    await feedback.runApiAction({
      setBusy: setAdding,
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/memberships/{user_id}/roles", {
          params: { path: { tenant_id: tenantId, user_id: targetId } },
          body: { role: initialRole },
        }),
      fallbackError: "Unable to assign faculty role.",
      onSuccess: (data) => {
        setAddUserId("");
        feedback.setActionSuccess(
          `Assigned "${ROLE_LABELS[initialRole]}" to ${data.display_name}.`,
        );
        directory.reload();
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
        directory.reload();
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
        directory.reload();
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
      fallbackError: "Unable to update membership status.",
      onSuccess: (data) => {
        feedback.setActionSuccess(
          `Updated ${data.display_name} status to "${data.status}".`,
        );
        directory.reload();
      },
      onFinally: () => setConfirmLifecycle(null),
    });
  };

  if (!tenantId) return null;

  const grantableFacultyRoles = roleModalMember
    ? FACULTY_ROLES.filter((r) => !roleModalMember.roles.includes(r))
    : [];

  return (
    <div className="viora-stack">
      <PageHeader
        title="Faculty"
        subtitle={`Teaching and academic coordination staff in ${tenant.selectedInstitution?.name ?? "your institution"}.`}
      />

      {feedback.alerts}

      <SectionCard
        title="Add Faculty Member"
        subtitle="Associate an existing verified user account and assign their teaching or coordination role."
      >
        <form
          onSubmit={handleAddFaculty}
          className="viora-row"
          style={{ alignItems: "flex-end" }}
        >
          <div style={{ flex: 2, minWidth: "240px" }}>
            <FormField label="Verified User ID" htmlFor="head-faculty-uuid">
              <input
                id="head-faculty-uuid"
                type="text"
                className="viora-input viora-mono"
                placeholder="00000000-0000-0000-0000-000000000000"
                value={addUserId}
                onChange={(e) => setAddUserId(e.target.value)}
                disabled={adding}
                required
              />
            </FormField>
          </div>
          <div style={{ flex: 1, minWidth: "180px" }}>
            <FormField label="Faculty role" htmlFor="head-faculty-role">
              <select
                id="head-faculty-role"
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
          </div>
          <button
            type="submit"
            className="viora-btn viora-btn-primary"
            disabled={adding}
          >
            {adding ? "Adding..." : "Add Faculty"}
          </button>
        </form>
      </SectionCard>

      <SectionCard
        title={`Faculty Directory (${faculty.length})`}
        actions={
          <div className="viora-row">
            <input
              type="search"
              className="viora-input"
              style={{ width: "180px" }}
              placeholder="Search by name..."
              aria-label="Search faculty by name"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
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
        {directory.loading && <LoadingSpinner message="Loading faculty..." />}
        {!directory.loading && directory.error && (
          <ErrorState
            title="Could not load faculty"
            message={directory.error.message}
            onRetry={directory.reload}
          />
        )}
        {!directory.loading && !directory.error && (
          <DataTable
            rows={faculty}
            rowKey={(m) => m.user_id}
            emptyTitle="No faculty members found"
            emptyDescription="No faculty members match your current filter."
            columns={[
              {
                key: "name",
                header: "Faculty Member",
                render: (m) => (
                  <strong>
                    {m.display_name}
                    {auth.user?.user_id === m.user_id ? " (You)" : ""}
                  </strong>
                ),
              },
              {
                key: "roles",
                header: "Responsibilities",
                render: (m) => (
                  <div className="viora-row" style={{ gap: "0.35rem" }}>
                    {m.roles
                      .filter((r) => FACULTY_ROLES.includes(r))
                      .map((r) => (
                        <StatusBadge key={r} tone="brand">
                          {ROLE_LABELS[r]}
                        </StatusBadge>
                      ))}
                  </div>
                ),
              },
              {
                key: "membership",
                header: "Membership",
                render: (m) => (
                  <StatusBadge tone={statusTone(m.status)}>{m.status}</StatusBadge>
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
                      onClick={() => {
                        setRoleModalMember(m);
                        const nextDefault =
                          FACULTY_ROLES.find((r) => !m.roles.includes(r)) ??
                          "subject_teacher";
                        setRoleToGrant(nextDefault);
                      }}
                    >
                      Roles
                    </button>
                    {m.status !== "active" && (
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
                    {m.status === "active" && (
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
        open={roleModalMember !== null}
        title={`Faculty Roles — ${roleModalMember?.display_name ?? ""}`}
        description="Assign or remove teaching and academic coordination responsibilities."
        onClose={() => setRoleModalMember(null)}
      >
        {roleModalMember && (
          <div className="viora-stack">
            <div className="viora-row">
              {roleModalMember.roles
                .filter((r) => FACULTY_ROLES.includes(r))
                .map((r) => (
                  <div
                    key={r}
                    className="viora-row"
                    style={{
                      padding: "0.3rem 0.55rem",
                      background: "var(--color-bg-subtle)",
                      borderRadius: "var(--radius-sm)",
                      border: "1px solid var(--color-border)",
                    }}
                  >
                    <span style={{ fontSize: "0.84rem", fontWeight: 500 }}>
                      {ROLE_LABELS[r]}
                    </span>
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm viora-btn-danger"
                      disabled={busy}
                      onClick={() => void handleRevokeFacultyRole(roleModalMember, r)}
                    >
                      Remove
                    </button>
                  </div>
                ))}
            </div>

            {grantableFacultyRoles.length > 0 && (
              <form
                onSubmit={handleGrantRole}
                className="viora-row"
                style={{ alignItems: "flex-end" }}
              >
                <div style={{ flex: 1 }}>
                  <FormField label="Assign faculty role" htmlFor="faculty-grant-role">
                    <select
                      id="faculty-grant-role"
                      className="viora-select"
                      value={roleToGrant}
                      onChange={(e) =>
                        setRoleToGrant(e.target.value as InstitutionRole)
                      }
                      disabled={busy}
                    >
                      {grantableFacultyRoles.map((r) => (
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
                  {busy ? "Assigning..." : "Assign role"}
                </button>
              </form>
            )}
          </div>
        )}
      </ModalDialog>

      <ConfirmDialog
        open={confirmLifecycle !== null}
        title={`Set membership status to "${confirmLifecycle?.nextStatus ?? ""}"?`}
        message={
          confirmLifecycle
            ? `Change ${confirmLifecycle.member.display_name}'s membership from "${confirmLifecycle.member.status}" to "${confirmLifecycle.nextStatus}"?`
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
