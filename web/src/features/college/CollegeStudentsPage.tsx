import { useState, type FormEvent } from "react";
import type { MemberSummary, MembershipStatus } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { ErrorState, LoadingSpinner } from "../../components/common/FeedbackStates";
import {
  ConfirmDialog,
  DataTable,
  FormField,
  PageHeader,
  SectionCard,
  StatusBadge,
} from "../../components/common/UiPrimitives";
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

export function CollegeStudentsPage() {
  const { apiClient } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const feedback = useActionFeedback();
  const directory = useAllMembers(tenantId);

  const [statusFilter, setStatusFilter] = useState<MembershipStatus | "all">("all");
  const [search, setSearch] = useState("");
  const [addUserId, setAddUserId] = useState("");
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);

  const [confirmLifecycle, setConfirmLifecycle] = useState<{
    member: MemberSummary;
    nextStatus: "active" | "disabled" | "revoked";
  } | null>(null);

  const students = directory.members.filter((m) => {
    if (!m.roles.includes("student")) return false;
    if (statusFilter !== "all" && m.status !== statusFilter) return false;
    if (
      search.trim() &&
      !m.display_name.toLowerCase().includes(search.trim().toLowerCase())
    ) {
      return false;
    }
    return true;
  });

  const handleAddStudent = async (e: FormEvent) => {
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
        fallbackError: "Unable to add student membership.",
        onSuccess: () => undefined,
      });
      if (!created) return;
    }

    await feedback.runApiAction({
      setBusy: setAdding,
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/memberships/{user_id}/roles", {
          params: { path: { tenant_id: tenantId, user_id: targetId } },
          body: { role: "student" },
        }),
      fallbackError: "Unable to assign Student role.",
      onSuccess: (data) => {
        setAddUserId("");
        feedback.setActionSuccess(`Added "${data.display_name}" as a Student.`);
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
      fallbackError: "Unable to update student membership status.",
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

  return (
    <div className="viora-stack">
      <PageHeader
        title="Students"
        subtitle={`Student directory and membership management for ${tenant.selectedInstitution?.name ?? "your institution"}.`}
      />

      {feedback.alerts}

      <SectionCard
        title="Add Student"
        subtitle="Associate an existing verified user account with this institution and assign the Student role."
      >
        <form
          onSubmit={handleAddStudent}
          className="viora-row"
          style={{ alignItems: "flex-end" }}
        >
          <div style={{ flex: 2, minWidth: "260px" }}>
            <FormField label="Verified User ID" htmlFor="head-student-uuid">
              <input
                id="head-student-uuid"
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
          <button
            type="submit"
            className="viora-btn viora-btn-primary"
            disabled={adding}
          >
            {adding ? "Adding..." : "Add Student"}
          </button>
        </form>
      </SectionCard>

      <SectionCard
        title={`Student Directory (${students.length})`}
        actions={
          <div className="viora-row">
            <input
              type="search"
              className="viora-input"
              style={{ width: "200px" }}
              placeholder="Search by name..."
              aria-label="Search students by name"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
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
        {directory.loading && <LoadingSpinner message="Loading students..." />}
        {!directory.loading && directory.error && (
          <ErrorState
            title="Could not load students"
            message={directory.error.message}
            onRetry={directory.reload}
          />
        )}
        {!directory.loading && !directory.error && (
          <DataTable
            rows={students}
            rowKey={(m) => m.user_id}
            emptyTitle="No students found"
            emptyDescription="No students match your current filter."
            columns={[
              {
                key: "name",
                header: "Student",
                render: (m) => <strong>{m.display_name}</strong>,
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
                header: "Account",
                render: (m) => (
                  <StatusBadge
                    tone={m.account_status === "active" ? "success" : "danger"}
                  >
                    {m.account_status}
                  </StatusBadge>
                ),
              },
              {
                key: "actions",
                header: "Actions",
                render: (m) => (
                  <div className="viora-row">
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
            ]}
          />
        )}
      </SectionCard>

      <ConfirmDialog
        open={confirmLifecycle !== null}
        title={`Set student membership status to "${confirmLifecycle?.nextStatus ?? ""}"?`}
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
