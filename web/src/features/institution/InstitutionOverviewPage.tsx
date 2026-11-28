import { useCallback, useEffect, useState, type FormEvent } from "react";
import type {
  InstitutionRole,
  SourceSummary,
  SubjectOfferingSummary,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import type { AppRouteId } from "../../auth/routeGuard";
import {
  AlertBanner,
  DataTable,
  FormField,
  PageHeader,
  PaginationBar,
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
import { useActionFeedback } from "../common/useActionFeedback";
import { useCursorList } from "../common/useCursorList";
import { OfferingQAPanel } from "../sources/OfferingQAPanel";
import { SourceKnowledgePanel } from "../sources/SourceKnowledgePanel";
import { buildSourceUploadFormData } from "../sources/sourceHelpers";

export function InstitutionOverviewPage({
  onNavigate,
}: {
  onNavigate: (route: AppRouteId) => void;
}) {
  const { apiClient, controller: authController, snapshot: auth } = useAuth();
  const { snapshot: tenant, controller: tenantController } = useTenant();
  const feedback = useActionFeedback();

  const selected = tenant.selectedInstitution;
  const tenantId = selected?.tenant_id ?? null;
  const flags = deriveRoleFlags(selected?.roles ?? []);

  const [offerings, setOfferings] = useState<SubjectOfferingSummary[]>([]);
  const [offeringsLoading, setOfferingsLoading] = useState(false);
  const [selectedOfferingId, setSelectedOfferingId] = useState("");
  const [selectedSource, setSelectedSource] = useState<SourceSummary | null>(null);

  const [noteMode, setNoteMode] = useState<"text" | "file">("text");
  const [noteTitle, setNoteTitle] = useState("");
  const [noteContent, setNoteContent] = useState("");
  const [noteFile, setNoteFile] = useState<File | null>(null);
  const [submittingNote, setSubmittingNote] = useState(false);

  useEffect(() => {
    setOfferings([]);
    setSelectedOfferingId("");
    setSelectedSource(null);
    if (
      !selected ||
      (!flags.isStudent && !flags.isSubjectTeacher && !flags.isClassCoordinator)
    ) {
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
          if (res.data.items.length > 0) {
            setSelectedOfferingId((prev) =>
              prev && res.data!.items.some((o) => o.id === prev)
                ? prev
                : res.data!.items[0].id,
            );
          }
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

  useEffect(() => {
    setSelectedSource(null);
  }, [selectedOfferingId]);

  const sourcesFetcher = useCallback(
    ({ cursor, limit }: { cursor?: string; limit: number }) =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/sources", {
        params: {
          path: { tenant_id: tenantId!, offering_id: selectedOfferingId },
          query: { cursor, limit },
        },
      }),
    [apiClient, selectedOfferingId, tenantId],
  );

  const sourcesList = useCursorList<SourceSummary>(
    tenantId && selectedOfferingId && flags.isStudent
      ? `${tenantId}:${selectedOfferingId}`
      : null,
    sourcesFetcher,
  );

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

  const activeOffering = offerings.find((o) => o.id === selectedOfferingId) ?? null;
  const teacherLessons = sourcesList.items.filter(
    (item) => item.origin_kind === "teacher_resource",
  );
  const personalNotes = sourcesList.items.filter(
    (item) => item.origin_kind === "student_submission",
  );

  const handleSubmitNote = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId || !selectedOfferingId) return;
    if (!noteTitle.trim()) {
      feedback.setValidationError("Title is required.");
      return;
    }
    if (noteMode === "file" && !noteFile) {
      feedback.setValidationError("Select a file to upload.");
      return;
    }

    await feedback.runApiAction({
      setBusy: setSubmittingNote,
      call: () =>
        noteMode === "text"
          ? apiClient.POST(
              "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/sources",
              {
                params: {
                  path: { tenant_id: tenantId, offering_id: selectedOfferingId },
                },
                body: {
                  title: noteTitle.trim(),
                  input_format: "typed_notes",
                  content: noteContent,
                  origin_kind: "student_submission",
                  visibility: "owner_private",
                },
              },
            )
          : apiClient.POST(
              "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/sources/files",
              {
                params: {
                  path: { tenant_id: tenantId, offering_id: selectedOfferingId },
                },
                body: buildSourceUploadFormData({
                  title: noteTitle,
                  file: noteFile!,
                  originKind: "student_submission",
                  visibility: "owner_private",
                }) as never,
              },
            ),
      fallbackError: "Unable to submit personal study notes.",
      onSuccess: (data) => {
        setNoteTitle("");
        setNoteContent("");
        setNoteFile(null);
        setSelectedSource(data.source);
        feedback.setActionSuccess(
          `Saved personal study note "${data.source.title}".`,
        );
        sourcesList.resetToFirstPage();
        sourcesList.reload();
      },
    });
  };

  return (
    <div className="viora-stack">
      <PageHeader
        title={selected.name}
        subtitle="Your enrolled subjects, published course materials, and subject-grounded Q&A."
        actions={
          flags.isStudent ? (
            <button
              type="button"
              className="viora-btn"
              onClick={() => onNavigate("student-learning")}
            >
              Open Personal Learning
            </button>
          ) : undefined
        }
      />

      {feedback.alerts}

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

      {flags.isStudent && (
        <SectionCard
          title="My Enrolled Subjects"
          subtitle="Select an enrolled subject to view published lessons and ask questions grounded in teacher materials."
        >
          {offeringsLoading ? (
            <LoadingSpinner message="Loading your enrolled subjects..." />
          ) : offerings.length === 0 ? (
            <AlertBanner
              tone="info"
              message="You are not enrolled in any active subject offerings yet. You can still use Personal Learning anytime for independent study."
            />
          ) : (
            <div className="viora-subject-grid">
              {offerings.map((o) => {
                const isSelected = o.id === selectedOfferingId;
                return (
                  <button
                    key={o.id}
                    type="button"
                    className={`viora-subject-card ${isSelected ? "is-selected" : ""}`}
                    onClick={() => setSelectedOfferingId(o.id)}
                  >
                    <strong style={{ color: "var(--color-brand-primary)" }}>
                      {o.name}
                    </strong>
                    <span className="viora-field-hint">
                      {isSelected ? "Selected subject" : "Select to view lessons & Q&A"}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </SectionCard>
      )}

      {flags.isStudent && tenantId && selectedOfferingId && (
        <>
          <SectionCard
            title={`Published Lessons — ${activeOffering?.name ?? "Subject"}`}
            subtitle="Teacher-published course materials and approved lesson guides."
          >
            {sourcesList.loading && <LoadingSpinner message="Loading subject materials..." />}
            {sourcesList.error && (
              <ErrorState
                title="Could not load subject materials"
                message={sourcesList.error.message}
                onRetry={sourcesList.reload}
              />
            )}
            {!sourcesList.loading && !sourcesList.error && (
              <>
                <DataTable
                  rows={teacherLessons}
                  rowKey={(s) => s.source_id}
                  emptyTitle="No published materials yet"
                  emptyDescription="Your teacher has not published materials for this subject yet."
                  columns={[
                    {
                      key: "title",
                      header: "Lesson / Material",
                      render: (s) => <strong>{s.title}</strong>,
                    },
                    {
                      key: "open",
                      header: "Action",
                      render: (s) => (
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm viora-btn-primary"
                          onClick={() => setSelectedSource(s)}
                        >
                          {selectedSource?.source_id === s.source_id
                            ? "Viewing"
                            : "Open Lesson"}
                        </button>
                      ),
                    },
                  ]}
                />
                <PaginationBar
                  limit={sourcesList.limit}
                  onLimitChange={sourcesList.setLimit}
                  hasPrevious={sourcesList.hasPrevious}
                  hasNext={sourcesList.hasNext}
                  onPrevious={sourcesList.goPrevious}
                  onNext={sourcesList.goNext}
                  count={sourcesList.items.length}
                />
              </>
            )}
          </SectionCard>

          {selectedSource && (
            <SourceKnowledgePanel
              tenantId={tenantId}
              source={selectedSource}
              isSubjectTeacher={false}
              policy={null}
              onSourceUpdated={sourcesList.reload}
            />
          )}

          <OfferingQAPanel
            tenantId={tenantId}
            offeringId={selectedOfferingId}
            offeringName={activeOffering?.name ?? ""}
            isSubjectTeacher={false}
          />

          <details className="viora-details">
            <summary>Personal Subject Notes (Advanced)</summary>
            <div className="viora-stack">
              {personalNotes.length > 0 && (
                <DataTable
                  rows={personalNotes}
                  rowKey={(s) => s.source_id}
                  columns={[
                    {
                      key: "title",
                      header: "My Note",
                      render: (s) => <strong>{s.title}</strong>,
                    },
                    {
                      key: "open",
                      header: "Inspect",
                      render: (s) => (
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm"
                          onClick={() => setSelectedSource(s)}
                        >
                          Open
                        </button>
                      ),
                    },
                  ]}
                />
              )}

              <form className="viora-stack" onSubmit={handleSubmitNote}>
                <div className="viora-row">
                  <button
                    type="button"
                    className={`viora-btn viora-btn-sm ${noteMode === "text" ? "viora-btn-primary" : ""}`}
                    onClick={() => setNoteMode("text")}
                  >
                    Typed Notes
                  </button>
                  <button
                    type="button"
                    className={`viora-btn viora-btn-sm ${noteMode === "file" ? "viora-btn-primary" : ""}`}
                    onClick={() => setNoteMode("file")}
                  >
                    PDF / Image Upload
                  </button>
                </div>
                <FormField label="Note title" htmlFor="inst-note-title">
                  <input
                    id="inst-note-title"
                    className="viora-input"
                    value={noteTitle}
                    onChange={(e) => setNoteTitle(e.target.value)}
                    placeholder="My revision summary"
                    required
                  />
                </FormField>
                {noteMode === "text" ? (
                  <FormField label="Notes" htmlFor="inst-note-content">
                    <textarea
                      id="inst-note-content"
                      className="viora-input"
                      rows={3}
                      value={noteContent}
                      onChange={(e) => setNoteContent(e.target.value)}
                      required
                    />
                  </FormField>
                ) : (
                  <FormField
                    label="Document File (PDF, PNG, JPEG)"
                    htmlFor="inst-note-file"
                  >
                    <input
                      id="inst-note-file"
                      type="file"
                      accept=".pdf,.png,.jpg,.jpeg"
                      onChange={(e) => setNoteFile(e.target.files?.[0] ?? null)}
                      required
                    />
                  </FormField>
                )}
                <div>
                  <button
                    type="submit"
                    className="viora-btn viora-btn-sm viora-btn-primary"
                    disabled={submittingNote}
                  >
                    {submittingNote ? "Saving..." : "Save Personal Note"}
                  </button>
                </div>
              </form>
            </div>
          </details>
        </>
      )}

      {!flags.isStudent && (flags.isSubjectTeacher || flags.isClassCoordinator) && (
        <SectionCard
          title="My Assigned Offerings"
          subtitle="Active subject offerings in your teaching or coordination scope."
        >
          {offeringsLoading ? (
            <LoadingSpinner message="Loading offerings..." />
          ) : (
            <DataTable
              rows={offerings}
              rowKey={(o) => o.id}
              emptyTitle="No active subjects"
              emptyDescription="No active subject offerings are assigned in this institution."
              columns={[
                {
                  key: "name",
                  header: "Subject Offering",
                  render: (o) => <strong>{o.name}</strong>,
                },
                {
                  key: "status",
                  header: "Status",
                  render: (o) => (
                    <StatusBadge tone={o.active ? "success" : "neutral"}>
                      {o.active ? "Active" : "Archived"}
                    </StatusBadge>
                  ),
                },
              ]}
            />
          )}
        </SectionCard>
      )}
    </div>
  );
}
