import { useCallback, useEffect, useState } from "react";
import type { SectionSummary, SubjectOfferingSummary } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { ErrorState, LoadingSpinner } from "../../components/common/FeedbackStates";
import {
  ActiveBadge,
  DataTable,
  FormField,
  ModalDialog,
  PageHeader,
  SectionCard,
} from "../../components/common/UiPrimitives";
import { useTenant } from "../../tenant/TenantContext";
import { validateRequiredName } from "../../tenant/validation";
import { AssignmentModal } from "../common/AssignmentModal";
import { useActionFeedback } from "../common/useActionFeedback";
import { useCursorList } from "../common/useCursorList";
import { useTenantOptions } from "../common/useTenantOptions";

export function TeacherClassPage() {
  const { apiClient, controller: authController } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const feedback = useActionFeedback();
  const options = useTenantOptions({ members: true });

  const [selectedSection, setSelectedSection] = useState<SectionSummary | null>(null);
  const [sectionOfferings, setSectionOfferings] = useState<SubjectOfferingSummary[]>([]);
  const [offeringsLoading, setOfferingsLoading] = useState(false);

  const [editingSection, setEditingSection] = useState<SectionSummary | null>(null);
  const [editName, setEditName] = useState("");
  const [saving, setSaving] = useState(false);

  const [coordsSection, setCoordsSection] = useState<SectionSummary | null>(null);
  const [teachersOffering, setTeachersOffering] = useState<SubjectOfferingSummary | null>(null);

  const fetcher = useCallback(
    ({ cursor, limit }: { cursor?: string; limit: number }) =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/sections", {
        params: {
          path: { tenant_id: tenantId! },
          query: { cursor, limit },
        },
      }),
    [apiClient, tenantId],
  );

  const list = useCursorList<SectionSummary>(tenantId, fetcher);

  useEffect(() => {
    if (list.items.length > 0 && !selectedSection) {
      setSelectedSection(list.items[0]);
    } else if (list.items.length === 0) {
      setSelectedSection(null);
    }
  }, [list.items, selectedSection]);

  useEffect(() => {
    if (!tenantId || !selectedSection) {
      setSectionOfferings([]);
      return;
    }
    let cancelled = false;
    setOfferingsLoading(true);
    void authController
      .runWithFreshToken(() =>
        apiClient.GET("/api/v1/tenants/{tenant_id}/offerings", {
          params: {
            path: { tenant_id: tenantId },
            query: { section_id: selectedSection.id, limit: 100 },
          },
        }),
      )
      .then((res) => {
        if (!cancelled && res.data) {
          setSectionOfferings(res.data.items);
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
  }, [apiClient, authController, selectedSection, tenantId]);

  const handleRename = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tenantId || !editingSection) return;
    const err = validateRequiredName(editName, "Class section name", 160);
    if (err) {
      feedback.setValidationError(err);
      return;
    }
    await feedback.runApiAction({
      setBusy: setSaving,
      call: () =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/sections/{section_id}", {
          params: { path: { tenant_id: tenantId, section_id: editingSection.id } },
          body: { name: editName.trim() },
        }),
      fallbackError: "Unable to rename class section.",
      onSuccess: (updated) => {
        feedback.setActionSuccess(`Updated section name to "${updated.name}".`);
        if (selectedSection?.id === updated.id) {
          setSelectedSection(updated);
        }
        setEditingSection(null);
        list.reload();
      },
    });
  };

  if (!tenantId) return null;

  return (
    <div className="viora-stack">
      <PageHeader
        title="My Class"
        subtitle="Class sections assigned to your coordination responsibility and their subject offerings."
      />

      {feedback.alerts}

      <SectionCard title="Coordinated Class Sections">
        {list.loading && <LoadingSpinner message="Loading your coordinated classes..." />}
        {!list.loading && list.error && (
          <ErrorState
            title="Could not load coordinated classes"
            message={list.error.message}
            onRetry={list.reload}
          />
        )}
        {!list.loading && !list.error && (
          <DataTable
            rows={list.items}
            rowKey={(s) => s.id}
            emptyTitle="No coordinated classes"
            emptyDescription="You are not currently assigned as coordinator for any active class section."
            columns={[
              {
                key: "name",
                header: "Class Section",
                render: (s) => <strong>{s.name}</strong>,
              },
              {
                key: "status",
                header: "Status",
                render: (s) => <ActiveBadge active={s.active} />,
              },
              {
                key: "actions",
                header: "Actions",
                render: (s) => (
                  <div className="viora-row">
                    <button
                      type="button"
                      className={`viora-btn viora-btn-sm ${selectedSection?.id === s.id ? "viora-btn-primary" : ""}`}
                      onClick={() => setSelectedSection(s)}
                    >
                      {selectedSection?.id === s.id ? "Selected" : "View Subjects"}
                    </button>
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      onClick={() => setCoordsSection(s)}
                    >
                      Coordinators
                    </button>
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      onClick={() => {
                        setEditName(s.name);
                        setEditingSection(s);
                      }}
                    >
                      Rename
                    </button>
                  </div>
                ),
              },
            ]}
          />
        )}
      </SectionCard>

      {selectedSection && (
        <SectionCard
          title={`Subject Offerings — ${selectedSection.name}`}
          subtitle="Subjects taught in this class section and their assigned teachers."
        >
          {offeringsLoading ? (
            <LoadingSpinner message="Loading class subject offerings..." />
          ) : (
            <DataTable
              rows={sectionOfferings}
              rowKey={(o) => o.id}
              emptyTitle="No offerings in this class"
              emptyDescription="No subject offerings have been created for this class section yet."
              columns={[
                { key: "name", header: "Subject Offering", render: (o) => <strong>{o.name}</strong> },
                {
                  key: "status",
                  header: "Status",
                  render: (o) => <ActiveBadge active={o.active} />,
                },
                {
                  key: "teachers",
                  header: "Teachers",
                  render: (o) => (
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      onClick={() => setTeachersOffering(o)}
                    >
                      View Teachers
                    </button>
                  ),
                },
              ]}
            />
          )}
        </SectionCard>
      )}

      <ModalDialog
        open={editingSection !== null}
        title="Rename Class Section"
        onClose={() => setEditingSection(null)}
      >
        <form onSubmit={(e) => void handleRename(e)} className="viora-stack">
          <FormField label="Section name" htmlFor="coord-sec-name">
            <input
              id="coord-sec-name"
              className="viora-input"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              disabled={saving}
              required
            />
          </FormField>
          <div className="viora-row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="viora-btn" onClick={() => setEditingSection(null)}>
              Cancel
            </button>
            <button type="submit" className="viora-btn viora-btn-primary" disabled={saving}>
              {saving ? "Saving..." : "Save changes"}
            </button>
          </div>
        </form>
      </ModalDialog>

      {tenantId && coordsSection && (
        <AssignmentModal
          open
          title={`Class Coordinators — ${coordsSection.name}`}
          subtitle="Assigned coordinators for this class section."
          canManage={false}
          eligibleRoleFilter="class_coordinator"
          assignActionLabel="Assign"
          revokeActionLabel="Revoke"
          members={options.members}
          onClose={() => setCoordsSection(null)}
          listFn={() =>
            apiClient.GET("/api/v1/tenants/{tenant_id}/sections/{section_id}/coordinators", {
              params: {
                path: { tenant_id: tenantId, section_id: coordsSection.id },
                query: { limit: 100 },
              },
            })
          }
          assignFn={async () => ({ response: new Response(null, { status: 403 }) })}
          revokeFn={async () => ({ response: new Response(null, { status: 403 }) })}
        />
      )}

      {tenantId && teachersOffering && (
        <AssignmentModal
          open
          title={`Assigned Teachers — ${teachersOffering.name}`}
          subtitle="Subject teachers assigned to this offering."
          canManage={false}
          eligibleRoleFilter="subject_teacher"
          assignActionLabel="Assign"
          revokeActionLabel="Revoke"
          members={options.members}
          onClose={() => setTeachersOffering(null)}
          listFn={() =>
            apiClient.GET("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/teachers", {
              params: {
                path: { tenant_id: tenantId, offering_id: teachersOffering.id },
                query: { limit: 100 },
              },
            })
          }
          assignFn={async () => ({ response: new Response(null, { status: 403 }) })}
          revokeFn={async () => ({ response: new Response(null, { status: 403 }) })}
        />
      )}
    </div>
  );
}
