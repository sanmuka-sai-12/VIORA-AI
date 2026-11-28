import { useCallback, useState, type FormEvent } from "react";
import type {
  InstitutionRole,
  MembershipStatus,
  MemberSummary,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import {
  ConfirmDialog,
  DataTable,
  FormField,
  ModalDialog,
  PageHeader,
  PaginationBar,
  SectionCard,
  StatusBadge,
  UuidChip,
} from "../../components/common/UiPrimitives";
import { ErrorState, LoadingSpinner } from "../../components/common/FeedbackStates";
import {
  ALL_INSTITUTION_ROLES,
  deriveRoleFlags,
  ROLE_LABELS,
} from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { validateRequiredUuid } from "../../tenant/validation";
import { useActionFeedback } from "../common/useActionFeedback";
import { useCursorList } from "../common/useCursorList";

function statusTone(
  status: MembershipStatus,
): "success" | "warning" | "danger" | "neutral" {
  if (status === "active") return "success";
  if (status === "invited") return "warning";
  if (status === "revoked") return "danger";
  return "neutral";
}

export function MembershipsPage({ embedded = false }: { embedded?: boolean }) {
  const { apiClient, snapshot: auth } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);
  const feedback = useActionFeedback();

  const [statusFilter, setStatusFilter] = useState<MembershipStatus | "all">("all");
  const [addUserId, setAddUserId] = useState("");
  const [addStatus, setAddStatus] = useState<"active" | "invited">("active");
  const [adding, setAdding] = useState(false);

  const [roleModalMember, setRoleModalMember] = useState<MemberSummary | null>(null);
  const [roleToGrant, setRoleToGrant] = useState<InstitutionRole>("subject_teacher");
  const [busy, setBusy] = useState(false);

  const [confirmLifecycle, setConfirmLifecycle] = useState<{
    member: MemberSummary;
    nextStatus: "active" | "disabled" | "revoked";
  } | null>(null);

  const [confirmRevokeRole, setConfirmRevokeRole] = useState<{
    member: MemberSummary;
    role: InstitutionRole;
  } | null>(null);

  const fetcher = useCallback(
    ({ cursor, limit }: { cursor?: string; limit: number }) =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/memberships", {
        params: {
          path: { tenant_id: tenantId! },
          query: {
            status: statusFilter === "all" ? undefined : statusFilter,
            cursor,
            limit,
          },
        },
      }),
    [apiClient, statusFilter, tenantId],
  );

  const list = useCursorList<MemberSummary>(tenantId, fetcher);

  const handleAddMember = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId) return;
    const problem = validateRequiredUuid(addUserId, "User ID");
    if (problem) {
      feedback.setValidationError(problem);
      return;
    }
    await feedback.runApiAction({
      setBusy: setAdding,
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/memberships", {
          params: { path: { tenant_id: tenantId } },
          body: { user_id: addUserId.trim(), status: addStatus },
        }),
      fallbackError: "Unable to add institutional member.",
      onSuccess: (data) => {
        setAddUserId("");
        feedback.setActionSuccess(`Added member "${data.display_name}" with status "${data.status}".`);
        list.resetToFirstPage();
        list.reload();
      },
    });
  };

  const handleLifecycleChange = async () => {
    if (!tenantId || !confirmLifecycle) return;
    const { member, nextStatus } = confirmLifecycle;
    await feedback.runApiAction({
      setBusy,
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/memberships/{user_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, user_id: member.user_id } },
          body: { status: nextStatus },
        }),
      fallbackError: "Unable to change membership status.",
      onSuccess: (data) => {
        feedback.setActionSuccess(`Updated ${data.display_name} membership status to "${data.status}".`);
        if (roleModalMember?.user_id === data.user_id) setRoleModalMember(data);
        list.reload();
      },
      onFinally: () => setConfirmLifecycle(null),
    });
  };

  const handleGrantRole = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId || !roleModalMember) return;
    const targetId = roleModalMember.user_id;
    await feedback.runApiAction({
      setBusy,
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/memberships/{user_id}/roles", {
          params: { path: { tenant_id: tenantId, user_id: targetId } },
          body: { role: roleToGrant },
        }),
      fallbackError: "Role grant was rejected.",
      onSuccess: (data) => {
        setRoleModalMember(data);
        feedback.setActionSuccess(`Granted role "${ROLE_LABELS[roleToGrant]}" to ${data.display_name}.`);
        list.reload();
      },
    });
  };

  const handleConfirmRevokeRole = async () => {
    if (!tenantId || !confirmRevokeRole) return;
    const { member, role } = confirmRevokeRole;
    await feedback.runApiAction({
      setBusy,
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/memberships/{user_id}/roles/{role}/revoke", {
          params: { path: { tenant_id: tenantId, user_id: member.user_id, role } },
        }),
      fallbackError: "Unable to revoke role.",
      onSuccess: (data) => {
        setRoleModalMember(data);
        feedback.setActionSuccess(`Revoked role "${ROLE_LABELS[role]}" from ${data.display_name}.`);
        list.reload();
      },
      onFinally: () => setConfirmRevokeRole(null),
    });
  };

  const availableRolesForModal = roleModalMember
    ? ALL_INSTITUTION_ROLES.filter((role) => !roleModalMember.roles.includes(role))
    : [];

  return (
    <div className="viora-stack">
      {!embedded && (
        <PageHeader
          title="Institutional Memberships & Roles"
          subtitle="Associate verified users with this institution, manage membership status, and assign roles."
        />
      )}

      {feedback.alerts}

      {flags.isAdmin && (
        <SectionCard
          title="Add Member"
          subtitle="Enter an existing verified User ID to associate them with this institution."
        >
          <form onSubmit={handleAddMember} className="viora-row" style={{ alignItems: "flex-end" }}>
            <div style={{ flex: 2, minWidth: "280px" }}>
              <FormField label="Existing User UUID" htmlFor="mem-add-uuid">
                <input
                  id="mem-add-uuid"
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
            <div style={{ flex: 1, minWidth: "160px" }}>
              <FormField label="Initial status" htmlFor="mem-add-status">
                <select
                  id="mem-add-status"
                  className="viora-select"
                  value={addStatus}
                  onChange={(e) => setAddStatus(e.target.value as "active" | "invited")}
                  disabled={adding}
                >
                  <option value="active">Active</option>
                  <option value="invited">Invited (inactive until activated)</option>
                </select>
              </FormField>
            </div>
            <button type="submit" className="viora-btn viora-btn-primary" disabled={adding}>
              {adding ? "Associating..." : "Add member"}
            </button>
          </form>
        </SectionCard>
      )}

      <SectionCard
        title="Directory"
        actions={
          <select
            className="viora-select viora-select-inline"
            aria-label="Filter members by membership status"
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value as MembershipStatus | "all");
              list.resetToFirstPage();
            }}
          >
            <option value="all">All statuses</option>
            <option value="active">Active</option>
            <option value="invited">Invited</option>
            <option value="disabled">Disabled</option>
            <option value="revoked">Revoked</option>
          </select>
        }
      >
        {list.loading && <LoadingSpinner message="Loading institutional members..." />}
        {!list.loading && list.error && (
          <ErrorState
            title="Could not load members"
            message={`${list.error.message} (${list.error.code})`}
            onRetry={list.reload}
          />
        )}
        {!list.loading && !list.error && (
          <>
            <DataTable
              rows={list.items}
              rowKey={(m) => m.user_id}
              emptyTitle="No members found"
              emptyDescription="No institutional memberships match your filter."
              columns={[
                {
                  key: "name",
                  header: "Member",
                  render: (m) => (
                    <strong>
                      {m.display_name}
                      {auth.user?.user_id === m.user_id ? " (You)" : ""}
                    </strong>
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
                  key: "account",
                  header: "Global Account",
                  render: (m) => (
                    <StatusBadge tone={m.account_status === "active" ? "success" : "danger"}>
                      {m.account_status}
                    </StatusBadge>
                  ),
                },
                {
                  key: "roles",
                  header: "Roles",
                  render: (m) => (
                    <div className="viora-row" style={{ gap: "0.35rem" }}>
                      {m.roles.length === 0 ? (
                        <span className="viora-field-hint">No roles</span>
                      ) : (
                        m.roles.map((r) => (
                          <StatusBadge key={r} tone="brand">
                            {ROLE_LABELS[r] ?? r}
                          </StatusBadge>
                        ))
                      )}
                    </div>
                  ),
                },
                ...(flags.isAdmin
                  ? [
                      {
                        key: "actions",
                        header: "Actions",
                        render: (m: MemberSummary) => (
                          <div className="viora-row">
                            <button
                              type="button"
                              className="viora-btn viora-btn-sm"
                              onClick={() => {
                                setRoleModalMember(m);
                                const nextDefault =
                                  ALL_INSTITUTION_ROLES.find((r) => !m.roles.includes(r)) ??
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
                            {m.status !== "revoked" && (
                              <button
                                type="button"
                                className="viora-btn viora-btn-sm viora-btn-danger"
                                onClick={() =>
                                  setConfirmLifecycle({ member: m, nextStatus: "revoked" })
                                }
                              >
                                Revoke
                              </button>
                            )}
                          </div>
                        ),
                      },
                    ]
                  : []),
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

      <ModalDialog
        open={roleModalMember !== null}
        title={`Manage Roles — ${roleModalMember?.display_name ?? ""}`}
        description="Only institution owners can assign or revoke owner and administrator roles."
        onClose={() => setRoleModalMember(null)}
      >
        {roleModalMember && (
          <div className="viora-stack">
            <div className="viora-row">
              <span className="viora-field-hint">User ID:</span>
              <UuidChip value={roleModalMember.user_id} />
            </div>
            <div>
              <div className="viora-field-label" style={{ marginBottom: "0.4rem" }}>
                Assigned Roles
              </div>
              {roleModalMember.roles.length === 0 ? (
                <p className="viora-field-hint">This member currently holds no roles.</p>
              ) : (
                <div className="viora-row">
                  {roleModalMember.roles.map((r) => (
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
                        {ROLE_LABELS[r] ?? r}
                      </span>
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm viora-btn-danger"
                        disabled={busy}
                        onClick={() =>
                          setConfirmRevokeRole({ member: roleModalMember, role: r })
                        }
                      >
                        Revoke
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {availableRolesForModal.length > 0 && (
              <form
                onSubmit={handleGrantRole}
                className="viora-row"
                style={{ alignItems: "flex-end" }}
              >
                <div style={{ flex: 1 }}>
                  <FormField
                    label="Grant role"
                    htmlFor="role-grant-select"
                    hint={
                      roleModalMember.user_id === auth.user?.user_id
                        ? "Self-granting roles is prohibited by policy."
                        : !flags.isOwner
                          ? "Owner and Admin roles require Institution Owner permission."
                          : undefined
                    }
                  >
                    <select
                      id="role-grant-select"
                      className="viora-select"
                      value={roleToGrant}
                      onChange={(e) => setRoleToGrant(e.target.value as InstitutionRole)}
                      disabled={busy}
                    >
                      {availableRolesForModal.map((r) => (
                        <option key={r} value={r}>
                          {ROLE_LABELS[r]} ({r})
                        </option>
                      ))}
                    </select>
                  </FormField>
                </div>
                <button
                  type="submit"
                  className="viora-btn viora-btn-primary"
                  disabled={busy || roleModalMember.user_id === auth.user?.user_id}
                >
                  {busy ? "Granting..." : "Grant role"}
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
            ? `Change ${confirmLifecycle.member.display_name}'s membership from "${confirmLifecycle.member.status}" to "${confirmLifecycle.nextStatus}"? Removing or suspending an active owner is blocked if no other active owner remains.`
            : ""
        }
        confirmLabel="Update status"
        danger={confirmLifecycle?.nextStatus === "revoked" || confirmLifecycle?.nextStatus === "disabled"}
        busy={busy}
        onCancel={() => setConfirmLifecycle(null)}
        onConfirm={() => void handleLifecycleChange()}
      />

      <ConfirmDialog
        open={confirmRevokeRole !== null}
        title="Revoke institutional role?"
        message={
          confirmRevokeRole
            ? `Revoke "${ROLE_LABELS[confirmRevokeRole.role]}" from ${confirmRevokeRole.member.display_name}? Revoking the last active owner is denied.`
            : ""
        }
        confirmLabel="Revoke role"
        danger
        busy={busy}
        onCancel={() => setConfirmRevokeRole(null)}
        onConfirm={() => void handleConfirmRevokeRole()}
      />
    </div>
  );
}
