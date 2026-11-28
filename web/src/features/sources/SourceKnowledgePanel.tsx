import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  normalizeApiError,
  type ConceptExtractionRecord,
  type DerivedExtractionRecord,
  type KnowledgeIndexStatus,
  type KnowledgePublicationState,
  type KnowledgeReviewRecord,
  type LessonBlueprintApprovalState,
  type LessonBlueprintRecord,
  type LessonBlueprintSummary,
  type OfferingSourcePolicyInfo,
  type PublishedLessonBlueprint,
  type PublishedLessonPackage,
  type SourceSummary,
  type SourceVersionSummary,
  type WorkflowJobSummary,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { LoadingSpinner } from "../../components/common/FeedbackStates";
import {
  AlertBanner,
  DataTable,
  FormField,
  SectionCard,
  StatusBadge,
} from "../../components/common/UiPrimitives";
import { useActionFeedback, type ApiResultLike } from "../common/useActionFeedback";
import { LessonBlueprintPreview } from "./LessonBlueprintPreview";
import {
  canApproveLessonBlueprint,
  canOwnerStudentProcess,
  canTeacherManageSource,
  canTriggerLessonBlueprint,
  groundingTone,
  jobStateTone,
  reviewTone,
} from "./sourceHelpers";

export function SourceKnowledgePanel({
  tenantId,
  source,
  isSubjectTeacher,
  policy,
  onSourceUpdated,
}: {
  tenantId: string;
  source: SourceSummary;
  isSubjectTeacher: boolean;
  policy: OfferingSourcePolicyInfo | null;
  onSourceUpdated: () => void;
}) {
  const { snapshot: auth, apiClient, controller: authController } = useAuth();
  const actorUserId = auth.user?.user_id;
  const { setActionError, setActionSuccess, clearFeedback, runApiAction, alerts } = useActionFeedback();

  const [versions, setVersions] = useState<SourceVersionSummary[]>([]);
  const [selectedVersionId, setSelectedVersionId] = useState(source.latest_version_id ?? "");
  const [jobs, setJobs] = useState<WorkflowJobSummary[]>([]);
  const [derived, setDerived] = useState<DerivedExtractionRecord | null>(null);
  const [concepts, setConcepts] = useState<ConceptExtractionRecord | null>(null);
  const [pubState, setPubState] = useState<KnowledgePublicationState | null>(null);
  const [reviews, setReviews] = useState<KnowledgeReviewRecord[]>([]);
  const [indexStatus, setIndexStatus] = useState<KnowledgeIndexStatus | null>(null);
  const [bpRecord, setBpRecord] = useState<LessonBlueprintRecord | null>(null);
  const [bpVersions, setBpVersions] = useState<LessonBlueprintSummary[]>([]);
  const [bpApproval, setBpApproval] = useState<LessonBlueprintApprovalState | null>(null);
  const [studentPkg, setStudentPkg] = useState<PublishedLessonPackage | null>(null);
  const [publishedBlueprint, setPublishedBlueprint] = useState<PublishedLessonBlueprint | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  const [decision, setDecision] = useState<"approved" | "changes_requested" | "rejected">("approved");
  const [reviewNotes, setReviewNotes] = useState("");
  const [ackTopicRequest, setAckTopicRequest] = useState(false);
  const [bpDecision, setBpDecision] = useState<"approved" | "changes_requested" | "rejected">("approved");
  const [bpReviewNotes, setBpReviewNotes] = useState("");

  const isTeacherManageable = canTeacherManageSource(isSubjectTeacher, source);
  const isOwnerStudent = canOwnerStudentProcess(actorUserId, source, policy);
  const isPersonalStudentNote =
    source.origin_kind === "student_submission" && source.owner_user_id === actorUserId;
  const canInspectDraft = isSubjectTeacher || isPersonalStudentNote;

  useEffect(() => {
    setSelectedVersionId(source.latest_version_id ?? "");
  }, [source.source_id, source.latest_version_id]);

  const loadWorkspace = useCallback(async (preserveFeedback = false) => {
    setLoading(true);
    if (!preserveFeedback) {
      clearFeedback();
    }
    const freshGet = <T,>(call: () => Promise<ApiResultLike<T>>) => authController.runWithFreshToken(call);
    const sourcePath = { tenant_id: tenantId, source_id: source.source_id };
    try {
      const [verRes, jobRes] = await Promise.all([
        freshGet(() => apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions", { params: { path: sourcePath } })),
        canInspectDraft
          ? freshGet(() => apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/jobs", { params: { path: sourcePath } }))
          : Promise.resolve({ data: undefined, response: new Response(null, { status: 200 }) } as ApiResultLike<{ items: WorkflowJobSummary[] }>),
      ]);
      if (verRes.data) setVersions(verRes.data.items);
      if (jobRes.data) setJobs(jobRes.data.items);
      else setJobs([]);

      const verId = selectedVersionId;
      if (!verId) return;
      const versionPath = { ...sourcePath, version_id: verId };

      if (canInspectDraft) {
        const [extRes, conRes, outRes, revRes, idxRes, bpRes, bpVerRes, bpAppRes] = await Promise.all([
          freshGet(() => apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/extraction", { params: { path: versionPath } })),
          freshGet(() => apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts", { params: { path: versionPath } })),
          freshGet(() => apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts/outline", { params: { path: versionPath } })),
          freshGet(() => apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts/reviews", { params: { path: versionPath } })),
          freshGet(() => apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/index", { params: { path: versionPath } })),
          freshGet(() => apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/blueprint", { params: { path: versionPath } })),
          freshGet(() => apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/blueprint/versions", { params: { path: versionPath } })),
          freshGet(() => apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/blueprint/approval", { params: { path: versionPath } })),
        ]);
        const hardFail = [extRes, conRes, outRes, revRes, idxRes, bpRes, bpVerRes, bpAppRes].find(
          (r) => r.data === undefined && r.response.status !== 404,
        );
        if (hardFail) {
          setActionError(normalizeApiError(hardFail.error, hardFail.response.status, "Failed to load material details."));
        }
        setDerived(extRes.data ?? null);
        setConcepts(conRes.data ?? null);
        setPubState(outRes.data ?? null);
        setReviews(revRes.data?.items ?? []);
        setIndexStatus(idxRes.data ?? null);
        setBpRecord(bpRes.data ?? null);
        setBpVersions(bpVerRes.data?.items ?? []);
        setBpApproval(bpAppRes.data ?? null);
        setStudentPkg(null);
        setPublishedBlueprint(null);
      } else {
        const [pubRes, bpPubRes] = await Promise.all([
          freshGet(() =>
            apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts/published", { params: { path: versionPath } }),
          ),
          freshGet(() =>
            apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/blueprint/published", { params: { path: versionPath } }),
          ),
        ]);
        const serverErr = [pubRes, bpPubRes].find((r) => r.response.status >= 500);
        if (serverErr) {
          setActionError(normalizeApiError(serverErr.error, serverErr.response.status, "Failed to load published lesson."));
        }
        setStudentPkg(pubRes.data ?? null);
        setPublishedBlueprint(bpPubRes.data ?? null);
        setDerived(null);
        setConcepts(null);
        setPubState(null);
        setReviews([]);
        setIndexStatus(null);
        setBpRecord(null);
        setBpVersions([]);
        setBpApproval(null);
      }
    } catch {
      setActionError({ status: 0, code: "network_error", message: "Failed to load material details." });
    } finally {
      setLoading(false);
    }
  }, [apiClient, authController, canInspectDraft, clearFeedback, selectedVersionId, setActionError, source.source_id, tenantId]);

  useEffect(() => {
    void loadWorkspace();
  }, [loadWorkspace]);

  const runAction = (
    label: string,
    call: () => Promise<ApiResultLike<unknown>>,
    successMsg: string,
    refreshSourceList = false,
  ) => {
    void runApiAction({
      call,
      fallbackError: `${label} failed.`,
      setBusy,
      onSuccess: () => {
        setActionSuccess(successMsg);
        if (refreshSourceList) onSourceUpdated();
        void loadWorkspace(true);
      },
    });
  };

  const handleReviewSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!concepts || !selectedVersionId) return;
    void runAction(
      "Review concepts",
      () =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts/review",
          {
            params: {
              path: {
                tenant_id: tenantId,
                source_id: source.source_id,
                version_id: selectedVersionId,
              },
            },
            body: {
              concept_extraction_id: concepts.concept_extraction_id,
              decision,
              review_notes: reviewNotes.trim() || null,
              approved_concept_ids: [],
              rejected_concept_ids: [],
              acknowledge_learning_request: ackTopicRequest,
            },
          },
        ),
      `Saved lesson outline review (${decision}).`,
    );
  };

  const activeKnowledge = concepts?.knowledge ?? studentPkg?.knowledge ?? null;
  const activeOutline = pubState?.outline ?? studentPkg?.outline ?? null;

  const processingControlsBlock = (isTeacherManageable || isOwnerStudent || canInspectDraft) && (
    <details className="viora-details">
      <summary>Advanced Resource Processing &amp; Pipeline</summary>
      <div className="viora-stack">
        <div className="viora-row">
          {versions.length > 0 && (
            <select
              aria-label="Source version"
              className="viora-select"
              style={{ width: "auto" }}
              value={selectedVersionId}
              onChange={(e) => setSelectedVersionId(e.target.value)}
            >
              {versions.map((v) => (
                <option key={v.version_id} value={v.version_id}>
                  v{v.version_number} ({v.processing_status})
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            onClick={() => void loadWorkspace(true)}
          >
            Refresh Status
          </button>
          {(isTeacherManageable || isOwnerStudent) && (
            <>
              <button
                type="button"
                className="viora-btn viora-btn-sm"
                disabled={busy}
                onClick={() =>
                  void runAction(
                    "Queue document extraction",
                    () =>
                      apiClient.POST(
                        "/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/extract",
                        { params: { path: { tenant_id: tenantId, source_id: source.source_id, version_id: selectedVersionId } } },
                      ),
                    "Document extraction job queued.",
                  )
                }
              >
                Extract Text
              </button>
              <button
                type="button"
                className="viora-btn viora-btn-sm viora-btn-primary"
                disabled={busy || !derived}
                onClick={() =>
                  void runAction(
                    "Queue concept extraction",
                    () =>
                      apiClient.POST(
                        "/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts/extract",
                        { params: { path: { tenant_id: tenantId, source_id: source.source_id, version_id: selectedVersionId } } },
                      ),
                    "Concept extraction job queued.",
                  )
                }
              >
                Extract Concepts
              </button>
            </>
          )}
          {isTeacherManageable && (
            <>
              <button
                type="button"
                className="viora-btn viora-btn-sm"
                disabled={busy || !derived}
                onClick={() =>
                  void runAction(
                    "Queue knowledge indexing",
                    () =>
                      apiClient.POST(
                        "/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/index",
                        { params: { path: { tenant_id: tenantId, source_id: source.source_id, version_id: selectedVersionId } } },
                      ),
                    "Knowledge indexing job queued.",
                  )
                }
              >
                Index for Q&amp;A
              </button>
              <button
                type="button"
                className="viora-btn viora-btn-sm viora-btn-primary"
                disabled={busy || !canTriggerLessonBlueprint(isSubjectTeacher, source, pubState)}
                onClick={() =>
                  void runAction(
                    "Queue lesson blueprint generation",
                    () =>
                      apiClient.POST(
                        "/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/blueprint/generate",
                        { params: { path: { tenant_id: tenantId, source_id: source.source_id, version_id: selectedVersionId } } },
                      ),
                    "Lesson blueprint generation job queued.",
                  )
                }
              >
                Generate Blueprint
              </button>
            </>
          )}
        </div>

        {derived && (
          <p style={{ fontSize: "0.84rem", color: "var(--color-text-muted)" }}>
            Extracted Document: {derived.document.pages.length} page(s),{" "}
            {derived.document.evidence_references.length} evidence spans, status:{" "}
            {derived.document.quality.overall_status}
          </p>
        )}

        {indexStatus && (
          <div className="viora-row">
            <StatusBadge tone={indexStatus.indexed ? "success" : "neutral"}>
              {indexStatus.indexed
                ? `Indexed v${indexStatus.index_version ?? 1} (${indexStatus.chunk_count} chunk(s))`
                : "Not Indexed for Q&A"}
            </StatusBadge>
            {indexStatus.embedding_model && (
              <span style={{ fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
                Embedder: <code>{indexStatus.embedding_model}</code>
              </span>
            )}
          </div>
        )}

        <DataTable
          rows={jobs}
          rowKey={(j) => j.job_id}
          emptyTitle="No processing jobs yet"
          emptyDescription="Run text extraction, concept extraction, indexing, or blueprint generation above."
          columns={[
            { key: "op", header: "Operation", render: (j) => <code>{j.operation_kind}</code> },
            {
              key: "state",
              header: "State",
              render: (j) => <StatusBadge tone={jobStateTone(j.state)}>{j.state}</StatusBadge>,
            },
            { key: "stage", header: "Stage", render: (j) => j.stage ?? "—" },
            {
              key: "actions",
              header: "Job Control",
              render: (j) =>
                (isTeacherManageable || isOwnerStudent) ? (
                  <div className="viora-row">
                    {(j.state === "queued" || j.state === "running" || j.state === "retry_wait") && (
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm"
                        onClick={() =>
                          void runAction(
                            "Cancel job",
                            () =>
                              apiClient.POST("/api/v1/tenants/{tenant_id}/jobs/{job_id}/cancel", {
                                params: { path: { tenant_id: tenantId, job_id: j.job_id } },
                              }),
                            "Cancellation requested.",
                          )
                        }
                      >
                        Cancel
                      </button>
                    )}
                    {(j.state === "failed" || j.state === "cancelled") && (
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm"
                        onClick={() =>
                          void runAction(
                            "Retry job",
                            () =>
                              apiClient.POST("/api/v1/tenants/{tenant_id}/jobs/{job_id}/retry", {
                                params: { path: { tenant_id: tenantId, job_id: j.job_id } },
                              }),
                            "Job re-queued.",
                          )
                        }
                      >
                        Retry
                      </button>
                    )}
                  </div>
                ) : (
                  "—"
                ),
            },
          ]}
        />

        {activeKnowledge && (
          <>
            <DataTable
              rows={activeKnowledge.concepts}
              rowKey={(c) => c.concept_id}
              columns={[
                {
                  key: "name",
                  header: "Concept",
                  render: (c) => (
                    <strong>
                      {c.name}
                      {c.sense_label ? ` (${c.sense_label})` : ""}
                    </strong>
                  ),
                },
                {
                  key: "grounding",
                  header: "Grounding",
                  render: (c) => <StatusBadge tone={groundingTone(c.grounding)}>{c.grounding}</StatusBadge>,
                },
                {
                  key: "ev",
                  header: "Evidence",
                  render: (c) =>
                    c.evidence_bindings.length > 0
                      ? c.evidence_bindings.map((b) => `${b.evidence_id} (p.${b.page_number})`).join(", ")
                      : "None",
                },
                { key: "summary", header: "Summary", render: (c) => c.canonical_summary },
              ]}
            />
            {activeKnowledge.relationships.length > 0 && (
              <div style={{ fontSize: "0.84rem" }}>
                <strong>Concept Relationships:</strong>{" "}
                {activeKnowledge.relationships
                  .map((r) => `${r.source_concept_name} [${r.relation_type}] ${r.target_concept_name}`)
                  .join(" • ")}
              </div>
            )}
          </>
        )}
      </div>
    </details>
  );

  const subtitle = isSubjectTeacher
    ? `${source.visibility === "offering_published" ? "Shared with class" : "Teacher draft"} • ${source.lifecycle_state === "active" ? "Active" : "Archived"}`
    : isPersonalStudentNote
      ? "Personal study note"
      : "Published lesson material";

  return (
    <SectionCard
      title={source.title}
      subtitle={subtitle}
      actions={
        isSubjectTeacher && source.origin_kind === "teacher_resource" ? (
          <div className="viora-row">
            <button
              type="button"
              className="viora-btn viora-btn-sm"
              disabled={busy || source.lifecycle_state !== "active"}
              onClick={() =>
                void runAction(
                  "Update visibility",
                  () =>
                    apiClient.PATCH("/api/v1/tenants/{tenant_id}/sources/{source_id}", {
                      params: { path: { tenant_id: tenantId, source_id: source.source_id } },
                      body: {
                        visibility:
                          source.visibility === "offering_published"
                            ? "teacher_only"
                            : "offering_published",
                      },
                    }),
                  "Updated material visibility.",
                  true,
                )
              }
            >
              {source.visibility === "offering_published" ? "Make Private" : "Share with Class"}
            </button>
            <button
              type="button"
              className="viora-btn viora-btn-sm"
              disabled={busy}
              onClick={() =>
                void runAction(
                  "Update lifecycle",
                  () =>
                    apiClient.PATCH("/api/v1/tenants/{tenant_id}/sources/{source_id}", {
                      params: { path: { tenant_id: tenantId, source_id: source.source_id } },
                      body: {
                        lifecycle_state: source.lifecycle_state === "active" ? "archived" : "active",
                      },
                    }),
                  "Updated material status.",
                  true,
                )
              }
            >
              {source.lifecycle_state === "active" ? "Archive" : "Reactivate"}
            </button>
          </div>
        ) : undefined
      }
    >
      <div className="viora-stack">
        {alerts}
        {loading && <LoadingSpinner message="Loading lesson content..." />}

        {activeOutline && (
          <div className="viora-stack">
            <div className="viora-row">
              {canInspectDraft && pubState && (
                <>
                  <StatusBadge tone={reviewTone(pubState.review_status)}>
                    Curriculum: {pubState.teacher_approved ? "Approved" : pubState.review_status}
                  </StatusBadge>
                  <StatusBadge tone={pubState.published_to_students ? "success" : "neutral"}>
                    {pubState.published_to_students ? "Published to Students" : "Draft"}
                  </StatusBadge>
                </>
              )}
              {activeOutline.learning_request_only && (
                <StatusBadge tone="warning">Topic Study Guide</StatusBadge>
              )}
            </div>

            {activeOutline.prerequisite_analysis.cycles.map((cyc) => (
              <AlertBanner key={cyc.cycle_id} tone="warning" message={`Ordering note: ${cyc.resolution_note}`} />
            ))}

            <strong>Lesson Outline ({activeOutline.estimated_total_minutes} min)</strong>
            {activeOutline.modules.map((mod) => (
              <div
                key={mod.module_id}
                style={{
                  padding: "0.75rem",
                  border: "1px solid var(--color-border)",
                  borderRadius: "var(--radius-sm)",
                }}
              >
                <strong>{mod.title}</strong>
                <ol style={{ marginTop: "0.4rem", paddingLeft: "1.25rem", fontSize: "0.85rem" }}>
                  {mod.steps.map((s) => (
                    <li key={s.concept_id}>
                      <strong>{s.concept_name}</strong> ({s.estimated_minutes}m) — {s.canonical_summary}
                    </li>
                  ))}
                </ol>
              </div>
            ))}
          </div>
        )}

        {!canInspectDraft && !studentPkg && !publishedBlueprint && !loading && (
          <AlertBanner
            tone="info"
            message="No teacher-approved lesson has been published for this material yet."
          />
        )}

        {!canInspectDraft && studentPkg && !publishedBlueprint && !loading && (
          <AlertBanner
            tone="info"
            message="The curriculum outline above is published. No interactive lesson guide has been published yet."
          />
        )}

        {!canInspectDraft && publishedBlueprint && (
          <LessonBlueprintPreview mode="student" published={publishedBlueprint} />
        )}

        {isTeacherManageable && !activeOutline && !bpRecord && !loading && (
          <AlertBanner
            tone="info"
            message="No lesson outline or blueprint has been generated for this material yet. Use Advanced Resource Processing below to process it."
          />
        )}

        {isTeacherManageable && concepts && pubState && (
          <div className="viora-stack" style={{ borderTop: "1px solid var(--color-border)", paddingTop: "0.75rem" }}>
            <form className="viora-stack" onSubmit={handleReviewSubmit}>
              <div className="viora-row">
                <FormField label="Curriculum Review Decision" htmlFor="review-decision">
                  <select
                    id="review-decision"
                    className="viora-select"
                    value={decision}
                    onChange={(e) => setDecision(e.target.value as "approved" | "changes_requested" | "rejected")}
                  >
                    <option value="approved">Approved</option>
                    <option value="changes_requested">Changes Requested</option>
                    <option value="rejected">Rejected</option>
                  </select>
                </FormField>
                <FormField label="Review Notes" htmlFor="review-notes">
                  <input
                    id="review-notes"
                    className="viora-input"
                    value={reviewNotes}
                    onChange={(e) => setReviewNotes(e.target.value)}
                    placeholder="Optional notes"
                  />
                </FormField>
              </div>
              {pubState.learning_request_only && decision === "approved" && (
                <label className="viora-row" style={{ fontSize: "0.84rem" }}>
                  <input
                    type="checkbox"
                    checked={ackTopicRequest}
                    onChange={(e) => setAckTopicRequest(e.target.checked)}
                  />
                  <span>Acknowledge that topic descriptions are study requests rather than source excerpts</span>
                </label>
              )}
              <div className="viora-row">
                <button type="submit" className="viora-btn viora-btn-primary" disabled={busy}>
                  Submit Review (v{concepts.concept_version})
                </button>
                <button
                  type="button"
                  className="viora-btn"
                  disabled={busy || (!pubState.published_to_students && !pubState.teacher_approved)}
                  onClick={() =>
                    void runAction(
                      pubState.published_to_students ? "Unpublish" : "Publish",
                      () =>
                        apiClient.POST(
                          "/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts/publish",
                          {
                            params: { path: { tenant_id: tenantId, source_id: source.source_id, version_id: selectedVersionId } },
                            body: { concept_extraction_id: concepts.concept_extraction_id, publish: !pubState.published_to_students },
                          },
                        ),
                      pubState.published_to_students
                        ? "Unpublished lesson outline from students."
                        : "Published approved lesson outline to enrolled students.",
                    )
                  }
                >
                  {pubState.published_to_students ? "Unpublish Outline" : "Publish Approved Outline"}
                </button>
              </div>
            </form>
            {reviews.length > 0 && (
              <p style={{ fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
                Review History: {reviews.map((r) => `v${r.review_version}: ${r.decision}`).join(" • ")}
              </p>
            )}
          </div>
        )}

        {canInspectDraft && bpRecord && (
          <>
            <LessonBlueprintPreview
              mode="teacher"
              record={bpRecord}
              approvalState={bpApproval}
              versions={bpVersions}
            />
            {canApproveLessonBlueprint(isSubjectTeacher, source, pubState, bpRecord) && (
              <div className="viora-stack" style={{ borderTop: "1px solid var(--color-border)", paddingTop: "0.75rem" }}>
                <strong>Blueprint Approval &amp; Publication (v{bpRecord.blueprint_version})</strong>
                <div className="viora-row">
                  <FormField label="Blueprint Decision" htmlFor="bp-review-decision">
                    <select
                      id="bp-review-decision"
                      className="viora-select"
                      value={bpDecision}
                      onChange={(e) => setBpDecision(e.target.value as "approved" | "changes_requested" | "rejected")}
                    >
                      <option value="approved">Approved</option>
                      <option value="changes_requested">Changes Requested</option>
                      <option value="rejected">Rejected</option>
                    </select>
                  </FormField>
                  <FormField label="Blueprint Review Notes" htmlFor="bp-review-notes">
                    <input
                      id="bp-review-notes"
                      className="viora-input"
                      value={bpReviewNotes}
                      onChange={(e) => setBpReviewNotes(e.target.value)}
                      placeholder="Review narration and practice questions"
                    />
                  </FormField>
                </div>
                <div className="viora-row">
                  <button
                    type="button"
                    className="viora-btn"
                    disabled={busy}
                    onClick={() =>
                      void runAction(
                        "Save blueprint review",
                        () =>
                          apiClient.POST(
                            "/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/blueprint/review",
                            {
                              params: { path: { tenant_id: tenantId, source_id: source.source_id, version_id: selectedVersionId } },
                              body: {
                                blueprint_id: bpRecord.blueprint_id,
                                decision: bpDecision,
                                publish: false,
                                review_notes: bpReviewNotes.trim() || null,
                              },
                            },
                          ),
                        `Saved blueprint review (${bpDecision}).`,
                      )
                    }
                  >
                    Save Review
                  </button>
                  <button
                    type="button"
                    className="viora-btn viora-btn-primary"
                    disabled={busy || source.visibility !== "offering_published" || !pubState?.published_to_students}
                    onClick={() =>
                      void runAction(
                        "Approve & publish blueprint",
                        () =>
                          apiClient.POST(
                            "/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/blueprint/review",
                            {
                              params: { path: { tenant_id: tenantId, source_id: source.source_id, version_id: selectedVersionId } },
                              body: {
                                blueprint_id: bpRecord.blueprint_id,
                                decision: "approved",
                                publish: true,
                                review_notes: bpReviewNotes.trim() || null,
                              },
                            },
                          ),
                        "Approved and published lesson to enrolled students.",
                      )
                    }
                  >
                    Approve &amp; Publish to Students
                  </button>
                  {bpApproval?.published_to_students && (
                    <button
                      type="button"
                      className="viora-btn viora-btn-danger"
                      disabled={busy}
                      onClick={() =>
                        void runAction(
                          "Revoke blueprint publication",
                          () =>
                            apiClient.POST(
                              "/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/blueprint/review",
                              {
                                params: { path: { tenant_id: tenantId, source_id: source.source_id, version_id: selectedVersionId } },
                                body: {
                                  blueprint_id: bpRecord.blueprint_id,
                                  decision: "changes_requested",
                                  publish: false,
                                  review_notes: bpReviewNotes.trim() || "Publication revoked by teacher",
                                },
                              },
                            ),
                          "Unpublished lesson blueprint from students.",
                        )
                      }
                    >
                      Unpublish Lesson
                    </button>
                  )}
                </div>
                {(source.visibility !== "offering_published" || !pubState?.published_to_students) && (
                  <p style={{ fontSize: "0.78rem", color: "var(--color-text-muted)" }}>
                    Share this material with the class and publish the curriculum outline before publishing the lesson to students.
                  </p>
                )}
              </div>
            )}
          </>
        )}

        {processingControlsBlock}
      </div>
    </SectionCard>
  );
}
