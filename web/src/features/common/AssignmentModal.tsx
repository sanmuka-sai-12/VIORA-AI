import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  normalizeApiError,
  type InstitutionRole,
  type MemberSummary,
  type NormalizedApiError,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { validateRequiredUuid } from "../../tenant/validation";
import {
  ActiveBadge,
  AlertBanner,
  ConfirmDialog,
  DataTable,
  FormField,
  ModalDialog,
} from "../../components/common/UiPrimitives";
import { LoadingSpinner } from "../../components/common/FeedbackStates";
import { resolveMemberLabel } from "./useTenantOptions";

export interface AssignmentRow {
  user_id: string;
  active: boolean;
}

export interface AssignmentModalProps {
  open: boolean;
  title: string;
  subtitle: string;
  canManage: boolean;
  eligibleRoleFilter?: InstitutionRole;
  assignActionLabel: string;
  revokeActionLabel: string;
  members: readonly MemberSummary[];
  onClose: () => void;
  listFn: () => Promise<{
    data?: { items: AssignmentRow[] };
    error?: unknown;
    response: Response;
  }>;
  assignFn: (userId: string) => Promise<{
    data?: unknown;
    error?: unknown;
    response: Response;
  }>;
  revokeFn: (userId: string) => Promise<{
    data?: unknown;
    error?: unknown;
    response: Response;
  }>;
}

export function AssignmentModal({
  open,
  title,
  subtitle,
  canManage,
  eligibleRoleFilter,
  assignActionLabel,
  revokeActionLabel,
  members,
  onClose,
  listFn,
  assignFn,
  revokeFn,
}: AssignmentModalProps) {
  const { controller: authController } = useAuth();
  const [rows, setRows] = useState<AssignmentRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<NormalizedApiError | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const [selectedUserId, setSelectedUserId] = useState("");
  const [manualUserId, setManualUserId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [confirmTarget, setConfirmTarget] = useState<string | null>(null);

  const eligibleMembers = members.filter(
    (m) =>
      m.status === "active" &&
      m.account_status === "active" &&
      (!eligibleRoleFilter || m.roles.includes(eligibleRoleFilter)),
  );

  const loadRows = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authController.runWithFreshToken(listFn);
      if (res.data) {
        setRows(res.data.items);
      } else {
        setError(
          normalizeApiError(
            res.error,
            res.response.status,
            "Failed to load assignments.",
          ),
        );
      }
    } catch {
      setError({
        status: 0,
        code: "network_error",
        message: "Unable to communicate with backend service.",
      });
    } finally {
      setLoading(false);
    }
  }, [authController, listFn]);

  useEffect(() => {
    if (!open) {
      return;
    }
    setSuccessMsg(null);
    setSelectedUserId("");
    setManualUserId("");
    setConfirmTarget(null);
    void loadRows();
  }, [open, loadRows]);

  const handleAssign = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccessMsg(null);

    const targetId = manualUserId.trim() || selectedUserId;
    const problem = validateRequiredUuid(targetId, "User ID");
    if (problem) {
      setError({ status: 422, code: "validation_error", message: problem });
      return;
    }

    setSubmitting(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        assignFn(targetId),
      );
      if (res.data) {
        setSuccessMsg("Assignment updated successfully.");
        setSelectedUserId("");
        setManualUserId("");
        await loadRows();
      } else {
        setError(
          normalizeApiError(
            res.error,
            res.response.status,
            "Failed to create assignment.",
          ),
        );
      }
    } catch {
      setError({
        status: 0,
        code: "network_error",
        message: "Unable to communicate with backend service.",
      });
    } finally {
      setSubmitting(false);
    }
  };

  const handleRevoke = async () => {
    if (!confirmTarget) {
      return;
    }
    const targetId = confirmTarget;
    setSubmitting(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const res = await authController.runWithFreshToken(() =>
        revokeFn(targetId),
      );
      if (res.data) {
        setConfirmTarget(null);
        setSuccessMsg("Assignment revoked.");
        await loadRows();
      } else {
        setConfirmTarget(null);
        setError(
          normalizeApiError(
            res.error,
            res.response.status,
            "Failed to revoke assignment.",
          ),
        );
      }
    } catch {
      setConfirmTarget(null);
      setError({
        status: 0,
        code: "network_error",
        message: "Unable to communicate with backend service.",
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <ModalDialog
        open={open}
        title={title}
        description={subtitle}
        onClose={onClose}
      >
        <div className="viora-stack">
          {successMsg && <AlertBanner tone="success" message={successMsg} />}
          {error && (
            <AlertBanner
              tone="error"
              title="Assignment request failed"
              error={error}
            />
          )}

          {canManage && (
            <form
              onSubmit={handleAssign}
              className="viora-stack"
              style={{
                padding: "0.85rem",
                background: "var(--color-bg-subtle)",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--color-border)",
              }}
            >
              {eligibleMembers.length > 0 ? (
                <FormField
                  label={
                    eligibleRoleFilter
                      ? `Eligible active member (${eligibleRoleFilter})`
                      : "Active institutional member"
                  }
                  htmlFor="assignment-member-select"
                >
                  <select
                    id="assignment-member-select"
                    className="viora-select"
                    value={selectedUserId}
                    onChange={(e) => {
                      setSelectedUserId(e.target.value);
                      if (e.target.value) setManualUserId("");
                    }}
                    disabled={submitting}
                    required
                  >
                    <option value="">Select an eligible member...</option>
                    {eligibleMembers.map((m) => (
                      <option key={m.user_id} value={m.user_id}>
                        {m.display_name}
                      </option>
                    ))}
                  </select>
                </FormField>
              ) : (
                <p className="viora-muted" style={{ margin: 0, fontSize: "0.85rem" }}>
                  No eligible active members found with the required role.
                </p>
              )}

              <div>
                <button
                  type="submit"
                  className="viora-btn viora-btn-primary viora-btn-sm"
                  disabled={submitting || eligibleMembers.length === 0}
                >
                  {submitting ? "Submitting..." : assignActionLabel}
                </button>
              </div>
            </form>
          )}

          {loading ? (
            <LoadingSpinner message="Loading assignments..." />
          ) : (
            <DataTable
              rows={rows}
              rowKey={(row) => row.user_id}
              emptyTitle="No assignments recorded"
              emptyDescription="No users have been assigned to this resource yet."
              columns={[
                {
                  key: "member",
                  header: "User",
                  render: (row) => {
                    const label = resolveMemberLabel(members, row.user_id);
                    return <strong>{label ?? "Institutional Member"}</strong>;
                  },
                },
                {
                  key: "status",
                  header: "Status",
                  render: (row) => <ActiveBadge active={row.active} />,
                },
                ...(canManage
                  ? [
                      {
                        key: "actions",
                        header: "Actions",
                        render: (row: AssignmentRow) =>
                          row.active ? (
                            <button
                              type="button"
                              className="viora-btn viora-btn-sm viora-btn-danger"
                              disabled={submitting}
                              onClick={() => setConfirmTarget(row.user_id)}
                            >
                              {revokeActionLabel}
                            </button>
                          ) : (
                            <span className="viora-field-hint">Revoked</span>
                          ),
                      },
                    ]
                  : []),
              ]}
            />
          )}
        </div>
      </ModalDialog>

      <ConfirmDialog
        open={confirmTarget !== null}
        title={`${revokeActionLabel}?`}
        message="This immediately revokes the active assignment for this user in the current institution."
        confirmLabel={revokeActionLabel}
        danger
        busy={submitting}
        onCancel={() => setConfirmTarget(null)}
        onConfirm={() => void handleRevoke()}
      />
    </>
  );
}
