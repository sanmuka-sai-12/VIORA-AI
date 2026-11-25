import { useEffect, useState, type FormEvent } from "react";
import {
  normalizeApiError,
  type InstitutionInfo,
  type InstitutionRole,
  type NormalizedApiError,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import type { AppRouteId } from "../../auth/routeGuard";
import {
  ActiveBadge,
  AlertBanner,
  FormField,
  PageHeader,
  SectionCard,
  StatusBadge,
  UuidChip,
} from "../../components/common/UiPrimitives";
import { ErrorState, LoadingSpinner } from "../../components/common/FeedbackStates";
import { deriveRoleFlags, ROLE_LABELS } from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { validateRequiredName } from "../../tenant/validation";

export function InstitutionOverviewPage({
  onNavigate,
}: {
  onNavigate: (route: AppRouteId) => void;
}) {
  const { apiClient, controller: authController, snapshot: auth } = useAuth();
  const { snapshot: tenant, controller: tenantController } = useTenant();

  const selected = tenant.selectedInstitution;
  const flags = deriveRoleFlags(selected?.roles ?? []);

  const [details, setDetails] = useState<InstitutionInfo | null>(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState<NormalizedApiError | null>(null);

  const [editName, setEditName] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<NormalizedApiError | null>(null);

  useEffect(() => {
    setDetails(null);
    setDetailsError(null);
    setSaveMessage(null);
    setSaveError(null);

    if (!selected || !flags.isAdmin) {
      setEditName(selected?.name ?? "");
      return;
    }

    setEditName(selected.name);
    let cancelled = false;
    setDetailsLoading(true);

    void authController
      .runWithFreshToken(() =>
        apiClient.GET("/api/v1/institutions/{tenant_id}", {
          params: { path: { tenant_id: selected.tenant_id } },
        }),
      )
      .then((res) => {
        if (cancelled) {
          return;
        }
        if (res.data) {
          setDetails(res.data);
          setEditName(res.data.name);
        } else {
          setDetailsError(
            normalizeApiError(
              res.error,
              res.response.status,
              "Unable to fetch institution metadata.",
            ),
          );
        }
      })
      .catch(() => {
        if (!cancelled) {
          setDetailsError({
            status: 0,
            code: "network_error",
            message: "Unable to communicate with backend service.",
          });
        }
      })
      .finally(() => {
        if (!cancelled) {
          setDetailsLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [apiClient, authController, flags.isAdmin, selected?.tenant_id]);

  if (tenant.status === "loading" || tenant.status === "idle") {
    return <LoadingSpinner message="Discovering your active institutions..." />;
  }

  if (tenant.status === "error") {
    return (
      <ErrorState
        title="Unable to discover institutions"
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
          subtitle="Your global identity is verified, but you have no active memberships in any institution."
        />
        <SectionCard title="Next Step">
          <div className="viora-stack">
            <p style={{ fontSize: "0.9rem" }}>
              Share your global User ID below with an institutional administrator or
              operator so they can add your membership and assign a role.
            </p>
            {auth.user && (
              <div className="viora-row">
                <strong>Your User ID:</strong>
                <UuidChip value={auth.user.user_id} />
              </div>
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
          subtitle="Your account holds active memberships in multiple institutions. Choose one to enter its isolated workspace."
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
                  onClick={() => tenantController.selectInstitution(inst.tenant_id)}
                >
                  Open workspace
                </button>
              }
            >
              <div className="viora-stack">
                <div className="viora-row">
                  <span className="viora-field-hint">Tenant ID:</span>
                  <UuidChip value={inst.tenant_id} />
                </div>
                <div className="viora-row">
                  {inst.roles.length === 0 ? (
                    <StatusBadge tone="neutral">No role hints</StatusBadge>
                  ) : (
                    inst.roles.map((r) => (
                      <StatusBadge key={r} tone="brand">
                        {ROLE_LABELS[r as InstitutionRole] ?? r}
                      </StatusBadge>
                    ))
                  )}
                </div>
              </div>
            </SectionCard>
          ))}
        </div>
      </div>
    );
  }

  const handleSaveMetadata = async (e: FormEvent) => {
    e.preventDefault();
    setSaveMessage(null);
    setSaveError(null);

    const problem = validateRequiredName(editName, "Institution name", 160);
    if (problem) {
      setSaveError({
        status: 422,
        code: "validation_error",
        message: problem,
      });
      return;
    }

    setSaving(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.PATCH("/api/v1/institutions/{tenant_id}", {
          params: { path: { tenant_id: selected.tenant_id } },
          body: { name: editName.trim() },
        }),
      );
      if (res.data) {
        setDetails(res.data);
        setEditName(res.data.name);
        tenantController.updateInstitutionNameLocal(
          selected.tenant_id,
          res.data.name,
        );
        setSaveMessage("Institution metadata saved.");
      } else {
        setSaveError(
          normalizeApiError(
            res.error,
            res.response.status,
            "Failed to update institution metadata.",
          ),
        );
      }
    } catch {
      setSaveError({
        status: 0,
        code: "network_error",
        message: "Unable to communicate with backend service.",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="viora-stack">
      <PageHeader
        title={details?.name ?? selected.name}
        subtitle="Institutional context and role-scoped navigation."
        actions={
          details ? <ActiveBadge active={details.active} /> : undefined
        }
      />

      <SectionCard
        title="Workspace Summary"
        subtitle="Discovery roles below are navigation hints only; all endpoint permissions and RLS tenant isolation are enforced by the backend."
      >
        <div className="viora-grid-2">
          <div>
            <div className="viora-field-hint">Tenant UUID</div>
            <UuidChip value={selected.tenant_id} />
          </div>
          <div>
            <div className="viora-field-hint">Your User UUID</div>
            {auth.user && <UuidChip value={auth.user.user_id} />}
          </div>
          <div>
            <div className="viora-field-hint">Assigned Roles in Institution</div>
            <div className="viora-row" style={{ marginTop: "0.25rem" }}>
              {selected.roles.length === 0 ? (
                <StatusBadge tone="neutral">Member (no active role)</StatusBadge>
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

      {flags.isAdmin && (
        <SectionCard
          title="Institution Metadata"
          subtitle="Update the institution's display name."
        >
          {detailsLoading && <LoadingSpinner message="Loading institution record..." />}
          {detailsError && (
            <AlertBanner
              tone="error"
              title="Could not load institution record"
              error={detailsError}
            />
          )}
          {!detailsLoading && !detailsError && (
            <form
              onSubmit={handleSaveMetadata}
              className="viora-stack"
              style={{ maxWidth: "460px" }}
            >
              {saveMessage && (
                <AlertBanner tone="success" message={saveMessage} />
              )}
              {saveError && (
                <AlertBanner
                  tone="error"
                  title="Update rejected"
                  error={saveError}
                />
              )}
              <FormField label="Institution name" htmlFor="inst-name-input">
                <input
                  id="inst-name-input"
                  type="text"
                  className="viora-input"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  disabled={saving}
                  required
                />
              </FormField>
              <div>
                <button
                  type="submit"
                  className="viora-btn viora-btn-primary"
                  disabled={saving}
                >
                  {saving ? "Saving..." : "Save metadata"}
                </button>
              </div>
            </form>
          )}
        </SectionCard>
      )}

      <SectionCard
        title="Available Actions for Your Roles"
        subtitle="Jump directly to permitted workflows for this institution."
      >
        <div className="viora-row">
          {flags.isAdmin && (
            <>
              <button
                type="button"
                className="viora-btn viora-btn-primary"
                onClick={() => onNavigate("memberships")}
              >
                Manage Members & Roles
              </button>
              <button
                type="button"
                className="viora-btn"
                onClick={() => onNavigate("academics-campuses")}
              >
                Manage Campuses
              </button>
              <button
                type="button"
                className="viora-btn"
                onClick={() => onNavigate("academics-departments")}
              >
                Manage Departments
              </button>
              <button
                type="button"
                className="viora-btn"
                onClick={() => onNavigate("academics-offerings")}
              >
                Manage Offerings & Enrolments
              </button>
              <button
                type="button"
                className="viora-btn"
                onClick={() => onNavigate("guardians")}
              >
                Guardian Links
              </button>
            </>
          )}

          {!flags.isAdmin && flags.isDepartmentHead && (
            <>
              <button
                type="button"
                className="viora-btn viora-btn-primary"
                onClick={() => onNavigate("academics-departments")}
              >
                View My Departments
              </button>
              <button
                type="button"
                className="viora-btn"
                onClick={() => onNavigate("academics-programmes")}
              >
                View Programmes
              </button>
              <button
                type="button"
                className="viora-btn"
                onClick={() => onNavigate("academics-subjects")}
              >
                View Subjects
              </button>
            </>
          )}

          {!flags.isAdmin && flags.isClassCoordinator && (
            <button
              type="button"
              className="viora-btn viora-btn-primary"
              onClick={() => onNavigate("academics-sections")}
            >
              View Coordinated Sections
            </button>
          )}

          {!flags.isAdmin &&
            (flags.isSubjectTeacher || flags.isStudent || flags.isClassCoordinator) && (
              <button
                type="button"
                className="viora-btn viora-btn-primary"
                onClick={() => onNavigate("academics-offerings")}
              >
                {flags.isStudent
                  ? "View My Enrolled Offerings"
                  : "View Assigned Offerings"}
              </button>
            )}

          {!flags.isAdmin && (flags.isStudent || flags.isGuardian) && (
            <button
              type="button"
              className="viora-btn"
              onClick={() => onNavigate("guardians")}
            >
              {flags.isGuardian
                ? "View Linked Student Summaries"
                : "Manage Guardian Consent"}
            </button>
          )}
        </div>
      </SectionCard>
    </div>
  );
}
