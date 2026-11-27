import { useCallback, useEffect, useState, type FormEvent } from "react";
import type {
  OfferingSourcePolicyInfo,
  SourceSummary,
  SubjectOfferingSummary,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { ErrorState, LoadingSpinner } from "../../components/common/FeedbackStates";
import {
  DataTable,
  FormField,
  PageHeader,
  PaginationBar,
  SectionCard,
  StatusBadge,
} from "../../components/common/UiPrimitives";
import { deriveRoleFlags } from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { useActionFeedback } from "../common/useActionFeedback";
import { useCursorList } from "../common/useCursorList";
import { SourceKnowledgePanel } from "./SourceKnowledgePanel";
import { buildSourceUploadFormData } from "./sourceHelpers";

export function SourcesPage() {
  const { apiClient, controller: authController } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);
  const feedback = useActionFeedback();

  const [offerings, setOfferings] = useState<SubjectOfferingSummary[]>([]);
  const [offeringId, setOfferingId] = useState("");
  const [policy, setPolicy] = useState<OfferingSourcePolicyInfo | null>(null);
  const [selectedSource, setSelectedSource] = useState<SourceSummary | null>(null);

  const [mode, setMode] = useState<"text" | "file">("text");
  const [title, setTitle] = useState("");
  const [inputFormat, setInputFormat] = useState<"plain_text" | "typed_notes" | "topic_description">("typed_notes");
  const [content, setContent] = useState("");
  const [fileObj, setFileObj] = useState<File | null>(null);
  const [visibility, setVisibility] = useState<"offering_published" | "teacher_only">("offering_published");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!tenantId) return;
    let cancelled = false;
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
            setOfferingId((prev) => prev || res.data!.items[0].id);
          }
        }
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, authController, tenantId]);

  const loadPolicy = useCallback(async () => {
    if (!tenantId || !offeringId) return;
    const res = await authController.runWithFreshToken(() =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/source-policy", {
        params: { path: { tenant_id: tenantId, offering_id: offeringId } },
      }),
    );
    if (res.data) setPolicy(res.data);
  }, [apiClient, authController, offeringId, tenantId]);

  useEffect(() => {
    setSelectedSource(null);
    void loadPolicy();
  }, [loadPolicy]);

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

  const list = useCursorList<SourceSummary>(tenantId && offeringId ? `${tenantId}:${offeringId}` : null, fetcher);

  const canUploadTeacher = flags.isSubjectTeacher;
  const canSubmitStudent = flags.isStudent && Boolean(policy?.student_submissions_enabled);

  const handleUpload = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId || !offeringId) return;
    if (!title.trim()) {
      feedback.setValidationError("Title is required.");
      return;
    }
    if (mode === "file" && !fileObj) {
      feedback.setValidationError("Select a document file to upload.");
      return;
    }

    const originKind = canUploadTeacher ? "teacher_resource" : "student_submission";
    const vis = canUploadTeacher ? visibility : "owner_private";
    await feedback.runApiAction({
      setBusy: setSubmitting,
      call: () =>
        mode === "text"
          ? apiClient.POST("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/sources", {
              params: { path: { tenant_id: tenantId, offering_id: offeringId } },
              body: {
                title: title.trim(),
                input_format: inputFormat,
                content,
                origin_kind: originKind,
                visibility: vis,
              },
            })
          : apiClient.POST("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/sources/files", {
              params: { path: { tenant_id: tenantId, offering_id: offeringId } },
              body: buildSourceUploadFormData({
                title,
                file: fileObj!,
                originKind,
                visibility: vis,
              }) as never,
            }),
      fallbackError: "Upload failed.",
      onSuccess: (data) => {
        setTitle("");
        setContent("");
        setFileObj(null);
        setSelectedSource(data.source);
        feedback.setActionSuccess(`Uploaded "${data.source.title}" (job ${data.job_state}).`);
        list.resetToFirstPage();
        list.reload();
      },
    });
  };

  const toggleStudentSubmissions = async () => {
    if (!tenantId || !offeringId || !policy) return;
    await feedback.runApiAction({
      call: () =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/source-policy", {
          params: { path: { tenant_id: tenantId, offering_id: offeringId } },
          body: { student_submissions_enabled: !policy.student_submissions_enabled },
        }),
      fallbackError: "Failed to update source policy.",
      onSuccess: (data) => {
        setPolicy(data);
        feedback.setActionSuccess(`Student submissions ${data.student_submissions_enabled ? "enabled" : "disabled"}.`);
      },
    });
  };

  if (!tenantId) return null;

  return (
    <div className="viora-stack">
      <PageHeader
        title="Educational Knowledge & Lessons"
        subtitle="Manage offering resources, inspect evidence-linked concept outlines, and publish teacher-approved lessons."
        actions={
          <select
            aria-label="Select Subject Offering"
            className="viora-select"
            style={{ width: "auto" }}
            value={offeringId}
            onChange={(e) => setOfferingId(e.target.value)}
          >
            {offerings.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        }
      />

      {feedback.alerts}

      {policy && (
        <SectionCard
          title="Offering Policy"
          subtitle={`Student note submissions: ${policy.student_submissions_enabled ? "Enabled" : "Disabled"}`}
          actions={
            canUploadTeacher ? (
              <button type="button" className="viora-btn viora-btn-sm" onClick={() => void toggleStudentSubmissions()}>
                {policy.student_submissions_enabled ? "Disable Student Submissions" : "Enable Student Submissions"}
              </button>
            ) : undefined
          }
        >
          {null}
        </SectionCard>
      )}

      {(canUploadTeacher || canSubmitStudent) && offeringId && (
        <SectionCard title={canUploadTeacher ? "Add Educational Resource" : "Submit Personal Notes"}>
          <form className="viora-stack" onSubmit={handleUpload}>
            <div className="viora-row">
              <button type="button" className={`viora-btn viora-btn-sm ${mode === "text" ? "viora-btn-primary" : ""}`} onClick={() => setMode("text")}>
                Notes / Topic Text
              </button>
              <button type="button" className={`viora-btn viora-btn-sm ${mode === "file" ? "viora-btn-primary" : ""}`} onClick={() => setMode("file")}>
                PDF / Image Upload
              </button>
            </div>
            <div className="viora-row">
              <FormField label="Title" htmlFor="src-title">
                <input id="src-title" className="viora-input" value={title} onChange={(e) => setTitle(e.target.value)} required />
              </FormField>
              {mode === "text" && (
                <FormField label="Format" htmlFor="src-fmt">
                  <select id="src-fmt" className="viora-select" value={inputFormat} onChange={(e) => setInputFormat(e.target.value as "plain_text" | "typed_notes" | "topic_description")}>
                    <option value="typed_notes">Typed Notes</option>
                    <option value="plain_text">Plain Text</option>
                    <option value="topic_description">Topic Request (Non-Evidentiary)</option>
                  </select>
                </FormField>
              )}
              {canUploadTeacher && (
                <FormField label="Visibility" htmlFor="src-vis">
                  <select id="src-vis" className="viora-select" value={visibility} onChange={(e) => setVisibility(e.target.value as "offering_published" | "teacher_only")}>
                    <option value="offering_published">Offering Published</option>
                    <option value="teacher_only">Teacher Only</option>
                  </select>
                </FormField>
              )}
            </div>
            {mode === "text" ? (
              <FormField label="Content" htmlFor="src-content">
                <textarea id="src-content" className="viora-input" rows={4} value={content} onChange={(e) => setContent(e.target.value)} required />
              </FormField>
            ) : (
              <FormField label="Document File (PDF, PNG, JPEG)" htmlFor="src-file">
                <input id="src-file" type="file" accept=".pdf,.png,.jpg,.jpeg" onChange={(e) => setFileObj(e.target.files?.[0] ?? null)} required />
              </FormField>
            )}
            <button type="submit" className="viora-btn viora-btn-primary" disabled={submitting}>
              {submitting ? "Uploading..." : canUploadTeacher ? "Register Resource" : "Submit Notes"}
            </button>
          </form>
        </SectionCard>
      )}

      {list.loading && <LoadingSpinner message="Loading offering resources..." />}
      {list.error && <ErrorState message={list.error.message} onRetry={list.reload} />}

      {!list.loading && !list.error && (
        <SectionCard title="Offering Resources">
          <DataTable
            rows={list.items}
            rowKey={(s) => s.source_id}
            emptyTitle="No resources yet"
            emptyDescription="Upload a resource or wait for your teacher to share materials."
            columns={[
              { key: "title", header: "Title", render: (s) => <strong>{s.title}</strong> },
              { key: "origin", header: "Origin", render: (s) => s.origin_kind },
              { key: "fmt", header: "Format", render: (s) => s.latest_input_format },
              { key: "vis", header: "Visibility", render: (s) => <StatusBadge tone={s.visibility === "offering_published" ? "success" : "neutral"}>{s.visibility}</StatusBadge> },
              {
                key: "open",
                header: "Inspect",
                render: (s) => (
                  <button type="button" className="viora-btn viora-btn-sm viora-btn-primary" onClick={() => setSelectedSource(s)}>
                    Open
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
        </SectionCard>
      )}

      {selectedSource && (
        <SourceKnowledgePanel
          tenantId={tenantId}
          source={selectedSource}
          isSubjectTeacher={flags.isSubjectTeacher}
          policy={policy}
          onSourceUpdated={list.reload}
        />
      )}
    </div>
  );
}
