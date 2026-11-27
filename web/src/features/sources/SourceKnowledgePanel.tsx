import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  type ConceptExtractionRecord,
  type DerivedExtractionRecord,
  type KnowledgePublicationState,
  type KnowledgeReviewRecord,
  type OfferingSourcePolicyInfo,
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
import {
  canOwnerStudentProcess,
  canTeacherManageSource,
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
  const [studentPkg, setStudentPkg] = useState<PublishedLessonPackage | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  const [decision, setDecision] = useState<"approved" | "changes_requested" | "rejected">("approved");
  const [reviewNotes, setReviewNotes] = useState("");
  const [ackTopicRequest, setAckTopicRequest] = useState(false);

  const isTeacherManageable = canTeacherManageSource(isSubjectTeacher, source);
  const isOwnerStudent = canOwnerStudentProcess(actorUserId, source, policy);
  const canInspectDraft =
    isSubjectTeacher ||
    (source.origin_kind === "student_submission" && source.owner_user_id === actorUserId);

  useEffect(() => {
    setSelectedVersionId(source.latest_version_id ?? "");
  }, [source.source_id, source.latest_version_id]);

  const loadWorkspace = useCallback(async () => {
    setLoading(true);
    clearFeedback();
    const freshGet = <T,>(call: () => Promise<ApiResultLike<T>>) => authController.runWithFreshToken(call);
    const sourcePath = { tenant_id: tenantId, source_id: source.source_id };
    try {
      const [verRes, jobRes] = await Promise.all([
        freshGet(() => apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions", { params: { path: sourcePath } })),
        freshGet(() => apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/jobs", { params: { path: sourcePath } })),
      ]);
      if (verRes.data) setVersions(verRes.data.items);
      if (jobRes.data) setJobs(jobRes.data.items);

      const verId = selectedVersionId;
      if (!verId) return;
      const versionPath = { ...sourcePath, version_id: verId };

      if (canInspectDraft) {
        const [extRes, conRes, outRes, revRes] = await Promise.all([
          freshGet(() => apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/extraction", { params: { path: versionPath } })),
          freshGet(() => apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts", { params: { path: versionPath } })),
          freshGet(() => apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts/outline", { params: { path: versionPath } })),
          freshGet(() => apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts/reviews", { params: { path: versionPath } })),
        ]);
        setDerived(extRes.data ?? null);
        setConcepts(conRes.data ?? null);
        setPubState(outRes.data ?? null);
        setReviews(revRes.data?.items ?? []);
        setStudentPkg(null);
      } else {
        const pubRes = await freshGet(() =>
          apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts/published", { params: { path: versionPath } }),
        );
        setStudentPkg(pubRes.data ?? null);
        setDerived(null);
        setConcepts(null);
        setPubState(null);
        setReviews([]);
      }
    } catch {
      setActionError({ status: 0, code: "network_error", message: "Failed to load resource details." });
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
        void loadWorkspace();
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
      `Saved versioned teacher review (${decision}).`,
    );
  };

  const activeKnowledge = concepts?.knowledge ?? studentPkg?.knowledge ?? null;
  const activeOutline = pubState?.outline ?? studentPkg?.outline ?? null;

  return (
    <SectionCard
      title={`Resource Inspector: ${source.title}`}
      subtitle={`Origin: ${source.origin_kind} • Visibility: ${source.visibility} • Lifecycle: ${source.lifecycle_state}`}
      actions={
        <div className="viora-row">
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
          <button type="button" className="viora-btn viora-btn-sm" onClick={() => void loadWorkspace()}>
            Refresh
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
          {isSubjectTeacher && source.origin_kind === "teacher_resource" && (
            <>
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
                    "Updated resource visibility.",
                    true,
                  )
                }
              >
                {source.visibility === "offering_published" ? "Make Teacher-Only" : "Share with Offering"}
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
                    "Updated resource lifecycle state.",
                    true,
                  )
                }
              >
                {source.lifecycle_state === "active" ? "Archive" : "Reactivate"}
              </button>
            </>
          )}
        </div>
      }
    >
      <div className="viora-stack">
        {alerts}
        {loading && <LoadingSpinner message="Loading extraction, jobs, and knowledge state..." />}

        <DataTable
          rows={jobs}
          rowKey={(j) => j.job_id}
          emptyTitle="No processing jobs yet"
          emptyDescription="Trigger text or concept extraction when permitted."
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

        {derived && (
          <p style={{ fontSize: "0.84rem", color: "var(--color-text-muted)" }}>
            Extracted Document: {derived.document.pages.length} page(s), {derived.document.evidence_references.length} evidence spans, status: {derived.document.quality.overall_status}
          </p>
        )}

        {activeKnowledge && activeOutline && (
          <>
            <div className="viora-row">
              <StatusBadge tone={reviewTone(pubState?.review_status ?? "approved")}>
                Teacher Approval: {pubState ? (pubState.teacher_approved ? "Approved" : pubState.review_status) : "Approved"}
              </StatusBadge>
              <StatusBadge tone={(pubState?.published_to_students ?? studentPkg?.published_to_students) ? "success" : "neutral"}>
                {(pubState?.published_to_students ?? studentPkg?.published_to_students) ? "Published to Students" : "Draft / Unpublished"}
              </StatusBadge>
              <StatusBadge tone={activeOutline.all_evidence_verified ? "success" : "warning"}>
                Evidence IDs Verified: {String(activeOutline.all_evidence_verified)}
              </StatusBadge>
              {activeOutline.learning_request_only && (
                <StatusBadge tone="warning">Topic Learning Request Only (Not Factual Evidence)</StatusBadge>
              )}
              {activeOutline.has_prerequisite_cycles && (
                <StatusBadge tone="warning">Prerequisite Cycles Resolved ({activeOutline.prerequisite_analysis.cycles.length})</StatusBadge>
              )}
            </div>

            {activeOutline.prerequisite_analysis.cycles.map((cyc) => (
              <AlertBanner key={cyc.cycle_id} tone="warning" message={`Cycle Notice: ${cyc.resolution_note}`} />
            ))}

            <DataTable
              rows={activeKnowledge.concepts}
              rowKey={(c) => c.concept_id}
              columns={[
                { key: "name", header: "Concept", render: (c) => <strong>{c.name}{c.sense_label ? ` (${c.sense_label})` : ""}</strong> },
                { key: "grounding", header: "Semantic Grounding", render: (c) => <StatusBadge tone={groundingTone(c.grounding)}>{c.grounding}</StatusBadge> },
                {
                  key: "ev",
                  header: "Source Evidence",
                  render: (c) =>
                    c.evidence_bindings.length > 0
                      ? c.evidence_bindings.map((b) => `${b.evidence_id} (p.${b.page_number})`).join(", ")
                      : "None (Unverified)",
                },
                { key: "summary", header: "Summary", render: (c) => c.canonical_summary },
              ]}
            />

            {activeKnowledge.relationships.length > 0 && (
              <div style={{ fontSize: "0.85rem" }}>
                <strong>Typed Relationships:</strong>{" "}
                {activeKnowledge.relationships
                  .map((r) => `${r.source_concept_name} [${r.relation_type}] ${r.target_concept_name}`)
                  .join(" • ")}
              </div>
            )}

            <div className="viora-stack">
              <strong>Structured Lesson Outline ({activeOutline.estimated_total_minutes} min total)</strong>
              {activeOutline.modules.map((mod) => (
                <div key={mod.module_id} style={{ padding: "0.75rem", border: "1px solid var(--color-border)", borderRadius: "var(--radius-sm)" }}>
                  <div className="viora-row" style={{ justifyContent: "space-between" }}>
                    <strong>{mod.title}</strong>
                    <StatusBadge tone={groundingTone(mod.module_grounding)}>{mod.module_grounding}</StatusBadge>
                  </div>
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
          </>
        )}

        {!canInspectDraft && !studentPkg && !loading && (
          <AlertBanner tone="info" message="No teacher-approved educational lesson has been published for this resource version yet." />
        )}

        {isTeacherManageable && concepts && pubState && (
          <div className="viora-stack" style={{ borderTop: "1px solid var(--color-border)", paddingTop: "0.75rem" }}>
            <form className="viora-stack" onSubmit={handleReviewSubmit}>
              <div className="viora-row">
                <FormField label="Teacher Review Decision" htmlFor="review-decision">
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
                    placeholder="Optional verification notes"
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
                  <span>Acknowledge that topic descriptions are learning requests (not factual source evidence)</span>
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
                        ? "Unpublished educational lesson from students."
                        : "Published approved lesson to enrolled students.",
                    )
                  }
                >
                  {pubState.published_to_students ? "Unpublish Lesson" : "Publish Approved Lesson"}
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
      </div>
    </SectionCard>
  );
}
