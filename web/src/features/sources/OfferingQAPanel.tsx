import { useState, type FormEvent } from "react";
import type { KnowledgeQARequest, KnowledgeQAResult } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { LoadingSpinner } from "../../components/common/FeedbackStates";
import {
  AlertBanner,
  DataTable,
  FormField,
  SectionCard,
  StatusBadge,
} from "../../components/common/UiPrimitives";
import { useActionFeedback } from "../common/useActionFeedback";
import { buildKnowledgeQABody, qaStatusTone } from "./sourceHelpers";

export function OfferingQAPanel({
  tenantId,
  offeringId,
  offeringName,
  isSubjectTeacher,
}: {
  tenantId: string;
  offeringId: string;
  offeringName: string;
  isSubjectTeacher: boolean;
}) {
  const { apiClient } = useAuth();
  const feedback = useActionFeedback();

  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<KnowledgeQARequest["mode"]>("hybrid_graph");
  const [allowInterpretation, setAllowInterpretation] = useState(false);
  const [allowGeneralKnowledge, setAllowGeneralKnowledge] = useState(false);
  const [publishedOnly, setPublishedOnly] = useState(!isSubjectTeacher);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<KnowledgeQAResult | null>(null);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!question.trim()) {
      feedback.setValidationError("Enter a question to search your subject materials.");
      return;
    }
    const body = buildKnowledgeQABody({
      question,
      offeringId,
      mode,
      allowInterpretation,
      allowGeneralKnowledge,
      publishedOnly,
    });
    void feedback.runApiAction({
      setBusy,
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/knowledge/qa", {
          params: { path: { tenant_id: tenantId } },
          body,
        }),
      fallbackError: "Question request failed.",
      onSuccess: (data) => {
        setResult(data);
      },
    });
  };

  return (
    <SectionCard
      title="Ask a Question"
      subtitle={`Get answers grounded in ${offeringName || "this subject"}'s authorized materials.`}
    >
      <div className="viora-stack">
        {feedback.alerts}

        <form className="viora-stack" onSubmit={handleSubmit}>
          <FormField label="Your question" htmlFor="qa-question">
            <textarea
              id="qa-question"
              className="viora-input"
              rows={2}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Ask about concepts, definitions, or worked steps in this subject..."
              required
            />
          </FormField>

          <details className="viora-details">
            <summary>Advanced Q&amp;A options</summary>
            <div className="viora-row">
              <FormField label="Retrieval Mode" htmlFor="qa-mode">
                <select
                  id="qa-mode"
                  className="viora-select"
                  value={mode}
                  onChange={(e) => setMode(e.target.value as KnowledgeQARequest["mode"])}
                >
                  <option value="hybrid_graph">Hybrid + Concept Graph</option>
                  <option value="hybrid">Hybrid (BM25 + Semantic)</option>
                  <option value="semantic">Semantic Only</option>
                  <option value="lexical">Lexical Only</option>
                </select>
              </FormField>

              <label className="viora-row" style={{ fontSize: "0.84rem" }}>
                <input
                  type="checkbox"
                  checked={publishedOnly}
                  onChange={(e) => setPublishedOnly(e.target.checked)}
                />
                <span>Published teacher sources only</span>
              </label>

              <label className="viora-row" style={{ fontSize: "0.84rem" }}>
                <input
                  type="checkbox"
                  checked={allowInterpretation}
                  onChange={(e) => setAllowInterpretation(e.target.checked)}
                />
                <span>Include labeled unverified interpretations</span>
              </label>

              <label className="viora-row" style={{ fontSize: "0.84rem" }}>
                <input
                  type="checkbox"
                  checked={allowGeneralKnowledge}
                  onChange={(e) => setAllowGeneralKnowledge(e.target.checked)}
                />
                <span>Allow separated general educational fallback</span>
              </label>
            </div>
          </details>

          <div className="viora-row">
            <button type="submit" className="viora-btn viora-btn-primary" disabled={busy}>
              {busy ? "Searching Subject Materials..." : "Ask Question"}
            </button>
          </div>
        </form>

        {busy && <LoadingSpinner message="Finding relevant course evidence..." />}

        {result && !busy && (
          <div
            className="viora-stack"
            style={{ borderTop: "1px solid var(--color-border)", paddingTop: "0.75rem" }}
          >
            {result.answer && (
              <div
                style={{
                  padding: "0.75rem",
                  border: "1px solid var(--color-border)",
                  borderRadius: "var(--radius-sm)",
                }}
              >
                <strong>Answer</strong>
                <p style={{ fontSize: "0.9rem", marginTop: "0.25rem" }}>{result.answer}</p>
              </div>
            )}

            {result.clarification && (
              <AlertBanner
                tone="warning"
                title="Clarification Needed"
                message={
                  `${result.clarification.reason}` +
                  (result.clarification.ambiguities.length > 0
                    ? ` Ambiguities: ${result.clarification.ambiguities.join("; ")}.`
                    : "") +
                  ` Questions: ${result.clarification.clarifying_questions.join(" | ")}`
                }
              />
            )}

            {result.abstention && (
              <AlertBanner
                tone="warning"
                title="Not Enough Course Evidence"
                message={
                  `${result.abstention.reason}` +
                  (result.abstention.missing_evidence_topics.length > 0
                    ? ` Missing topics: ${result.abstention.missing_evidence_topics.join(", ")}.`
                    : "")
                }
              />
            )}

            {result.source_grounded_claims.length > 0 && (
              <div className="viora-stack" style={{ gap: "0.4rem" }}>
                <strong>Key Grounded Points ({result.source_grounded_claims.length})</strong>
                {result.source_grounded_claims.map((claim, idx) => (
                  <div
                    key={`claim-${idx}`}
                    style={{
                      padding: "0.6rem 0.75rem",
                      border: "1px solid var(--color-border)",
                      borderRadius: "var(--radius-sm)",
                    }}
                  >
                    <div className="viora-row" style={{ justifyContent: "space-between" }}>
                      <span>{claim.statement}</span>
                      <StatusBadge tone="success">
                        [{claim.citations.map((c) => c.citation_ref).join(", ")}]
                      </StatusBadge>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {result.interpretations.length > 0 && (
              <div className="viora-stack" style={{ gap: "0.4rem" }}>
                <AlertBanner
                  tone="warning"
                  title="Additional Unverified Interpretations"
                  message="These notes were requested as interpretations and have not been directly verified against exact source spans."
                />
                {result.interpretations.map((interp, idx) => (
                  <div
                    key={`interp-${idx}`}
                    style={{
                      padding: "0.6rem 0.75rem",
                      border: "1px dashed var(--color-border)",
                      borderRadius: "var(--radius-sm)",
                    }}
                  >
                    <div className="viora-row" style={{ justifyContent: "space-between" }}>
                      <span>{interp.statement}</span>
                      <StatusBadge tone="warning">
                        Interpretation [{interp.citations.map((c) => c.citation_ref).join(", ")}]
                      </StatusBadge>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {result.general_knowledge && (
              <AlertBanner
                tone="info"
                title={result.general_knowledge.label}
                message={result.general_knowledge.content}
              />
            )}

            {result.citations.length > 0 && (
              <div className="viora-stack" style={{ gap: "0.4rem" }}>
                <strong>Referenced Material ({result.citations.length})</strong>
                <DataTable
                  rows={result.citations}
                  rowKey={(c) => `${c.citation_ref}-${c.chunk_record_id}`}
                  columns={[
                    { key: "ref", header: "Ref", render: (c) => <code>{c.citation_ref}</code> },
                    {
                      key: "pages",
                      header: "Location",
                      render: (c) =>
                        `p.${c.page_start}-${c.page_end}${c.heading_title ? ` (${c.heading_title})` : ""}`,
                    },
                    { key: "excerpt", header: "Excerpt", render: (c) => c.excerpt },
                  ]}
                />
              </div>
            )}

            <details className="viora-details">
              <summary>Retrieval &amp; verification diagnostics</summary>
              <div className="viora-row">
                <StatusBadge tone={qaStatusTone(result.status)}>
                  Status: {result.status}
                </StatusBadge>
                <StatusBadge tone="neutral">
                  Mode: {result.validation.retrieval_mode}
                </StatusBadge>
                <StatusBadge tone="neutral">
                  Citations: {result.validation.valid_citations_count} valid /{" "}
                  {result.validation.rejected_citations_count} rejected
                </StatusBadge>
                <span style={{ fontSize: "0.78rem", color: "var(--color-text-muted)" }}>
                  {result.validation.context_tokens} ctx tokens •{" "}
                  {result.validation.inference_requests} req • {result.validation.total_ms.toFixed(1)} ms
                </span>
              </div>
            </details>
          </div>
        )}
      </div>
    </SectionCard>
  );
}
