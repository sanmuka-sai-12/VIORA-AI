import { useCallback, useEffect, useState, type FormEvent } from "react";
import type {
  SourceSummary,
  SubjectOfferingSummary,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { ErrorState, LoadingSpinner } from "../../components/common/FeedbackStates";
import {
  AlertBanner,
  DataTable,
  FormField,
  PageHeader,
  PaginationBar,
  SectionCard,
} from "../../components/common/UiPrimitives";
import { useTenant } from "../../tenant/TenantContext";
import { useActionFeedback } from "../common/useActionFeedback";
import { useCursorList } from "../common/useCursorList";
import { OfferingQAPanel } from "../sources/OfferingQAPanel";
import { SourceKnowledgePanel } from "../sources/SourceKnowledgePanel";
import { buildSourceUploadFormData } from "../sources/sourceHelpers";

export function StudentLearningPage() {
  const { apiClient, controller: authController } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const feedback = useActionFeedback();

  const [offerings, setOfferings] = useState<SubjectOfferingSummary[]>([]);
  const [offeringsLoading, setOfferingsLoading] = useState(false);
  const [offeringId, setOfferingId] = useState("");
  const [selectedSource, setSelectedSource] = useState<SourceSummary | null>(null);

  const [noteMode, setNoteMode] = useState<"text" | "file">("text");
  const [noteTitle, setNoteTitle] = useState("");
  const [noteContent, setNoteContent] = useState("");
  const [noteFile, setNoteFile] = useState<File | null>(null);
  const [submittingNote, setSubmittingNote] = useState(false);

  useEffect(() => {
    if (!tenantId) return;
    let cancelled = false;
    setOfferingsLoading(true);
    void authController
      .runWithFreshToken(() =>
        apiClient.GET("/api/v1/tenants/{tenant_id}/offerings", {
          params: { path: { tenant_id: tenantId }, query: { limit: 100 } },
        }),
      )
      .then((res) => {
        if (!cancelled && res.data) {
          setOfferings(res.data.items);
          if (res.data.items.length > 0) {
            setOfferingId((prev) =>
              prev && res.data!.items.some((o) => o.id === prev)
                ? prev
                : res.data!.items[0].id,
            );
          } else {
            setOfferingId("");
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
  }, [apiClient, authController, tenantId]);

  useEffect(() => {
    setSelectedSource(null);
  }, [offeringId]);

  const fetcher = useCallback(
    ({ cursor, limit }: { cursor?: string; limit: number }) =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/sources", {
        params: {
          path: { tenant_id: tenantId!, offering_id: offeringId },
          query: { cursor, limit },
        },
      }),
    [apiClient, offeringId, tenantId],
  );

  const list = useCursorList<SourceSummary>(
    tenantId && offeringId ? `${tenantId}:${offeringId}` : null,
    fetcher,
  );

  const teacherLessons = list.items.filter(
    (item) => item.origin_kind === "teacher_resource",
  );
  const personalNotes = list.items.filter(
    (item) => item.origin_kind === "student_submission",
  );

  const activeOffering = offerings.find((o) => o.id === offeringId) ?? null;

  const handleSubmitNote = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId || !offeringId) return;
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
                params: { path: { tenant_id: tenantId, offering_id: offeringId } },
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
                params: { path: { tenant_id: tenantId, offering_id: offeringId } },
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
        feedback.setActionSuccess(`Saved personal study note "${data.source.title}".`);
        list.resetToFirstPage();
        list.reload();
      },
    });
  };

  if (!tenantId) return null;

  return (
    <div className="viora-stack">
      <PageHeader
        title="Personal Learning"
        subtitle={`Study published lessons and ask subject questions in ${tenant.selectedInstitution?.name ?? "your institution"}.`}
      />

      {feedback.alerts}

      {offeringsLoading ? (
        <LoadingSpinner message="Loading your enrolled subjects..." />
      ) : offerings.length === 0 ? (
        <SectionCard title="No Enrolled Subjects Yet">
          <AlertBanner
            tone="info"
            message="You are not enrolled in any active subject offerings yet. Check My Institution or contact your college administrator."
          />
        </SectionCard>
      ) : (
        <>
          <SectionCard
            title="My Subjects"
            subtitle="Choose a subject to read published lessons and ask questions."
          >
            <div className="viora-subject-grid">
              {offerings.map((o) => {
                const selected = o.id === offeringId;
                return (
                  <button
                    key={o.id}
                    type="button"
                    className={`viora-subject-card ${selected ? "is-selected" : ""}`}
                    onClick={() => setOfferingId(o.id)}
                  >
                    <strong style={{ color: "var(--color-brand-primary)" }}>
                      {o.name}
                    </strong>
                    <span className="viora-field-hint">
                      {selected ? "Selected subject" : "Click to study"}
                    </span>
                  </button>
                );
              })}
            </div>
          </SectionCard>

          {offeringId && (
            <>
              <SectionCard
                title={`Lessons — ${activeOffering?.name ?? "Subject"}`}
                subtitle="Teacher-approved learning materials and lesson guides."
              >
                {list.loading && <LoadingSpinner message="Loading lessons..." />}
                {list.error && (
                  <ErrorState
                    title="Could not load subject materials"
                    message={list.error.message}
                    onRetry={list.reload}
                  />
                )}
                {!list.loading && !list.error && (
                  <>
                    <DataTable
                      rows={teacherLessons}
                      rowKey={(s) => s.source_id}
                      emptyTitle="No lessons published yet"
                      emptyDescription="Your teacher has not published learning materials for this subject yet."
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

              {selectedSource && (
                <SourceKnowledgePanel
                  tenantId={tenantId}
                  source={selectedSource}
                  isSubjectTeacher={false}
                  policy={null}
                  onSourceUpdated={list.reload}
                />
              )}

              <OfferingQAPanel
                tenantId={tenantId}
                offeringId={offeringId}
                offeringName={activeOffering?.name ?? ""}
                isSubjectTeacher={false}
              />

              <details className="viora-details">
                <summary>Personal Study Notes (Advanced)</summary>
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
                    <FormField label="Note title" htmlFor="student-note-title">
                      <input
                        id="student-note-title"
                        className="viora-input"
                        value={noteTitle}
                        onChange={(e) => setNoteTitle(e.target.value)}
                        placeholder="My revision summary"
                        required
                      />
                    </FormField>
                    {noteMode === "text" ? (
                      <FormField label="Notes" htmlFor="student-note-content">
                        <textarea
                          id="student-note-content"
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
                        htmlFor="student-note-file"
                      >
                        <input
                          id="student-note-file"
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
        </>
      )}
    </div>
  );
}
