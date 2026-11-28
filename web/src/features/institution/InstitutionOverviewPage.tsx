import { useEffect, useState } from "react";
import type { InstitutionRole, SubjectOfferingSummary } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import type { AppRouteId } from "../../auth/routeGuard";
import {
  DataTable,
  PageHeader,
  SectionCard,
  StatusBadge,
  UuidChip,
} from "../../components/common/UiPrimitives";
import { ErrorState, LoadingSpinner } from "../../components/common/FeedbackStates";
import {
  defaultRouteForRoles,
  deriveRoleFlags,
  ROLE_LABELS,
} from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";

export function InstitutionOverviewPage({
  onNavigate,
}: {
  onNavigate: (route: AppRouteId) => void;
}) {
  const { apiClient, controller: authController, snapshot: auth } = useAuth();
  const { snapshot: tenant, controller: tenantController } = useTenant();

  const selected = tenant.selectedInstitution;
  const flags = deriveRoleFlags(selected?.roles ?? []);

  const [offerings, setOfferings] = useState<SubjectOfferingSummary[]>([]);
  const [offeringsLoading, setOfferingsLoading] = useState(false);

  useEffect(() => {
    setOfferings([]);
    if (!selected || (!flags.isStudent && !flags.isSubjectTeacher && !flags.isClassCoordinator)) {
      return;
    }

    let cancelled = false;
    setOfferingsLoading(true);

    void authController
      .runWithFreshToken(() =>
        apiClient.GET("/api/v1/tenants/{tenant_id}/offerings", {
          params: { path: { tenant_id: selected.tenant_id }, query: { limit: 100 } },
        }),
      )
      .then((res) => {
        if (!cancelled && res.data) {
          setOfferings(res.data.items);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setOfferingsLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [
    apiClient,
    authController,
    flags.isClassCoordinator,
    flags.isStudent,
    flags.isSubjectTeacher,
    selected,
  ]);

  if (tenant.status === "loading" || tenant.status === "idle") {
    return <LoadingSpinner message="Loading your institutions..." />;
  }

  if (tenant.status === "error") {
    return (
      <ErrorState
        title="Unable to load institutions"
        message={tenant.error?.message}
        onRetry={() => void tenantController.discover()}
      />
    );
  }

  if (tenant.institutions.length === 0) {
    return (
      <div className="viora-stack">
        <PageHeader
          title="No Active Institutional Memberships"
          subtitle="Your account is signed in, but it is not yet associated with an institution."
        />
        <SectionCard title="Next Step">
          <div className="viora-stack">
            <p style={{ fontSize: "0.9rem" }}>
              Contact your college administrator to add you to an institution.
            </p>
            {auth.user && (
              <details className="viora-details">
                <summary>Share User ID with your administrator</summary>
                <div className="viora-row">
                  <strong>User ID:</strong>
                  <UuidChip value={auth.user.user_id} />
                </div>
              </details>
            )}
            <div>
              <button
                type="button"
                className="viora-btn"
                onClick={() => void tenantController.discover()}
              >
                Refresh memberships
              </button>
            </div>
          </div>
        </SectionCard>
      </div>
    );
  }

  if (!selected) {
    return (
      <div className="viora-stack">
        <PageHeader
          title="Select an Institution"
          subtitle="Choose an institution to open your workspace."
        />
        <div className="viora-grid-2">
          {tenant.institutions.map((inst) => (
            <SectionCard
              key={inst.tenant_id}
              title={inst.name}
              actions={
                <button
                  type="button"
                  className="viora-btn viora-btn-primary viora-btn-sm"
                  onClick={() => {
                    if (tenantController.selectInstitution(inst.tenant_id)) {
                      onNavigate(defaultRouteForRoles(inst.roles, true));
                    }
                  }}
                >
                  Open workspace
                </button>
              }
            >
              <div className="viora-row">
                {inst.roles.length === 0 ? (
                  <StatusBadge tone="neutral">Member</StatusBadge>
                ) : (
                  inst.roles.map((r) => (
                    <StatusBadge key={r} tone="brand">
                      {ROLE_LABELS[r as InstitutionRole] ?? r}
                    </StatusBadge>
                  ))
                )}
              </div>
            </SectionCard>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="viora-stack">
      <PageHeader
        title={selected.name}
        subtitle="My Institution workspace and enrolled academic context."
        actions={
          flags.isStudent ? (
            <button
              type="button"
              className="viora-btn viora-btn-primary"
              onClick={() => onNavigate("student-learning")}
            >
              Open Personal Learning
            </button>
          ) : undefined
        }
      />

      <SectionCard title="Membership">
        <div className="viora-grid-2">
          <div>
            <div className="viora-field-hint">Institution</div>
            <strong>{selected.name}</strong>
          </div>
          <div>
            <div className="viora-field-hint">Your Roles</div>
            <div className="viora-row" style={{ marginTop: "0.25rem" }}>
              {selected.roles.length === 0 ? (
                <StatusBadge tone="neutral">Member (no assigned role)</StatusBadge>
              ) : (
                selected.roles.map((r) => (
                  <StatusBadge key={r} tone="brand">
                    {ROLE_LABELS[r as InstitutionRole] ?? r}
                  </StatusBadge>
                ))
              )}
            </div>
          </div>
        </div>
      </SectionCard>

      {(flags.isStudent || flags.isSubjectTeacher || flags.isClassCoordinator) && (
        <SectionCard
          title={flags.isStudent ? "My Enrolled Subjects" : "My Assigned Offerings"}
          subtitle={
            flags.isStudent
              ? "Active subject offerings you are enrolled in at this institution."
              : "Active subject offerings in your teaching or coordination scope."
          }
        >
          {offeringsLoading ? (
            <LoadingSpinner message="Loading subjects..." />
          ) : (
            <DataTable
              rows={offerings}
              rowKey={(o) => o.id}
              emptyTitle="No active subjects"
              emptyDescription="You are not enrolled in or assigned to any active subject offerings in this institution yet."
              columns={[
                { key: "name", header: "Subject Offering", render: (o) => <strong>{o.name}</strong> },
                {
                  key: "status",
                  header: "Status",
                  render: (o) => (
                    <StatusBadge tone={o.active ? "success" : "neutral"}>
                      {o.active ? "Active" : "Archived"}
                    </StatusBadge>
                  ),
                },
                ...(flags.isStudent
                  ? [
                      {
                        key: "action",
                        header: "Learning",
                        render: () => (
                          <button
                            type="button"
                            className="viora-btn viora-btn-sm viora-btn-primary"
                            onClick={() => onNavigate("student-learning")}
                          >
                            Study
                          </button>
                        ),
                      },
                    ]
                  : []),
              ]}
            />
          )}
        </SectionCard>
      )}
    </div>
  );
}
