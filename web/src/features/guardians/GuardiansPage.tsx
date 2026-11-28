import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  normalizeApiError,
  type GuardianLinkSummary,
  type NormalizedApiError,
  type StudentSummary,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import {
  ActiveBadge,
  AlertBanner,
  ConfirmDialog,
  DataTable,
  FormField,
  PageHeader,
  PaginationBar,
  SectionCard,
  StatusBadge,
  UuidChip,
} from "../../components/common/UiPrimitives";
import { ErrorState, LoadingSpinner } from "../../components/common/FeedbackStates";
import { deriveRoleFlags } from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { validateRequiredUuid } from "../../tenant/validation";
import { useActionFeedback } from "../common/useActionFeedback";
import { useCursorList } from "../common/useCursorList";
import { resolveMemberLabel, useTenantOptions } from "../common/useTenantOptions";

export function GuardiansPage({ embedded = false }: { embedded?: boolean }) {
  const { apiClient, controller: authController, snapshot: auth } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);

  const options = useTenantOptions({ members: true });
  const feedback = useActionFeedback();

  const [activeFilter, setActiveFilter] = useState<"all" | "active" | "inactive">("all");
  const [guardianId, setGuardianId] = useState("");
  const [studentId, setStudentId] = useState("");
  const [creating, setCreating] = useState(false);

  const [confirmConsent, setConfirmConsent] = useState<GuardianLinkSummary | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState<GuardianLinkSummary | null>(null);
  const [busy, setBusy] = useState(false);

  const [studentSummary, setStudentSummary] = useState<StudentSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<NormalizedApiError | null>(null);

  const fetcher = useCallback(
    ({ cursor, limit }: { cursor?: string; limit: number }) =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/guardian-links", {
        params: {
          path: { tenant_id: tenantId! },
          query: {
            active: activeFilter === "all" ? undefined : activeFilter === "active",
            cursor,
            limit,
          },
        },
      }),
    [activeFilter, apiClient, tenantId],
  );

  const list = useCursorList<GuardianLinkSummary>(tenantId, fetcher);

  const loadStudentSummary = useCallback(
    async (targetStudentId: string) => {
      if (!tenantId) return;
      setSummaryLoading(true);
      setSummaryError(null);
      try {
        const res = await authController.runWithFreshToken(() =>
          apiClient.GET("/api/v1/tenants/{tenant_id}/students/{student_id}/summary", {
            params: { path: { tenant_id: tenantId, student_id: targetStudentId } },
          }),
        );
        if (res.data) {
          setStudentSummary(res.data);
        } else {
          setStudentSummary(null);
          setSummaryError(
            normalizeApiError(
              res.error,
              res.response.status,
              "Access to student summary was denied or unavailable.",
            ),
          );
        }
      } catch {
        setSummaryError({
          status: 0,
          code: "network_error",
          message: "Unable to load student summary.",
        });
      } finally {
        setSummaryLoading(false);
      }
    },
    [apiClient, authController, tenantId],
  );

  useEffect(() => {
    setStudentSummary(null);
    setSummaryError(null);
    if (tenantId && flags.isStudent && auth.user?.user_id) {
      void loadStudentSummary(auth.user.user_id);
    }
  }, [auth.user?.user_id, flags.isStudent, loadStudentSummary, tenantId]);

  const guardians = options.members.filter(
    (m) => m.status === "active" && m.roles.includes("guardian"),
  );
  const students = options.members.filter(
    (m) => m.status === "active" && m.roles.includes("student"),
  );

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId) return;
    const gErr = validateRequiredUuid(guardianId, "Guardian User ID");
    const sErr = validateRequiredUuid(studentId, "Student User ID");
    if (gErr || sErr) {
      feedback.setValidationError(gErr ?? sErr ?? "Both Guardian and Student UUIDs are required.");
      return;
    }
    await feedback.runApiAction({
      setBusy: setCreating,
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/guardian-links", {
          params: { path: { tenant_id: tenantId } },
          body: { guardian_user_id: guardianId.trim(), student_user_id: studentId.trim() },
        }),
      fallbackError: "Unable to create guardian link.",
      onSuccess: () => {
        setGuardianId("");
        setStudentId("");
        feedback.setActionSuccess(
          "Inactive guardian link created. Verification and activation remain explicitly deferred on the backend.",
        );
        list.resetToFirstPage();
        list.reload();
      },
    });
  };

  const handleConsent = async () => {
    if (!tenantId || !confirmConsent) return;
    const target = confirmConsent;
    await feedback.runApiAction({
      setBusy,
      call: () =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/guardian-links/{guardian_user_id}/students/{student_user_id}/consent",
          {
            params: {
              path: {
                tenant_id: tenantId,
                guardian_user_id: target.guardian_user_id,
                student_user_id: target.student_user_id,
              },
            },
          },
        ),
      fallbackError: "Unable to record consent.",
      onSuccess: () => {
        feedback.setActionSuccess("Student consent timestamp recorded on guardian link.");
        list.reload();
      },
      onFinally: () => setConfirmConsent(null),
    });
  };

  const handleRevoke = async () => {
    if (!tenantId || !confirmRevoke) return;
    const target = confirmRevoke;
    await feedback.runApiAction({
      setBusy,
      call: () =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/guardian-links/{guardian_user_id}/students/{student_user_id}/revoke",
          {
            params: {
              path: {
                tenant_id: tenantId,
                guardian_user_id: target.guardian_user_id,
                student_user_id: target.student_user_id,
              },
            },
          },
        ),
      fallbackError: "Unable to revoke guardian link.",
      onSuccess: () => {
        setStudentSummary(null);
        feedback.setActionSuccess("Guardian link revoked; subsequent guardian summary access is denied.");
        list.reload();
      },
      onFinally: () => setConfirmRevoke(null),
    });
  };

  return (
    <div className="viora-stack">
      {!embedded && (
        <PageHeader
          title="Guardian Relationships"
          subtitle="Manage guardian links, student consent, and permitted student summaries."
        />
      )}

      {feedback.alerts}

      {flags.isAdmin && (
        <SectionCard
          title="Create Inactive Guardian Link"
          subtitle="Both Guardian and Student must hold active memberships with their respective roles."
        >
          <form onSubmit={handleCreate} className="viora-grid-2" style={{ alignItems: "end" }}>
            <FormField label="Guardian member or UUID" htmlFor="g-link-guardian">
              {guardians.length > 0 ? (
                <select
                  id="g-link-guardian"
                  className="viora-select"
                  value={guardianId}
                  onChange={(e) => setGuardianId(e.target.value)}
                  disabled={creating}
                  required
                >
                  <option value="">Select active guardian...</option>
                  {guardians.map((g) => (
                    <option key={g.user_id} value={g.user_id}>
                      {g.display_name} ({g.user_id.slice(0, 8)}...)
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  id="g-link-guardian"
                  type="text"
                  className="viora-input viora-mono"
                  placeholder="Guardian UUID"
                  value={guardianId}
                  onChange={(e) => setGuardianId(e.target.value)}
                  disabled={creating}
                  required
                />
              )}
            </FormField>

            <FormField label="Student member or UUID" htmlFor="g-link-student">
              {students.length > 0 ? (
                <select
                  id="g-link-student"
                  className="viora-select"
                  value={studentId}
                  onChange={(e) => setStudentId(e.target.value)}
                  disabled={creating}
                  required
                >
                  <option value="">Select active student...</option>
                  {students.map((s) => (
                    <option key={s.user_id} value={s.user_id}>
                      {s.display_name} ({s.user_id.slice(0, 8)}...)
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  id="g-link-student"
                  type="text"
                  className="viora-input viora-mono"
                  placeholder="Student UUID"
                  value={studentId}
                  onChange={(e) => setStudentId(e.target.value)}
                  disabled={creating}
                  required
                />
              )}
            </FormField>

            <div>
              <button type="submit" className="viora-btn viora-btn-primary" disabled={creating}>
                {creating ? "Creating..." : "Create inactive link"}
              </button>
            </div>
          </form>
        </SectionCard>
      )}

      <SectionCard
        title="Guardian Links"
        actions={
          <select
            className="viora-select viora-select-inline"
            aria-label="Filter guardian links"
            value={activeFilter}
            onChange={(e) => {
              setActiveFilter(e.target.value as "all" | "active" | "inactive");
              list.resetToFirstPage();
            }}
          >
            <option value="all">All links</option>
            <option value="active">Active only</option>
            <option value="inactive">Inactive only</option>
          </select>
        }
      >
        {list.loading && <LoadingSpinner message="Loading guardian links..." />}
        {!list.loading && list.error && (
          <ErrorState
            title="Could not load guardian links"
            message={`${list.error.message} (${list.error.code})`}
            onRetry={list.reload}
          />
        )}
        {!list.loading && !list.error && (
          <>
            <DataTable
              rows={list.items}
              rowKey={(g) => `${g.guardian_user_id}:${g.student_user_id}`}
              emptyTitle="No guardian relationships found"
              emptyDescription="No guardian links exist in your current view."
              columns={[
                {
                  key: "guardian",
                  header: "Guardian",
                  render: (g) => {
                    const label = resolveMemberLabel(options.members, g.guardian_user_id);
                    return (
                      <div className="viora-stack" style={{ gap: "0.2rem" }}>
                        {label && <strong>{label}</strong>}
                        <UuidChip value={g.guardian_user_id} />
                      </div>
                    );
                  },
                },
                {
                  key: "student",
                  header: "Student",
                  render: (g) => {
                    const label = resolveMemberLabel(options.members, g.student_user_id);
                    return (
                      <div className="viora-stack" style={{ gap: "0.2rem" }}>
                        {label && <strong>{label}</strong>}
                        <UuidChip value={g.student_user_id} />
                      </div>
                    );
                  },
                },
                {
                  key: "verification",
                  header: "Verification & Consent",
                  render: (g) => (
                    <div className="viora-row">
                      <StatusBadge tone={g.verified_at ? "success" : "neutral"}>
                        {g.verified_at ? "Verified" : "Unverified"}
                      </StatusBadge>
                      <StatusBadge tone={g.consent_at ? "success" : "warning"}>
                        {g.consent_at ? "Consented" : "Consent pending"}
                      </StatusBadge>
                    </div>
                  ),
                },
                {
                  key: "active",
                  header: "Link State",
                  render: (g) => <ActiveBadge active={g.active} />,
                },
                {
                  key: "actions",
                  header: "Actions",
                  render: (g) => {
                    const isSelfStudent =
                      flags.isStudent && auth.user?.user_id === g.student_user_id;
                    const isSelfGuardian =
                      flags.isGuardian && auth.user?.user_id === g.guardian_user_id;
                    return (
                      <div className="viora-row">
                        {isSelfStudent && !g.consent_at && (
                          <button
                            type="button"
                            className="viora-btn viora-btn-sm viora-btn-primary"
                            onClick={() => setConfirmConsent(g)}
                          >
                            Record Consent
                          </button>
                        )}
                        {isSelfGuardian && (
                          <button
                            type="button"
                            className="viora-btn viora-btn-sm"
                            onClick={() => void loadStudentSummary(g.student_user_id)}
                          >
                            Read Summary
                          </button>
                        )}
                        {(flags.isAdmin || isSelfStudent || isSelfGuardian) && (
                          <button
                            type="button"
                            className="viora-btn viora-btn-sm viora-btn-danger"
                            onClick={() => setConfirmRevoke(g)}
                          >
                            Revoke
                          </button>
                        )}
                      </div>
                    );
                  },
                },
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

      {(flags.isStudent || flags.isGuardian) && (
        <SectionCard
          title="Minimal Student Summary"
          subtitle="Restricted identity projection returned by /students/{student_id}/summary when authorized."
        >
          {summaryLoading && <LoadingSpinner message="Loading permitted student summary..." />}
          {!summaryLoading && summaryError && (
            <AlertBanner tone="warning" title="Summary unavailable" error={summaryError} />
          )}
          {!summaryLoading && !summaryError && studentSummary && (
            <div className="viora-grid-2">
              <div>
                <div className="viora-field-hint">Student Display Name</div>
                <strong>{studentSummary.display_name}</strong>
              </div>
              <div>
                <div className="viora-field-hint">Student UUID</div>
                <UuidChip value={studentSummary.user_id} />
              </div>
            </div>
          )}
          {!summaryLoading && !summaryError && !studentSummary && flags.isGuardian && (
            <p className="viora-field-hint">
              Select &ldquo;Read Summary&rdquo; on an active guardian link above to load the
              permitted minimal student summary. Inactive or revoked links fail closed (HTTP 403).
            </p>
          )}
        </SectionCard>
      )}

      <ConfirmDialog
        open={confirmConsent !== null}
        title="Record student consent?"
        message="This records your consent timestamp on this guardian relationship. The link remains inactive until verified and activated once that workflow is enabled."
        confirmLabel="Record consent"
        busy={busy}
        onCancel={() => setConfirmConsent(null)}
        onConfirm={() => void handleConsent()}
      />

      <ConfirmDialog
        open={confirmRevoke !== null}
        title="Revoke guardian relationship?"
        message="Revoking clears consent/activation and immediately denies guardian access to student summaries."
        confirmLabel="Revoke link"
        danger
        busy={busy}
        onCancel={() => setConfirmRevoke(null)}
        onConfirm={() => void handleRevoke()}
      />
    </div>
  );
}
