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
import { AssignmentModal } from "../common/AssignmentModal";
import { useActionFeedback } from "../common/useActionFeedback";
import { useCursorList } from "../common/useCursorList";
import { useTenantOptions } from "../common/useTenantOptions";
import { OfferingQAPanel } from "./OfferingQAPanel";
import { SourceKnowledgePanel } from "./SourceKnowledgePanel";
import { buildSourceUploadFormData } from "./sourceHelpers";

function formatInputKind(fmt: string | null | undefined): string {
  if (!fmt) return "Resource";
  if (fmt === "typed_notes") return "Typed Notes";
  if (fmt === "plain_text") return "Plain Text";
  if (fmt === "topic_description") return "Topic Outline";
  if (fmt === "pdf") return "PDF Document";
  if (fmt === "image") return "Image";
  return fmt;
}

export function SourcesPage({
  pageTitle = "My Teaching Subjects",
  pageSubtitle = "Manage course materials, publish approved lessons, and answer subject questions.",
}: {
  pageTitle?: string;
  pageSubtitle?: string;
}) {
  const { apiClient, controller: authController } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);
  const feedback = useActionFeedback();
  const options = useTenantOptions({ members: true });

  const [offerings, setOfferings] = useState<SubjectOfferingSummary[]>([]);
  const [offeringId, setOfferingId] = useState("");
  const [policy, setPolicy] = useState<OfferingSourcePolicyInfo | null>(null);
  const [selectedSource, setSelectedSource] = useState<SourceSummary | null>(null);
  const [rosterMode, setRosterMode] = useState<"enrolments" | "teachers" | null>(null);

  const [mode, setMode] = useState<"text" | "file">("text");
  const [title, setTitle] = useState("");
  const [inputFormat, setInputFormat] = useState<
    "plain_text" | "typed_notes" | "topic_description"
  >("typed_notes");
  const [content, setContent] = useState("");
  const [fileObj, setFileObj] = useState<File | null>(null);
  const [visibility, setVisibility] = useState<"offering_published" | "teacher_only">(
    "offering_published",
  );
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
            setOfferingId((prev) =>
              prev && res.data!.items.some((o) => o.id === prev)
                ? prev
                : res.data!.items[0].id,
            );
          } else {
            setOfferingId("");
          }
        }
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, authController, tenantId]);

  const loadPolicy = useCallback(async () => {
    if (!tenantId || !offeringId || !(flags.isSubjectTeacher || flags.isAdmin)) {
      setPolicy(null);
      return;
    }
    const res = await authController.runWithFreshToken(() =>
      apiClient.GET(
        "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/source-policy",
        {
          params: { path: { tenant_id: tenantId, offering_id: offeringId } },
        },
      ),
    );
    if (res.data) setPolicy(res.data);
  }, [apiClient, authController, flags.isAdmin, flags.isSubjectTeacher, offeringId, tenantId]);

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

  const list = useCursorList<SourceSummary>(
    tenantId && offeringId ? `${tenantId}:${offeringId}` : null,
    fetcher,
  );

  const canUploadTeacher = flags.isSubjectTeacher;
  const activeOffering = offerings.find((o) => o.id === offeringId) ?? null;

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
          : apiClient.POST(
              "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/sources/files",
              {
                params: { path: { tenant_id: tenantId, offering_id: offeringId } },
                body: buildSourceUploadFormData({
                  title,
                  file: fileObj!,
                  originKind,
                  visibility: vis,
                }) as never,
              },
            ),
      fallbackError: "Upload failed.",
      onSuccess: (data) => {
        setTitle("");
        setContent("");
        setFileObj(null);
        setSelectedSource(data.source);
        feedback.setActionSuccess(`Added "${data.source.title}".`);
        list.resetToFirstPage();
        list.reload();
      },
    });
  };

  const toggleStudentSubmissions = async () => {
    if (!tenantId || !offeringId || !policy) return;
    await feedback.runApiAction({
      call: () =>
        apiClient.PATCH(
          "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/source-policy",
          {
            params: { path: { tenant_id: tenantId, offering_id: offeringId } },
            body: { student_submissions_enabled: !policy.student_submissions_enabled },
          },
        ),
      fallbackError: "Failed to update subject settings.",
      onSuccess: (data) => {
        setPolicy(data);
        feedback.setActionSuccess(
          `Student personal note submissions ${data.student_submissions_enabled ? "enabled" : "disabled"}.`,
        );
      },
    });
  };

  if (!tenantId) return null;

  return (
    <div className="viora-stack">
      <PageHeader
        title={pageTitle}
        subtitle={pageSubtitle}
        actions={
          activeOffering ? (
            <div className="viora-row">
              <button
                type="button"
                className="viora-btn viora-btn-sm"
                onClick={() => setRosterMode("enrolments")}
              >
                Class Roster
              </button>
              <button
                type="button"
                className="viora-btn viora-btn-sm"
                onClick={() => setRosterMode("teachers")}
              >
                Co-Teachers
              </button>
            </div>
          ) : undefined
        }
      />

      {feedback.alerts}

      {offerings.length === 0 ? (
        <SectionCard title="No Assigned Teaching Subjects">
          <p className="viora-field-hint">
            You are not currently assigned to any active subject offerings in this institution.
          </p>
        </SectionCard>
      ) : (
        <>
          <SectionCard title="Assigned Subjects">
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
                    <strong style={{ color: "var(--color-brand-primary)" }}>{o.name}</strong>
                    <span className="viora-field-hint">
                      {selected ? "Selected subject" : "Select subject"}
                    </span>
                  </button>
                );
              })}
            </div>
          </SectionCard>

          {canUploadTeacher && offeringId && (
            <SectionCard title={`Add Material — ${activeOffering?.name ?? ""}`}>
              <form className="viora-stack" onSubmit={handleUpload}>
                <div className="viora-row">
                  <button
                    type="button"
                    className={`viora-btn viora-btn-sm ${mode === "text" ? "viora-btn-primary" : ""}`}
                    onClick={() => setMode("text")}
                  >
                    Notes / Topic Text
                  </button>
                  <button
                    type="button"
                    className={`viora-btn viora-btn-sm ${mode === "file" ? "viora-btn-primary" : ""}`}
                    onClick={() => setMode("file")}
                  >
                    PDF / Image Upload
                  </button>
                </div>
                <div className="viora-row">
                  <FormField label="Title" htmlFor="src-title">
                    <input
                      id="src-title"
                      className="viora-input"
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      required
                    />
                  </FormField>
                  {mode === "text" && (
                    <FormField label="Format" htmlFor="src-fmt">
                      <select
                        id="src-fmt"
                        className="viora-select"
                        value={inputFormat}
                        onChange={(e) =>
                          setInputFormat(
                            e.target.value as
                              | "plain_text"
                              | "typed_notes"
                              | "topic_description",
                          )
                        }
                      >
                        <option value="typed_notes">Typed Notes</option>
                        <option value="plain_text">Plain Text</option>
                        <option value="topic_description">Topic Outline (Non-Evidentiary)</option>
                      </select>
                    </FormField>
                  )}
                  <FormField label="Sharing" htmlFor="src-vis">
                    <select
                      id="src-vis"
                      className="viora-select"
                      value={visibility}
                      onChange={(e) =>
                        setVisibility(
                          e.target.value as "offering_published" | "teacher_only",
                        )
                      }
                    >
                      <option value="offering_published">Shared with Class</option>
                      <option value="teacher_only">Teacher Only</option>
                    </select>
                  </FormField>
                </div>
                {mode === "text" ? (
                  <FormField label="Content" htmlFor="src-content">
                    <textarea
                      id="src-content"
                      className="viora-input"
                      rows={4}
                      value={content}
                      onChange={(e) => setContent(e.target.value)}
                      required
                    />
                  </FormField>
                ) : (
                  <FormField label="Document File (PDF, PNG, JPEG)" htmlFor="src-file">
                    <input
                      id="src-file"
                      type="file"
                      accept=".pdf,.png,.jpg,.jpeg"
                      onChange={(e) => setFileObj(e.target.files?.[0] ?? null)}
                      required
                    />
                  </FormField>
                )}
                <div>
                  <button
                    type="submit"
                    className="viora-btn viora-btn-primary"
                    disabled={submitting}
                  >
                    {submitting ? "Adding..." : "Add Material"}
                  </button>
                </div>
              </form>
            </SectionCard>
          )}

          {list.loading && <LoadingSpinner message="Loading subject materials..." />}
          {list.error && (
            <ErrorState
              title="Could not load subject materials"
              message={list.error.message}
              onRetry={list.reload}
            />
          )}

          {!list.loading && !list.error && offeringId && (
            <SectionCard title="Course Materials & Lessons">
              <DataTable
                rows={list.items}
                rowKey={(s) => s.source_id}
                emptyTitle="No materials yet"
                emptyDescription="Add notes or upload a PDF/image above to prepare lessons for this subject."
                columns={[
                  { key: "title", header: "Material", render: (s) => <strong>{s.title}</strong> },
                  {
                    key: "fmt",
                    header: "Format",
                    render: (s) => formatInputKind(s.latest_input_format),
                  },
                  {
                    key: "vis",
                    header: "Visibility",
                    render: (s) => (
                      <StatusBadge
                        tone={s.visibility === "offering_published" ? "success" : "neutral"}
                      >
                        {s.visibility === "offering_published"
                          ? "Shared with Class"
                          : s.visibility === "teacher_only"
                            ? "Teacher Draft"
                            : "Student Note"}
                      </StatusBadge>
                    ),
                  },
                  {
                    key: "open",
                    header: "Lesson",
                    render: (s) => (
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm viora-btn-primary"
                        onClick={() => setSelectedSource(s)}
                      >
                        {selectedSource?.source_id === s.source_id ? "Opened" : "Open"}
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

          {offeringId && (
            <OfferingQAPanel
              tenantId={tenantId}
              offeringId={offeringId}
              offeringName={activeOffering?.name ?? ""}
              isSubjectTeacher={flags.isSubjectTeacher}
            />
          )}

          {policy && canUploadTeacher && (
            <details className="viora-details">
              <summary>Advanced Subject Settings</summary>
              <div className="viora-row" style={{ justifyContent: "space-between" }}>
                <span style={{ fontSize: "0.85rem" }}>
                  Student personal note submissions:{" "}
                  <strong>
                    {policy.student_submissions_enabled ? "Enabled" : "Disabled"}
                  </strong>
                </span>
                <button
                  type="button"
                  className="viora-btn viora-btn-sm"
                  onClick={() => void toggleStudentSubmissions()}
                >
                  {policy.student_submissions_enabled
                    ? "Disable Student Notes"
                    : "Enable Student Notes"}
                </button>
              </div>
            </details>
          )}
        </>
      )}

      {tenantId && activeOffering && rosterMode && (
        <AssignmentModal
          open
          title={`${rosterMode === "teachers" ? "Co-Teachers" : "Class Roster"} — ${activeOffering.name}`}
          subtitle={
            rosterMode === "teachers"
              ? "Teachers assigned to this subject offering."
              : "Students enrolled in this subject offering."
          }
          canManage={flags.isAdmin}
          eligibleRoleFilter={rosterMode === "teachers" ? "subject_teacher" : "student"}
          assignActionLabel={rosterMode === "teachers" ? "Assign teacher" : "Enrol student"}
          revokeActionLabel={rosterMode === "teachers" ? "Revoke teacher" : "Withdraw"}
          members={options.members}
          onClose={() => setRosterMode(null)}
          listFn={() =>
            rosterMode === "teachers"
              ? apiClient.GET("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/teachers", {
                  params: {
                    path: { tenant_id: tenantId, offering_id: activeOffering.id },
                    query: { limit: 100 },
                  },
                })
              : apiClient.GET(
                  "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/enrolments",
                  {
                    params: {
                      path: { tenant_id: tenantId, offering_id: activeOffering.id },
                      query: { limit: 100 },
                    },
                  },
                )
          }
          assignFn={(userId) =>
            rosterMode === "teachers"
              ? apiClient.POST("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/teachers", {
                  params: { path: { tenant_id: tenantId, offering_id: activeOffering.id } },
                  body: { user_id: userId },
                })
              : apiClient.POST(
                  "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/enrolments",
                  {
                    params: { path: { tenant_id: tenantId, offering_id: activeOffering.id } },
                    body: { user_id: userId },
                  },
                )
          }
          revokeFn={(userId) =>
            rosterMode === "teachers"
              ? apiClient.POST(
                  "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/teachers/{user_id}/revoke",
                  {
                    params: {
                      path: {
                        tenant_id: tenantId,
                        offering_id: activeOffering.id,
                        user_id: userId,
                      },
                    },
                  },
                )
              : apiClient.POST(
                  "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/enrolments/{user_id}/withdraw",
                  {
                    params: {
                      path: {
                        tenant_id: tenantId,
                        offering_id: activeOffering.id,
                        user_id: userId,
                      },
                    },
                  },
                )
          }
        />
      )}
    </div>
  );
}
