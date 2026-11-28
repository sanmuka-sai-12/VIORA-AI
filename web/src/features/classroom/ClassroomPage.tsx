import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  normalizeApiError,
  type AssignedOfferingSummary,
  type ClassroomAnnouncementSummary,
  type NormalizedApiError,
  type SourceSummary,
  type StudentProfileSummary,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import type { AppRouteId } from "../../auth/routeGuard";
import { ErrorState, LoadingSpinner } from "../../components/common/FeedbackStates";
import {
  ConfirmDialog,
  DataTable,
  FormField,
  ModalDialog,
  PageHeader,
  SectionCard,
  StatusBadge,
} from "../../components/common/UiPrimitives";
import { deriveRoleFlags } from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";
import { AssignmentModal } from "../common/AssignmentModal";
import { useActionFeedback } from "../common/useActionFeedback";
import { useTenantOptions } from "../common/useTenantOptions";
import { writeClassroomMaterialHandoff } from "../learning/learningSessionState";
import { OfferingQAPanel } from "../sources/OfferingQAPanel";
import { SourceKnowledgePanel } from "../sources/SourceKnowledgePanel";

type ClassroomTab = "overview" | "materials" | "announcements" | "students";

export function ClassroomPage({
  mode,
  initialOfferingId,
  onNavigate,
}: {
  mode: "teacher" | "student";
  initialOfferingId?: string;
  onNavigate?: (route: AppRouteId) => void;
}) {
  const { apiClient, controller: authController, snapshot: auth } = useAuth();
  const { snapshot: tenant } = useTenant();
  const tenantId = tenant.selectedTenantId;
  const userId = auth.user?.user_id;
  const flags = deriveRoleFlags(tenant.selectedInstitution?.roles ?? []);
  const feedback = useActionFeedback();
  const options = useTenantOptions({ members: mode === "teacher" });

  const [classrooms, setClassrooms] = useState<AssignedOfferingSummary[]>([]);
  const [loadingClassrooms, setLoadingClassrooms] = useState(false);
  const [classroomsError, setClassroomsError] =
    useState<NormalizedApiError | null>(null);
  const [selectedOfferingId, setSelectedOfferingId] = useState("");
  const [activeTab, setActiveTab] = useState<ClassroomTab>("overview");

  // Materials state
  const [materials, setMaterials] = useState<SourceSummary[]>([]);
  const [materialsLoading, setMaterialsLoading] = useState(false);
  const [folderFilter, setFolderFilter] = useState("all");
  const [uploadMode, setUploadMode] = useState<"file" | "text">("file");
  const [matTitle, setMatTitle] = useState("");
  const [matFolder, setMatFolder] = useState("");
  const [matFormat, setMatFormat] = useState<
    "typed_notes" | "plain_text" | "topic_description"
  >("typed_notes");
  const [matContent, setMatContent] = useState("");
  const [matFile, setMatFile] = useState<File | null>(null);
  const [matPublishNow, setMatPublishNow] = useState(true);
  const [uploadingMat, setUploadingMat] = useState(false);
  const [editingMaterial, setEditingMaterial] = useState<SourceSummary | null>(null);
  const [editMatTitle, setEditMatTitle] = useState("");
  const [editMatFolder, setEditMatFolder] = useState("");
  const [showAiToolsFor, setShowAiToolsFor] = useState<SourceSummary | null>(null);
  const [learningImportId, setLearningImportId] = useState<string | null>(null);

  // Announcements state
  const [announcements, setAnnouncements] = useState<
    ClassroomAnnouncementSummary[]
  >([]);
  const [announcementsLoading, setAnnouncementsLoading] = useState(false);
  const [annTitle, setAnnTitle] = useState("");
  const [annBody, setAnnBody] = useState("");
  const [annPinned, setAnnPinned] = useState(false);
  const [savingAnn, setSavingAnn] = useState(false);
  const [editingAnn, setEditingAnn] =
    useState<ClassroomAnnouncementSummary | null>(null);
  const [editAnnTitle, setEditAnnTitle] = useState("");
  const [editAnnBody, setEditAnnBody] = useState("");
  const [editAnnPinned, setEditAnnPinned] = useState(false);
  const [deletingAnn, setDeletingAnn] =
    useState<ClassroomAnnouncementSummary | null>(null);

  // Students roster state (teacher/admin only)
  const [roster, setRoster] = useState<StudentProfileSummary[]>([]);
  const [rosterLoading, setRosterLoading] = useState(false);
  const [rosterSearch, setRosterSearch] = useState("");
  const [showCoTeachers, setShowCoTeachers] = useState(false);

  const isTeacherView = mode === "teacher" && (flags.isSubjectTeacher || flags.isAdmin);
  const activeClassroom =
    classrooms.find((c) => c.offering_id === selectedOfferingId) ?? null;

  const loadClassrooms = useCallback(async () => {
    if (!tenantId) return;
    setLoadingClassrooms(true);
    setClassroomsError(null);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.GET("/api/v1/tenants/{tenant_id}/my-classrooms", {
          params: { path: { tenant_id: tenantId } },
        }),
      );
      if (res.data) {
        const list =
          mode === "teacher"
            ? res.data.teaching_offerings
            : res.data.enrolled_offerings;
        setClassrooms(list);
        setSelectedOfferingId((prev) =>
          initialOfferingId && list.some((c) => c.offering_id === initialOfferingId)
            ? initialOfferingId
            : prev && list.some((c) => c.offering_id === prev)
              ? prev
              : list[0]?.offering_id ?? "",
        );
      } else {
        setClassroomsError(
          normalizeApiError(
            res.error,
            res.response.status,
            "Unable to load your classrooms.",
          ),
        );
      }
    } finally {
      setLoadingClassrooms(false);
    }
  }, [apiClient, authController, initialOfferingId, mode, tenantId]);

  useEffect(() => {
    void loadClassrooms();
  }, [loadClassrooms]);

  const loadMaterials = useCallback(async () => {
    if (!tenantId || !selectedOfferingId) {
      setMaterials([]);
      return;
    }
    setMaterialsLoading(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.GET("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/sources", {
          params: {
            path: { tenant_id: tenantId, offering_id: selectedOfferingId },
            query: { limit: 100 },
          },
        }),
      );
      if (res.data) {
        setMaterials(
          res.data.items.filter(
            (m) =>
              m.origin_kind === "teacher_resource" &&
              (isTeacherView ||
                (m.visibility === "offering_published" &&
                  m.lifecycle_state === "active")),
          ),
        );
      }
    } finally {
      setMaterialsLoading(false);
    }
  }, [apiClient, authController, isTeacherView, selectedOfferingId, tenantId]);

  const loadAnnouncements = useCallback(async () => {
    if (!tenantId || !selectedOfferingId) {
      setAnnouncements([]);
      return;
    }
    setAnnouncementsLoading(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.GET(
          "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/announcements",
          {
            params: {
              path: { tenant_id: tenantId, offering_id: selectedOfferingId },
              query: { limit: 100 },
            },
          },
        ),
      );
      if (res.data) {
        setAnnouncements(res.data.items);
      }
    } finally {
      setAnnouncementsLoading(false);
    }
  }, [apiClient, authController, selectedOfferingId, tenantId]);

  const loadRoster = useCallback(async () => {
    if (!tenantId || !selectedOfferingId || !isTeacherView) {
      setRoster([]);
      return;
    }
    setRosterLoading(true);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.GET("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/roster", {
          params: {
            path: { tenant_id: tenantId, offering_id: selectedOfferingId },
            query: { q: rosterSearch.trim() || undefined, limit: 200 },
          },
        }),
      );
      if (res.data) {
        setRoster(res.data.items);
      }
    } finally {
      setRosterLoading(false);
    }
  }, [apiClient, authController, isTeacherView, rosterSearch, selectedOfferingId, tenantId]);

  useEffect(() => {
    setFolderFilter("all");
    setShowAiToolsFor(null);
    void loadMaterials();
    void loadAnnouncements();
    if (isTeacherView) {
      void loadRoster();
    }
  }, [isTeacherView, loadAnnouncements, loadMaterials, loadRoster]);

  const handleUploadMaterial = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId || !selectedOfferingId) return;
    if (!matTitle.trim()) {
      feedback.setValidationError("Material title is required.");
      return;
    }
    if (uploadMode === "file" && !matFile) {
      feedback.setValidationError("Select a file to upload.");
      return;
    }
    if (uploadMode === "text" && !matContent.trim()) {
      feedback.setValidationError("Enter study material content.");
      return;
    }

    const visibility = matPublishNow ? "offering_published" : "teacher_only";
    await feedback.runApiAction({
      setBusy: setUploadingMat,
      call: async () => {
        if (uploadMode === "text") {
          return apiClient.POST(
            "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/sources",
            {
              params: { path: { tenant_id: tenantId, offering_id: selectedOfferingId } },
              body: {
                title: matTitle.trim(),
                input_format: matFormat,
                content: matContent,
                origin_kind: "teacher_resource",
                visibility,
                folder_label: matFolder.trim() || null,
              },
            },
          );
        }
        const formData = new FormData();
        formData.append("title", matTitle.trim());
        formData.append("origin_kind", "teacher_resource");
        formData.append("visibility", visibility);
        if (matFolder.trim()) {
          formData.append("folder_label", matFolder.trim());
        }
        formData.append("file", matFile!);
        return apiClient.POST(
          "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/sources/files",
          {
            params: { path: { tenant_id: tenantId, offering_id: selectedOfferingId } },
            body: formData as never,
          },
        );
      },
      fallbackError: "Unable to upload study material.",
      onSuccess: () => {
        setMatTitle("");
        setMatFolder("");
        setMatContent("");
        setMatFile(null);
        feedback.setActionSuccess(
          matPublishNow
            ? "Uploaded and published study material to the classroom."
            : "Uploaded study material as a teacher draft.",
        );
        void loadMaterials();
      },
    });
  };

  const handleUpdateMaterial = async (
    sourceId: string,
    patch: {
      title?: string;
      folder_label?: string | null;
      visibility?: "teacher_only" | "offering_published";
      lifecycle_state?: "active" | "archived";
    },
    successMessage: string,
  ) => {
    if (!tenantId) return;
    await feedback.runApiAction({
      setBusy: setUploadingMat,
      call: () =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/sources/{source_id}", {
          params: { path: { tenant_id: tenantId, source_id: sourceId } },
          body: patch,
        }),
      fallbackError: "Unable to update study material.",
      onSuccess: () => {
        feedback.setActionSuccess(successMessage);
        setEditingMaterial(null);
        void loadMaterials();
      },
    });
  };

  const handleDownloadMaterial = async (material: SourceSummary) => {
    if (!tenantId) return;
    const res = await authController.runWithFreshToken(() =>
      apiClient.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/download", {
        params: { path: { tenant_id: tenantId, source_id: material.source_id } },
        parseAs: "blob",
      }),
    );
    if (!res.response.ok || !res.data) {
      feedback.setValidationError("Unable to open this study material.");
      return;
    }
    const blob = res.data as Blob;
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener,noreferrer");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };

  const handleLearnWithViora = async (material: SourceSummary) => {
    if (!tenantId || !userId || !onNavigate) return;
    setLearningImportId(material.source_id);
    try {
      const res = await authController.runWithFreshToken(() =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/learning/sources/{source_id}/import",
          {
            params: {
              path: { tenant_id: tenantId, source_id: material.source_id },
            },
          },
        ),
      );
      if (res.data) {
        writeClassroomMaterialHandoff(userId, tenantId, {
          attachment: res.data,
          classroomTitle: activeClassroom?.offering_name,
        });
        onNavigate("student-learning");
      } else {
        const err = normalizeApiError(
          res.error,
          res.response.status,
          "Unable to open material in Personal Learning.",
        );
        feedback.setValidationError(err.message);
      }
    } finally {
      setLearningImportId(null);
    }
  };

  const handleCreateAnnouncement = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId || !selectedOfferingId) return;
    if (!annTitle.trim() || !annBody.trim()) {
      feedback.setValidationError("Announcement title and message are required.");
      return;
    }
    await feedback.runApiAction({
      setBusy: setSavingAnn,
      call: () =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/announcements",
          {
            params: { path: { tenant_id: tenantId, offering_id: selectedOfferingId } },
            body: {
              title: annTitle.trim(),
              body: annBody.trim(),
              pinned: annPinned,
            },
          },
        ),
      fallbackError: "Unable to post announcement.",
      onSuccess: () => {
        setAnnTitle("");
        setAnnBody("");
        setAnnPinned(false);
        feedback.setActionSuccess("Classroom announcement posted.");
        void loadAnnouncements();
      },
    });
  };

  const handleSaveAnnouncement = async (e: FormEvent) => {
    e.preventDefault();
    if (!tenantId || !selectedOfferingId || !editingAnn) return;
    await feedback.runApiAction({
      setBusy: setSavingAnn,
      call: () =>
        apiClient.PATCH(
          "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/announcements/{announcement_id}",
          {
            params: {
              path: {
                tenant_id: tenantId,
                offering_id: selectedOfferingId,
                announcement_id: editingAnn.id,
              },
            },
            body: {
              title: editAnnTitle.trim(),
              body: editAnnBody.trim(),
              pinned: editAnnPinned,
            },
          },
        ),
      fallbackError: "Unable to update announcement.",
      onSuccess: () => {
        setEditingAnn(null);
        feedback.setActionSuccess("Announcement updated.");
        void loadAnnouncements();
      },
    });
  };

  const handleDeleteAnnouncement = async () => {
    if (!tenantId || !selectedOfferingId || !deletingAnn) return;
    await feedback.runApiAction({
      setBusy: setSavingAnn,
      call: () =>
        apiClient.DELETE(
          "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/announcements/{announcement_id}",
          {
            params: {
              path: {
                tenant_id: tenantId,
                offering_id: selectedOfferingId,
                announcement_id: deletingAnn.id,
              },
            },
          },
        ),
      fallbackError: "Unable to delete announcement.",
      onSuccess: () => {
        setDeletingAnn(null);
        feedback.setActionSuccess("Announcement deleted.");
        void loadAnnouncements();
      },
    });
  };

  if (!tenantId) return null;

  const folders = Array.from(
    new Set(
      materials
        .map((m) => m.folder_label?.trim())
        .filter((v): v is string => Boolean(v)),
    ),
  ).sort((a, b) => a.localeCompare(b));

  const visibleMaterials = materials.filter((m) => {
    if (folderFilter === "all") return true;
    if (folderFilter === "__unorganized__") return !m.folder_label;
    return m.folder_label === folderFilter;
  });

  const publishedCount = materials.filter(
    (m) => m.visibility === "offering_published" && m.lifecycle_state === "active",
  ).length;

  return (
    <div className="viora-stack">
      <PageHeader
        title={isTeacherView ? "My Teaching Subjects" : "My Classrooms"}
        subtitle={
          isTeacherView
            ? "Subject-section classrooms for study materials, announcements, and enrolled students."
            : "Enrolled subject-section classrooms, published study materials, and announcements."
        }
        actions={
          !isTeacherView && onNavigate ? (
            <button
              type="button"
              className="viora-btn viora-btn-sm viora-btn-primary"
              onClick={() => onNavigate("student-learning")}
            >
              Open Personal Learning
            </button>
          ) : undefined
        }
      />

      {feedback.alerts}

      {loadingClassrooms && <LoadingSpinner message="Loading classrooms..." />}
      {!loadingClassrooms && classroomsError && (
        <ErrorState
          title="Could not load classrooms"
          message={classroomsError.message}
          onRetry={() => void loadClassrooms()}
        />
      )}

      {!loadingClassrooms && !classroomsError && classrooms.length === 0 && (
        <SectionCard title="No Classrooms Available">
          <p className="viora-muted" style={{ margin: 0 }}>
            {isTeacherView
              ? "You are not currently assigned to any active subject-section offerings."
              : "You are not currently enrolled in any active subject-section offerings."}
          </p>
        </SectionCard>
      )}

      {!loadingClassrooms && !classroomsError && classrooms.length > 0 && (
        <>
          <SectionCard title="Subject-Section Classrooms">
            <div className="viora-subject-grid">
              {classrooms.map((c) => {
                const selected = c.offering_id === selectedOfferingId;
                return (
                  <button
                    key={c.offering_id}
                    type="button"
                    className={`viora-subject-card ${selected ? "is-selected" : ""}`}
                    onClick={() => setSelectedOfferingId(c.offering_id)}
                  >
                    <strong style={{ color: "var(--color-brand-primary)" }}>
                      {c.offering_name}
                    </strong>
                    <span className="viora-field-hint">
                      {c.section_name} · {c.semester_name} ({c.academic_year_name})
                    </span>
                  </button>
                );
              })}
            </div>
          </SectionCard>

          {activeClassroom && (
            <>
              <div className="viora-row" style={{ gap: "0.5rem", flexWrap: "wrap" }}>
                {(
                  [
                    { id: "overview", label: "Overview" },
                    {
                      id: "materials",
                      label: `Study Materials (${visibleMaterials.length})`,
                    },
                    {
                      id: "announcements",
                      label: `Announcements (${announcements.length})`,
                    },
                    ...(isTeacherView
                      ? [{ id: "students" as const, label: `Students (${roster.length})` }]
                      : []),
                  ] as const
                ).map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    className={`viora-btn ${
                      activeTab === tab.id ? "viora-btn-primary" : ""
                    }`}
                    onClick={() => setActiveTab(tab.id)}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              {activeTab === "overview" && (
                <SectionCard
                  title={activeClassroom.offering_name}
                  subtitle={`${activeClassroom.subject_name} · ${activeClassroom.section_name}`}
                  actions={
                    isTeacherView ? (
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm"
                        onClick={() => setShowCoTeachers(true)}
                      >
                        Co-Teachers
                      </button>
                    ) : undefined
                  }
                >
                  <div className="viora-grid viora-grid-3">
                    <div>
                      <div className="viora-muted" style={{ fontSize: "0.78rem" }}>
                        Subject
                      </div>
                      <strong>{activeClassroom.subject_name}</strong>
                    </div>
                    <div>
                      <div className="viora-muted" style={{ fontSize: "0.78rem" }}>
                        Class Section
                      </div>
                      <strong>{activeClassroom.section_name}</strong>
                    </div>
                    <div>
                      <div className="viora-muted" style={{ fontSize: "0.78rem" }}>
                        Academic Term
                      </div>
                      <strong>
                        {activeClassroom.academic_year_name} ·{" "}
                        {activeClassroom.semester_name}
                      </strong>
                    </div>
                    <div>
                      <div className="viora-muted" style={{ fontSize: "0.78rem" }}>
                        Department
                      </div>
                      <strong>{activeClassroom.department_name ?? "—"}</strong>
                    </div>
                    <div>
                      <div className="viora-muted" style={{ fontSize: "0.78rem" }}>
                        Published Materials
                      </div>
                      <strong>{publishedCount}</strong>
                    </div>
                    <div>
                      <div className="viora-muted" style={{ fontSize: "0.78rem" }}>
                        Announcements
                      </div>
                      <strong>{announcements.length}</strong>
                    </div>
                  </div>
                </SectionCard>
              )}

              {activeTab === "materials" && (
                <div className="viora-stack">
                  {isTeacherView && (
                    <SectionCard
                      title="Upload Study Material"
                      subtitle="Upload and publish original PDFs, images, or notes directly without mandatory AI processing."
                    >
                      <form onSubmit={handleUploadMaterial} className="viora-stack">
                        <div className="viora-row">
                          <button
                            type="button"
                            className={`viora-btn viora-btn-sm ${
                              uploadMode === "file" ? "viora-btn-primary" : ""
                            }`}
                            onClick={() => setUploadMode("file")}
                          >
                            PDF / Image File
                          </button>
                          <button
                            type="button"
                            className={`viora-btn viora-btn-sm ${
                              uploadMode === "text" ? "viora-btn-primary" : ""
                            }`}
                            onClick={() => setUploadMode("text")}
                          >
                            Notes / Text
                          </button>
                        </div>

                        <div className="viora-grid viora-grid-2">
                          <FormField label="Material Title *" htmlFor="cls-mat-title">
                            <input
                              id="cls-mat-title"
                              type="text"
                              className="viora-input"
                              placeholder="Lecture 01 — Asymptotic Notation"
                              value={matTitle}
                              onChange={(e) => setMatTitle(e.target.value)}
                              disabled={uploadingMat}
                              required
                            />
                          </FormField>
                          <FormField
                            label="Unit / Folder (optional)"
                            htmlFor="cls-mat-folder"
                          >
                            <input
                              id="cls-mat-folder"
                              type="text"
                              className="viora-input"
                              placeholder="Unit 1: Foundations"
                              value={matFolder}
                              onChange={(e) => setMatFolder(e.target.value)}
                              disabled={uploadingMat}
                            />
                          </FormField>
                        </div>

                        {uploadMode === "file" ? (
                          <FormField label="Select File (PDF, PNG, JPEG)" htmlFor="cls-mat-file">
                            <input
                              id="cls-mat-file"
                              type="file"
                              accept=".pdf,image/png,image/jpeg"
                              className="viora-input"
                              onChange={(e) => setMatFile(e.target.files?.[0] ?? null)}
                              disabled={uploadingMat}
                            />
                          </FormField>
                        ) : (
                          <>
                            <FormField label="Format" htmlFor="cls-mat-fmt">
                              <select
                                id="cls-mat-fmt"
                                className="viora-select"
                                value={matFormat}
                                onChange={(e) =>
                                  setMatFormat(
                                    e.target.value as
                                      | "typed_notes"
                                      | "plain_text"
                                      | "topic_description",
                                  )
                                }
                                disabled={uploadingMat}
                              >
                                <option value="typed_notes">Typed Notes</option>
                                <option value="plain_text">Plain Text</option>
                                <option value="topic_description">Topic Outline</option>
                              </select>
                            </FormField>
                            <FormField label="Content *" htmlFor="cls-mat-content">
                              <textarea
                                id="cls-mat-content"
                                className="viora-textarea"
                                rows={4}
                                value={matContent}
                                onChange={(e) => setMatContent(e.target.value)}
                                disabled={uploadingMat}
                                required
                              />
                            </FormField>
                          </>
                        )}

                        <div
                          className="viora-row"
                          style={{ justifyContent: "space-between", alignItems: "center" }}
                        >
                          <label className="viora-row" style={{ gap: "0.45rem", cursor: "pointer" }}>
                            <input
                              type="checkbox"
                              checked={matPublishNow}
                              onChange={(e) => setMatPublishNow(e.target.checked)}
                              disabled={uploadingMat}
                            />
                            <span>Publish immediately to enrolled students</span>
                          </label>
                          <button
                            type="submit"
                            className="viora-btn viora-btn-primary"
                            disabled={uploadingMat}
                          >
                            {uploadingMat ? "Uploading..." : "Upload Material"}
                          </button>
                        </div>
                      </form>
                    </SectionCard>
                  )}

                  <SectionCard
                    title={`Study Materials (${visibleMaterials.length})`}
                    actions={
                      folders.length > 0 ? (
                        <select
                          className="viora-select viora-select-inline"
                          aria-label="Filter materials by folder"
                          value={folderFilter}
                          onChange={(e) => setFolderFilter(e.target.value)}
                        >
                          <option value="all">All folders</option>
                          {folders.map((f) => (
                            <option key={f} value={f}>
                              {f}
                            </option>
                          ))}
                          <option value="__unorganized__">Unfiled</option>
                        </select>
                      ) : undefined
                    }
                  >
                    {materialsLoading ? (
                      <LoadingSpinner message="Loading study materials..." />
                    ) : (
                      <DataTable
                        rows={visibleMaterials}
                        rowKey={(m) => m.source_id}
                        emptyTitle="No study materials"
                        emptyDescription={
                          isTeacherView
                            ? "Upload study materials above to share them with this classroom."
                            : "Your teacher has not published any study materials here yet."
                        }
                        columns={[
                          {
                            key: "title",
                            header: "Material",
                            render: (m) => (
                              <div>
                                <strong>{m.title}</strong>
                                <div className="viora-muted" style={{ fontSize: "0.8rem" }}>
                                  {[m.folder_label, m.latest_input_format]
                                    .filter(Boolean)
                                    .join(" · ")}
                                </div>
                              </div>
                            ),
                          },
                          ...(isTeacherView
                            ? [
                                {
                                  key: "status",
                                  header: "Visibility",
                                  render: (m: SourceSummary) => (
                                    <div className="viora-row" style={{ gap: "0.35rem" }}>
                                      <StatusBadge
                                        tone={
                                          m.visibility === "offering_published"
                                            ? "success"
                                            : "warning"
                                        }
                                      >
                                        {m.visibility === "offering_published"
                                          ? "Published"
                                          : "Draft"}
                                      </StatusBadge>
                                      {m.lifecycle_state === "archived" && (
                                        <StatusBadge tone="neutral">Archived</StatusBadge>
                                      )}
                                    </div>
                                  ),
                                },
                              ]
                            : []),
                          {
                            key: "actions",
                            header: "Actions",
                            render: (m) => (
                              <div className="viora-row" style={{ flexWrap: "wrap" }}>
                                <button
                                  type="button"
                                  className="viora-btn viora-btn-sm"
                                  onClick={() => void handleDownloadMaterial(m)}
                                >
                                  View / Download
                                </button>
                                {!isTeacherView && (
                                  <button
                                    type="button"
                                    className="viora-btn viora-btn-sm viora-btn-primary"
                                    disabled={learningImportId === m.source_id}
                                    onClick={() => void handleLearnWithViora(m)}
                                  >
                                    {learningImportId === m.source_id
                                      ? "Opening..."
                                      : "Learn with VIORA AI"}
                                  </button>
                                )}
                                {isTeacherView && (
                                  <>
                                    {m.visibility !== "offering_published" ? (
                                      <button
                                        type="button"
                                        className="viora-btn viora-btn-sm viora-btn-primary"
                                        onClick={() =>
                                          void handleUpdateMaterial(
                                            m.source_id,
                                            { visibility: "offering_published" },
                                            `Published "${m.title}".`,
                                          )
                                        }
                                      >
                                        Publish
                                      </button>
                                    ) : (
                                      <button
                                        type="button"
                                        className="viora-btn viora-btn-sm"
                                        onClick={() =>
                                          void handleUpdateMaterial(
                                            m.source_id,
                                            { visibility: "teacher_only" },
                                            `Withdrew "${m.title}" from student view.`,
                                          )
                                        }
                                      >
                                        Withdraw
                                      </button>
                                    )}
                                    <button
                                      type="button"
                                      className="viora-btn viora-btn-sm"
                                      onClick={() => {
                                        setEditingMaterial(m);
                                        setEditMatTitle(m.title);
                                        setEditMatFolder(m.folder_label ?? "");
                                      }}
                                    >
                                      Organize
                                    </button>
                                    <button
                                      type="button"
                                      className="viora-btn viora-btn-sm"
                                      onClick={() =>
                                        setShowAiToolsFor((prev) =>
                                          prev?.source_id === m.source_id ? null : m,
                                        )
                                      }
                                    >
                                      {showAiToolsFor?.source_id === m.source_id
                                        ? "Hide AI Tools"
                                        : "AI Tools"}
                                    </button>
                                  </>
                                )}
                              </div>
                            ),
                          },
                        ]}
                      />
                    )}
                  </SectionCard>

                  {isTeacherView && showAiToolsFor && activeClassroom && (
                    <div className="viora-stack">
                      <SourceKnowledgePanel
                        tenantId={tenantId}
                        source={showAiToolsFor}
                        isSubjectTeacher={flags.isSubjectTeacher}
                        policy={null}
                        onSourceUpdated={() => void loadMaterials()}
                      />
                      <OfferingQAPanel
                        tenantId={tenantId}
                        offeringId={selectedOfferingId}
                        offeringName={activeClassroom.offering_name}
                        isSubjectTeacher={flags.isSubjectTeacher}
                      />
                    </div>
                  )}
                </div>
              )}

              {activeTab === "announcements" && (
                <div className="viora-stack">
                  {isTeacherView && (
                    <SectionCard title="Post Classroom Announcement">
                      <form onSubmit={handleCreateAnnouncement} className="viora-stack">
                        <FormField label="Title *" htmlFor="cls-ann-title">
                          <input
                            id="cls-ann-title"
                            type="text"
                            className="viora-input"
                            placeholder="Midterm review session schedule"
                            value={annTitle}
                            onChange={(e) => setAnnTitle(e.target.value)}
                            disabled={savingAnn}
                            required
                          />
                        </FormField>
                        <FormField label="Message *" htmlFor="cls-ann-body">
                          <textarea
                            id="cls-ann-body"
                            className="viora-textarea"
                            rows={3}
                            placeholder="Share updates, reading reminders, or schedule notes..."
                            value={annBody}
                            onChange={(e) => setAnnBody(e.target.value)}
                            disabled={savingAnn}
                            required
                          />
                        </FormField>
                        <div
                          className="viora-row"
                          style={{ justifyContent: "space-between", alignItems: "center" }}
                        >
                          <label className="viora-row" style={{ gap: "0.45rem", cursor: "pointer" }}>
                            <input
                              type="checkbox"
                              checked={annPinned}
                              onChange={(e) => setAnnPinned(e.target.checked)}
                              disabled={savingAnn}
                            />
                            <span>Pin announcement to top</span>
                          </label>
                          <button
                            type="submit"
                            className="viora-btn viora-btn-primary"
                            disabled={savingAnn}
                          >
                            {savingAnn ? "Posting..." : "Post Announcement"}
                          </button>
                        </div>
                      </form>
                    </SectionCard>
                  )}

                  <SectionCard title={`Announcements (${announcements.length})`}>
                    {announcementsLoading ? (
                      <LoadingSpinner message="Loading announcements..." />
                    ) : announcements.length === 0 ? (
                      <p className="viora-muted" style={{ margin: 0 }}>
                        No announcements have been posted in this classroom yet.
                      </p>
                    ) : (
                      <div className="viora-stack">
                        {announcements.map((a) => (
                          <div
                            key={a.id}
                            style={{
                              padding: "1rem",
                              borderRadius: "var(--radius-sm)",
                              border: "1px solid var(--color-border)",
                              background: "var(--color-bg-subtle)",
                            }}
                          >
                            <div
                              className="viora-row"
                              style={{
                                justifyContent: "space-between",
                                alignItems: "flex-start",
                              }}
                            >
                              <div>
                                <div className="viora-row" style={{ gap: "0.5rem" }}>
                                  <strong>{a.title}</strong>
                                  {a.pinned && <StatusBadge tone="brand">Pinned</StatusBadge>}
                                </div>
                                <div className="viora-muted" style={{ fontSize: "0.8rem" }}>
                                  {a.author_name} ·{" "}
                                  {new Date(a.created_at).toLocaleString()}
                                </div>
                              </div>
                              {isTeacherView && (
                                <div className="viora-row">
                                  <button
                                    type="button"
                                    className="viora-btn viora-btn-sm"
                                    onClick={() => {
                                      setEditingAnn(a);
                                      setEditAnnTitle(a.title);
                                      setEditAnnBody(a.body);
                                      setEditAnnPinned(a.pinned);
                                    }}
                                  >
                                    Edit
                                  </button>
                                  <button
                                    type="button"
                                    className="viora-btn viora-btn-sm viora-btn-danger"
                                    onClick={() => setDeletingAnn(a)}
                                  >
                                    Delete
                                  </button>
                                </div>
                              )}
                            </div>
                            <p style={{ margin: "0.6rem 0 0", whiteSpace: "pre-wrap" }}>
                              {a.body}
                            </p>
                          </div>
                        ))}
                      </div>
                    )}
                  </SectionCard>
                </div>
              )}

              {activeTab === "students" && isTeacherView && (
                <SectionCard
                  title={`Enrolled Students (${roster.length})`}
                  subtitle="Ordered by roll number."
                  actions={
                    <input
                      type="search"
                      className="viora-input"
                      style={{ width: "220px" }}
                      placeholder="Search roll no. or name..."
                      aria-label="Search enrolled students"
                      value={rosterSearch}
                      onChange={(e) => setRosterSearch(e.target.value)}
                    />
                  }
                >
                  {rosterLoading ? (
                    <LoadingSpinner message="Loading enrolled students..." />
                  ) : (
                    <DataTable
                      rows={roster}
                      rowKey={(s) => s.user_id}
                      emptyTitle="No enrolled students"
                      emptyDescription="No active students are currently enrolled in this offering."
                      columns={[
                        {
                          key: "roll",
                          header: "Roll Number",
                          render: (s) =>
                            s.roll_number ? (
                              <strong>{s.roll_number}</strong>
                            ) : (
                              <span className="viora-muted">—</span>
                            ),
                        },
                        {
                          key: "name",
                          header: "Student Name",
                          render: (s) => <strong>{s.display_name}</strong>,
                        },
                        {
                          key: "email",
                          header: "Email",
                          render: (s) => s.email ?? "—",
                        },
                      ]}
                    />
                  )}
                </SectionCard>
              )}
            </>
          )}
        </>
      )}

      <ModalDialog
        open={editingMaterial !== null}
        title="Organize Study Material"
        onClose={() => setEditingMaterial(null)}
      >
        {editingMaterial && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void handleUpdateMaterial(
                editingMaterial.source_id,
                {
                  title: editMatTitle.trim(),
                  folder_label: editMatFolder.trim() || null,
                },
                `Updated "${editMatTitle.trim()}".`,
              );
            }}
            className="viora-stack"
          >
            <FormField label="Material Title *" htmlFor="edit-mat-title">
              <input
                id="edit-mat-title"
                type="text"
                className="viora-input"
                value={editMatTitle}
                onChange={(e) => setEditMatTitle(e.target.value)}
                disabled={uploadingMat}
                required
              />
            </FormField>
            <FormField label="Unit / Folder" htmlFor="edit-mat-folder">
              <input
                id="edit-mat-folder"
                type="text"
                className="viora-input"
                placeholder="e.g. Unit 2: Graph Algorithms"
                value={editMatFolder}
                onChange={(e) => setEditMatFolder(e.target.value)}
                disabled={uploadingMat}
              />
            </FormField>
            <div className="viora-row">
              <button
                type="submit"
                className="viora-btn viora-btn-primary"
                disabled={uploadingMat}
              >
                Save
              </button>
              <button
                type="button"
                className="viora-btn"
                onClick={() => setEditingMaterial(null)}
              >
                Cancel
              </button>
            </div>
          </form>
        )}
      </ModalDialog>

      <ModalDialog
        open={editingAnn !== null}
        title="Edit Announcement"
        onClose={() => setEditingAnn(null)}
      >
        <form onSubmit={handleSaveAnnouncement} className="viora-stack">
          <FormField label="Title *" htmlFor="edit-ann-title">
            <input
              id="edit-ann-title"
              type="text"
              className="viora-input"
              value={editAnnTitle}
              onChange={(e) => setEditAnnTitle(e.target.value)}
              disabled={savingAnn}
              required
            />
          </FormField>
          <FormField label="Message *" htmlFor="edit-ann-body">
            <textarea
              id="edit-ann-body"
              className="viora-textarea"
              rows={4}
              value={editAnnBody}
              onChange={(e) => setEditAnnBody(e.target.value)}
              disabled={savingAnn}
              required
            />
          </FormField>
          <label className="viora-row" style={{ gap: "0.45rem", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={editAnnPinned}
              onChange={(e) => setEditAnnPinned(e.target.checked)}
              disabled={savingAnn}
            />
            <span>Pin announcement</span>
          </label>
          <div className="viora-row">
            <button
              type="submit"
              className="viora-btn viora-btn-primary"
              disabled={savingAnn}
            >
              Save Changes
            </button>
            <button
              type="button"
              className="viora-btn"
              onClick={() => setEditingAnn(null)}
            >
              Cancel
            </button>
          </div>
        </form>
      </ModalDialog>

      <ConfirmDialog
        open={deletingAnn !== null}
        title="Delete announcement?"
        message={
          deletingAnn
            ? `Delete "${deletingAnn.title}" from this classroom?`
            : ""
        }
        confirmLabel="Delete"
        danger
        busy={savingAnn}
        onCancel={() => setDeletingAnn(null)}
        onConfirm={() => void handleDeleteAnnouncement()}
      />

      {tenantId && activeClassroom && showCoTeachers && (
        <AssignmentModal
          open
          title={`Co-Teachers — ${activeClassroom.offering_name}`}
          subtitle="Subject teachers assigned to this offering."
          canManage={flags.isAdmin}
          eligibleRoleFilter="subject_teacher"
          assignActionLabel="Assign teacher"
          revokeActionLabel="Revoke"
          members={options.members}
          onClose={() => setShowCoTeachers(false)}
          listFn={() =>
            apiClient.GET("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/teachers", {
              params: {
                path: { tenant_id: tenantId, offering_id: activeClassroom.offering_id },
                query: { limit: 100 },
              },
            })
          }
          assignFn={(uid) =>
            apiClient.POST("/api/v1/tenants/{tenant_id}/offerings/{offering_id}/teachers", {
              params: { path: { tenant_id: tenantId, offering_id: activeClassroom.offering_id } },
              body: { user_id: uid },
            })
          }
          revokeFn={(uid) =>
            apiClient.POST(
              "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/teachers/{user_id}/revoke",
              {
                params: {
                  path: {
                    tenant_id: tenantId,
                    offering_id: activeClassroom.offering_id,
                    user_id: uid,
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
