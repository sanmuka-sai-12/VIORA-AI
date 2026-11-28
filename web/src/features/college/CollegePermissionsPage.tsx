import { useEffect, useState, type FormEvent } from "react";
import {
  normalizeApiError,
  type InstitutionInfo,
  type NormalizedApiError,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { LoadingSpinner } from "../../components/common/FeedbackStates";
import {
  ActiveBadge,
  AlertBanner,
  FormField,
  PageHeader,
  SectionCard,
} from "../../components/common/UiPrimitives";
import { deriveRoleFlags } from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { validateRequiredName } from "../../tenant/validation";
import { GuardiansPage } from "../guardians/GuardiansPage";
import { MembershipsPage } from "../memberships/MembershipsPage";

export function CollegePermissionsPage() {
  const { apiClient, controller: authController } = useAuth();
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
  const [showGuardians, setShowGuardians] = useState(false);

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
        if (cancelled) return;
        if (res.data) {
          setDetails(res.data);
          setEditName(res.data.name);
        } else {
          setDetailsError(
            normalizeApiError(
              res.error,
              res.response.status,
              "Unable to fetch institution settings.",
            ),
          );
        }
      })
      .finally(() => {
        if (!cancelled) setDetailsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [apiClient, authController, flags.isAdmin, selected?.tenant_id]);

  const handleSaveMetadata = async (e: FormEvent) => {
    e.preventDefault();
    if (!selected) return;
    setSaveMessage(null);
    setSaveError(null);

    const problem = validateRequiredName(editName, "Institution name", 160);
    if (problem) {
      setSaveError({ status: 422, code: "validation_error", message: problem });
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
        tenantController.updateInstitutionNameLocal(selected.tenant_id, res.data.name);
        setSaveMessage("Institution name updated.");
      } else {
        setSaveError(
          normalizeApiError(
            res.error,
            res.response.status,
            "Failed to update institution name.",
          ),
        );
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="viora-stack">
      <PageHeader
        title="Permissions & Governance"
        subtitle="Manage institutional members, role permissions, and college settings."
        actions={details ? <ActiveBadge active={details.active} /> : undefined}
      />

      <MembershipsPage embedded />

      {flags.isAdmin && selected && (
        <SectionCard
          title="Institution Settings"
          subtitle="Update your institution's display name."
        >
          {detailsLoading && <LoadingSpinner message="Loading institution settings..." />}
          {detailsError && (
            <AlertBanner
              tone="error"
              title="Could not load institution settings"
              error={detailsError}
            />
          )}
          {!detailsLoading && !detailsError && (
            <form
              onSubmit={handleSaveMetadata}
              className="viora-row"
              style={{ alignItems: "flex-end", maxWidth: "540px" }}
            >
              <div style={{ flex: 1, minWidth: "240px" }}>
                <FormField label="Institution name" htmlFor="perm-inst-name">
                  <input
                    id="perm-inst-name"
                    type="text"
                    className="viora-input"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    disabled={saving}
                    required
                  />
                </FormField>
              </div>
              <button
                type="submit"
                className="viora-btn viora-btn-primary"
                disabled={saving}
              >
                {saving ? "Saving..." : "Save name"}
              </button>
            </form>
          )}
          {saveMessage && (
            <div style={{ marginTop: "0.75rem" }}>
              <AlertBanner tone="success" message={saveMessage} />
            </div>
          )}
          {saveError && (
            <div style={{ marginTop: "0.75rem" }}>
              <AlertBanner tone="error" title="Update rejected" error={saveError} />
            </div>
          )}
        </SectionCard>
      )}

      <details
        className="viora-details"
        onToggle={(e) =>
          setShowGuardians((e.currentTarget as HTMLDetailsElement).open)
        }
      >
        <summary>Advanced: Guardian Relationship Administration</summary>
        {showGuardians && <GuardiansPage embedded />}
      </details>
    </div>
  );
}
