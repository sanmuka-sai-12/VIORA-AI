import { useCallback, useEffect, useState } from "react";
import {
  normalizeApiError,
  type AssignedOfferingSummary,
  type AssignedSectionSummary,
  type ClassroomAnnouncementSummary,
  type NormalizedApiError,
  type SourceSummary,
  type StudentProfileSummary,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import type { AppRouteId } from "../../auth/routeGuard";
import { ErrorState, LoadingSpinner } from "../../components/common/FeedbackStates";
import { StatusBadge } from "../../components/common/UiPrimitives";
import {
  InstitutionIcon,
  LibraryIcon,
  StudioSparkIcon,
} from "../../components/learning/LearningIcons";
import { useTenant } from "../../tenant/TenantContext";
import { useActionFeedback } from "../common/useActionFeedback";
import { writeClassroomMaterialHandoff } from "../learning/learningSessionState";

type DashboardTab =
  | "overview"
  | "subjects"
  | "materials"
  | "announcements"
  | "profile";

interface ClassroomMaterialItem extends SourceSummary {
  classroomName: string;
  sectionName: string;
}

interface ClassroomAnnouncementItem extends ClassroomAnnouncementSummary {
  classroomName: string;
  sectionName: string;
}

export function StudentInstitutionalDashboard({
  onNavigate,
  onOpenClassroom,
  onLogout,
  authBusy = false,
}: {
  onNavigate: (route: AppRouteId) => void;
  onOpenClassroom?: (offeringId: string) => void;
  onLogout?: () => void;
  authBusy?: boolean;
}) {
  const { apiClient, controller: authController, snapshot: auth } = useAuth();
  const { snapshot: tenant, controller: tenantController } = useTenant();
  const feedback = useActionFeedback();

  const selected = tenant.selectedInstitution;
  const tenantId = selected?.tenant_id ?? null;
  const userId = auth.user?.user_id;

  const [tab, setTab] = useState<DashboardTab>("overview");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<NormalizedApiError | null>(null);

  const [profile, setProfile] = useState<StudentProfileSummary | null>(null);
  const [offerings, setOfferings] = useState<AssignedOfferingSummary[]>([]);
  const [homeroomSection, setHomeroomSection] =
    useState<AssignedSectionSummary | null>(null);
  const [homeroomTeachers, setHomeroomTeachers] = useState<string[]>([]);
  const [materials, setMaterials] = useState<ClassroomMaterialItem[]>([]);
  const [announcements, setAnnouncements] = useState<ClassroomAnnouncementItem[]>(
    [],
  );
  const [subjectFilter, setSubjectFilter] = useState("all");
  const [importingId, setImportingId] = useState<string | null>(null);

  const loadDashboard = useCallback(async () => {
    if (!tenantId || !userId) return;
    setLoading(true);
    setError(null);
    try {
      const [clsRes, profRes] = await Promise.all([
        authController.runWithFreshToken(() =>
          apiClient.GET("/api/v1/tenants/{tenant_id}/my-classrooms", {
            params: { path: { tenant_id: tenantId } },
          }),
        ),
        authController.runWithFreshToken(() =>
          apiClient.GET("/api/v1/tenants/{tenant_id}/students/{user_id}", {
            params: { path: { tenant_id: tenantId, user_id: userId } },
          }),
        ),
      ]);

      if (!clsRes.data) {
        setError(
          normalizeApiError(
            clsRes.error,
            clsRes.response.status,
            "Unable to load your institutional workspace.",
          ),
        );
        return;
      }

      const enrolled = clsRes.data.enrolled_offerings;
      setOfferings(enrolled);
      setHomeroomSection(clsRes.data.homeroom_section ?? null);
      setHomeroomTeachers([...(clsRes.data.homeroom_teacher_names ?? [])]);
      if (profRes.data) {
        setProfile(profRes.data);
      }

      if (enrolled.length === 0) {
        setMaterials([]);
        setAnnouncements([]);
        return;
      }

      const perOffering = await Promise.all(
        enrolled.map(async (off) => {
          const [matRes, annRes] = await Promise.all([
            authController.runWithFreshToken(() =>
              apiClient.GET(
                "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/sources",
                {
                  params: {
                    path: { tenant_id: tenantId, offering_id: off.offering_id },
                    query: { limit: 100 },
                  },
                },
              ),
            ),
            authController.runWithFreshToken(() =>
              apiClient.GET(
                "/api/v1/tenants/{tenant_id}/offerings/{offering_id}/announcements",
                {
                  params: {
                    path: { tenant_id: tenantId, offering_id: off.offering_id },
                    query: { limit: 50 },
                  },
                },
              ),
            ),
          ]);
          const mats: ClassroomMaterialItem[] = (matRes.data?.items ?? [])
            .filter(
              (m) =>
                m.origin_kind === "teacher_resource" &&
                m.visibility === "offering_published" &&
                m.lifecycle_state === "active",
            )
            .map((m) => ({
              ...m,
              classroomName: off.offering_name,
              sectionName: off.section_name,
            }));
          const anns: ClassroomAnnouncementItem[] = (
            annRes.data?.items ?? []
          ).map((a) => ({
            ...a,
            classroomName: off.offering_name,
            sectionName: off.section_name,
          }));
          return { mats, anns };
        }),
      );

      const allMats = perOffering
        .flatMap((x) => x.mats)
        .sort((a, b) =>
          (b.updated_at ?? b.created_at ?? "").localeCompare(
            a.updated_at ?? a.created_at ?? "",
          ),
        );
      const allAnns = perOffering
        .flatMap((x) => x.anns)
        .sort((a, b) => {
          if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
          return b.created_at.localeCompare(a.created_at);
        });
      setMaterials(allMats);
      setAnnouncements(allAnns);
    } finally {
      setLoading(false);
    }
  }, [apiClient, authController, tenantId, userId]);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  const handleDownload = async (material: ClassroomMaterialItem) => {
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
    const url = URL.createObjectURL(res.data as Blob);
    window.open(url, "_blank", "noopener,noreferrer");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };

  const handleLearnWithViora = async (material: ClassroomMaterialItem) => {
    if (!tenantId || !userId) return;
    setImportingId(material.source_id);
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
          classroomTitle: material.classroomName,
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
      setImportingId(null);
    }
  };

  const openSubjectClassroom = (offeringId: string) => {
    if (onOpenClassroom) {
      onOpenClassroom(offeringId);
    } else {
      onNavigate("student-classrooms");
    }
  };

  if (!selected || !tenantId) return null;

  const filteredMaterials =
    subjectFilter === "all"
      ? materials
      : materials.filter((m) => m.offering_id === subjectFilter);

  const sectionLabel =
    profile?.section_name ?? homeroomSection?.section_name ?? "Unassigned";
  const semesterLabel =
    [
      profile?.academic_year_name ?? homeroomSection?.academic_year_name,
      profile?.semester_name ?? homeroomSection?.semester_name,
    ]
      .filter(Boolean)
      .join(" · ") || "—";
  const homeroomTeacherLabel =
    homeroomTeachers.length > 0 ? homeroomTeachers.join(", ") : "Unassigned";

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#f4f5f7",
        color: "#0f172a",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <header
        style={{
          background: "#ffffff",
          borderBottom: "1px solid #e2e8f0",
          padding: "0.9rem 1.75rem",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "1rem",
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <div
            style={{
              width: "36px",
              height: "36px",
              borderRadius: "10px",
              background: "#eef2ff",
              color: "#1e3a8a",
              display: "grid",
              placeItems: "center",
            }}
          >
            <InstitutionIcon />
          </div>
          <div>
            <div style={{ fontWeight: 700, fontSize: "1.02rem", color: "#0f172a" }}>
              {selected.name}
            </div>
            <div style={{ fontSize: "0.8rem", color: "#64748b" }}>
              {profile?.display_name ?? auth.user?.display_name ?? "Student"}
              {profile?.roll_number ? ` · Roll ${profile.roll_number}` : ""}
              {sectionLabel !== "Unassigned" ? ` · ${sectionLabel}` : ""}
            </div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" }}>
          {tenant.institutions.length > 1 && (
            <select
              className="viora-select viora-select-inline"
              aria-label="Switch institution"
              value={tenantId}
              onChange={(e) => tenantController.selectInstitution(e.target.value)}
            >
              {tenant.institutions.map((inst) => (
                <option key={inst.tenant_id} value={inst.tenant_id}>
                  {inst.name}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            className="viora-btn viora-btn-primary"
            onClick={() => onNavigate("student-learning")}
          >
            <StudioSparkIcon style={{ width: "16px", height: "16px", marginRight: "0.35rem" }} />
            Personal Learning
          </button>
          <button
            type="button"
            className="viora-btn"
            onClick={() => onNavigate("account-password")}
          >
            Account
          </button>
          {onLogout && (
            <button
              type="button"
              className="viora-btn"
              onClick={onLogout}
              disabled={authBusy}
            >
              Sign out
            </button>
          )}
        </div>
      </header>

      <main
        style={{
          width: "100%",
          maxWidth: "1180px",
          margin: "0 auto",
          padding: "1.5rem 1.25rem 3rem",
        }}
        className="viora-stack"
      >
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          {(
            [
              { id: "overview", label: "Overview" },
              { id: "subjects", label: `My Subjects (${offerings.length})` },
              { id: "materials", label: `Study Materials (${materials.length})` },
              { id: "announcements", label: `Announcements (${announcements.length})` },
              { id: "profile", label: "My Profile" },
            ] as const
          ).map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setTab(item.id)}
              style={{
                padding: "0.5rem 1rem",
                borderRadius: "999px",
                border: tab === item.id ? "1px solid #1e3a8a" : "1px solid #cbd5e1",
                background: tab === item.id ? "#1e3a8a" : "#ffffff",
                color: tab === item.id ? "#ffffff" : "#334155",
                fontWeight: 600,
                fontSize: "0.88rem",
                cursor: "pointer",
              }}
            >
              {item.label}
            </button>
          ))}
        </div>

        {feedback.alerts}

        {loading && <LoadingSpinner message="Loading your institutional dashboard..." />}
        {!loading && error && (
          <ErrorState
            title="Could not load student dashboard"
            message={error.message}
            onRetry={() => void loadDashboard()}
          />
        )}

        {!loading && !error && tab === "overview" && (
          <div className="viora-stack">
            <section
              style={{
                background: "#ffffff",
                borderRadius: "16px",
                border: "1px solid #e2e8f0",
                padding: "1.35rem",
              }}
            >
              <div className="viora-grid viora-grid-3">
                <div>
                  <div style={{ fontSize: "0.78rem", color: "#64748b" }}>Roll Number</div>
                  <strong style={{ fontSize: "1.05rem" }}>
                    {profile?.roll_number ?? "Not assigned"}
                  </strong>
                </div>
                <div>
                  <div style={{ fontSize: "0.78rem", color: "#64748b" }}>
                    Department & Section
                  </div>
                  <strong style={{ fontSize: "1.05rem" }}>
                    {[profile?.department_name ?? homeroomSection?.department_name, sectionLabel]
                      .filter(Boolean)
                      .join(" · ")}
                  </strong>
                </div>
                <div>
                  <div style={{ fontSize: "0.78rem", color: "#64748b" }}>
                    Academic Term
                  </div>
                  <strong style={{ fontSize: "1.05rem" }}>{semesterLabel}</strong>
                </div>
                <div>
                  <div style={{ fontSize: "0.78rem", color: "#64748b" }}>
                    Assigned Homeroom Teacher
                  </div>
                  <strong style={{ fontSize: "1.05rem" }}>
                    {homeroomTeacherLabel}
                  </strong>
                </div>
                <div>
                  <div style={{ fontSize: "0.78rem", color: "#64748b" }}>
                    Enrolled Subjects
                  </div>
                  <strong style={{ fontSize: "1.05rem" }}>{offerings.length}</strong>
                </div>
                <div>
                  <div style={{ fontSize: "0.78rem", color: "#64748b" }}>
                    Published Study Materials
                  </div>
                  <strong style={{ fontSize: "1.05rem" }}>{materials.length}</strong>
                </div>
              </div>
            </section>

            <section
              style={{
                background: "#ffffff",
                borderRadius: "16px",
                border: "1px solid #e2e8f0",
                padding: "1.35rem",
              }}
              className="viora-stack"
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <h2 style={{ margin: 0, fontSize: "1.05rem" }}>Enrolled Subjects</h2>
                <button
                  type="button"
                  className="viora-btn viora-btn-sm"
                  onClick={() => setTab("subjects")}
                >
                  View All
                </button>
              </div>
              {offerings.length === 0 ? (
                <p className="viora-muted" style={{ margin: 0 }}>
                  You are not enrolled in any subject offerings yet.
                </p>
              ) : (
                <div className="viora-grid viora-grid-3">
                  {offerings.map((off) => (
                    <div
                      key={off.offering_id}
                      style={{
                        border: "1px solid #e2e8f0",
                        borderRadius: "12px",
                        padding: "1rem",
                        background: "#f8fafc",
                        display: "flex",
                        flexDirection: "column",
                        justifyContent: "space-between",
                        gap: "0.65rem",
                      }}
                    >
                      <div>
                        <strong style={{ color: "#1e3a8a" }}>{off.subject_name}</strong>
                        <div style={{ fontSize: "0.82rem", color: "#475569", marginTop: "0.2rem" }}>
                          {off.section_name} · {off.semester_name}
                        </div>
                        <div style={{ fontSize: "0.8rem", color: "#64748b", marginTop: "0.25rem" }}>
                          Faculty:{" "}
                          {off.teacher_names && off.teacher_names.length > 0
                            ? off.teacher_names.join(", ")
                            : "Unassigned"}
                        </div>
                      </div>
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm viora-btn-primary"
                        onClick={() => openSubjectClassroom(off.offering_id)}
                      >
                        Open Classroom
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <div className="viora-grid viora-grid-2">
              <section
                style={{
                  background: "#ffffff",
                  borderRadius: "16px",
                  border: "1px solid #e2e8f0",
                  padding: "1.35rem",
                }}
                className="viora-stack"
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                >
                  <h2 style={{ margin: 0, fontSize: "1.05rem" }}>Recent Announcements</h2>
                  <button
                    type="button"
                    className="viora-btn viora-btn-sm"
                    onClick={() => setTab("announcements")}
                  >
                    All Announcements
                  </button>
                </div>
                {announcements.length === 0 ? (
                  <p className="viora-muted" style={{ margin: 0 }}>
                    No recent announcements.
                  </p>
                ) : (
                  announcements.slice(0, 4).map((a) => (
                    <div
                      key={a.id}
                      style={{
                        borderBottom: "1px solid #f1f5f9",
                        paddingBottom: "0.65rem",
                      }}
                    >
                      <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
                        <strong>{a.title}</strong>
                        {a.pinned && <StatusBadge tone="brand">Pinned</StatusBadge>}
                      </div>
                      <div style={{ fontSize: "0.78rem", color: "#64748b" }}>
                        {a.classroomName} · {a.author_name}
                      </div>
                      <div style={{ fontSize: "0.86rem", color: "#334155", marginTop: "0.25rem" }}>
                        {a.body}
                      </div>
                    </div>
                  ))
                )}
              </section>

              <section
                style={{
                  background: "#ffffff",
                  borderRadius: "16px",
                  border: "1px solid #e2e8f0",
                  padding: "1.35rem",
                }}
                className="viora-stack"
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                >
                  <h2 style={{ margin: 0, fontSize: "1.05rem" }}>
                    Recent Published Materials
                  </h2>
                  <button
                    type="button"
                    className="viora-btn viora-btn-sm"
                    onClick={() => setTab("materials")}
                  >
                    All Materials
                  </button>
                </div>
                {materials.length === 0 ? (
                  <p className="viora-muted" style={{ margin: 0 }}>
                    No published study materials yet.
                  </p>
                ) : (
                  materials.slice(0, 4).map((m) => (
                    <div
                      key={m.source_id}
                      style={{
                        borderBottom: "1px solid #f1f5f9",
                        paddingBottom: "0.65rem",
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        gap: "0.75rem",
                      }}
                    >
                      <div>
                        <strong>{m.title}</strong>
                        <div style={{ fontSize: "0.78rem", color: "#64748b" }}>
                          {[m.classroomName, m.folder_label].filter(Boolean).join(" · ")}
                        </div>
                      </div>
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm viora-btn-primary"
                        disabled={importingId === m.source_id}
                        onClick={() => void handleLearnWithViora(m)}
                      >
                        {importingId === m.source_id
                          ? "Opening..."
                          : "Learn with VIORA AI"}
                      </button>
                    </div>
                  ))
                )}
              </section>
            </div>
          </div>
        )}

        {!loading && !error && tab === "subjects" && (
          <section
            style={{
              background: "#ffffff",
              borderRadius: "16px",
              border: "1px solid #e2e8f0",
              padding: "1.35rem",
            }}
            className="viora-stack"
          >
            <h2 style={{ margin: 0, fontSize: "1.1rem" }}>My Enrolled Subjects</h2>
            {offerings.length === 0 ? (
              <p className="viora-muted" style={{ margin: 0 }}>
                No active subject enrolments found.
              </p>
            ) : (
              <div className="viora-grid viora-grid-3">
                {offerings.map((off) => (
                  <div
                    key={off.offering_id}
                    style={{
                      border: "1px solid #e2e8f0",
                      borderRadius: "12px",
                      padding: "1.1rem",
                      background: "#f8fafc",
                      display: "flex",
                      flexDirection: "column",
                      justifyContent: "space-between",
                      gap: "0.75rem",
                    }}
                  >
                    <div className="viora-stack" style={{ gap: "0.3rem" }}>
                      <strong style={{ color: "#1e3a8a", fontSize: "1rem" }}>
                        {off.offering_name}
                      </strong>
                      <div style={{ fontSize: "0.84rem", color: "#475569" }}>
                        {off.subject_name} · {off.section_name}
                      </div>
                      <div style={{ fontSize: "0.8rem", color: "#64748b" }}>
                        {off.academic_year_name} · {off.semester_name}
                      </div>
                      <div style={{ fontSize: "0.82rem", color: "#334155" }}>
                        <strong>Faculty:</strong>{" "}
                        {off.teacher_names && off.teacher_names.length > 0
                          ? off.teacher_names.join(", ")
                          : "Unassigned"}
                      </div>
                    </div>
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm viora-btn-primary"
                      onClick={() => openSubjectClassroom(off.offering_id)}
                    >
                      <LibraryIcon style={{ width: "14px", height: "14px", marginRight: "0.35rem" }} />
                      Open Classroom
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {!loading && !error && tab === "materials" && (
          <section
            style={{
              background: "#ffffff",
              borderRadius: "16px",
              border: "1px solid #e2e8f0",
              padding: "1.35rem",
            }}
            className="viora-stack"
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                flexWrap: "wrap",
                gap: "0.75rem",
              }}
            >
              <h2 style={{ margin: 0, fontSize: "1.1rem" }}>
                Published Study Materials ({filteredMaterials.length})
              </h2>
              <select
                className="viora-select viora-select-inline"
                aria-label="Filter materials by subject"
                value={subjectFilter}
                onChange={(e) => setSubjectFilter(e.target.value)}
              >
                <option value="all">All subjects</option>
                {offerings.map((o) => (
                  <option key={o.offering_id} value={o.offering_id}>
                    {o.offering_name}
                  </option>
                ))}
              </select>
            </div>
            {filteredMaterials.length === 0 ? (
              <p className="viora-muted" style={{ margin: 0 }}>
                No published study materials match this filter.
              </p>
            ) : (
              <div className="viora-stack">
                {filteredMaterials.map((m) => (
                  <div
                    key={m.source_id}
                    style={{
                      border: "1px solid #e2e8f0",
                      borderRadius: "12px",
                      padding: "0.95rem 1.1rem",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      flexWrap: "wrap",
                      gap: "0.75rem",
                    }}
                  >
                    <div>
                      <strong>{m.title}</strong>
                      <div style={{ fontSize: "0.82rem", color: "#64748b" }}>
                        {[m.classroomName, m.folder_label, m.latest_input_format]
                          .filter(Boolean)
                          .join(" · ")}
                      </div>
                    </div>
                    <div className="viora-row">
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm"
                        onClick={() => void handleDownload(m)}
                      >
                        View / Download
                      </button>
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm viora-btn-primary"
                        disabled={importingId === m.source_id}
                        onClick={() => void handleLearnWithViora(m)}
                      >
                        {importingId === m.source_id
                          ? "Opening..."
                          : "Learn with VIORA AI"}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {!loading && !error && tab === "announcements" && (
          <section
            style={{
              background: "#ffffff",
              borderRadius: "16px",
              border: "1px solid #e2e8f0",
              padding: "1.35rem",
            }}
            className="viora-stack"
          >
            <h2 style={{ margin: 0, fontSize: "1.1rem" }}>
              Classroom Announcements ({announcements.length})
            </h2>
            {announcements.length === 0 ? (
              <p className="viora-muted" style={{ margin: 0 }}>
                No announcements have been posted in your classrooms yet.
              </p>
            ) : (
              <div className="viora-stack">
                {announcements.map((a) => (
                  <div
                    key={a.id}
                    style={{
                      border: "1px solid #e2e8f0",
                      borderRadius: "12px",
                      padding: "1rem",
                      background: "#f8fafc",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        flexWrap: "wrap",
                        gap: "0.5rem",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                        <strong>{a.title}</strong>
                        {a.pinned && <StatusBadge tone="brand">Pinned</StatusBadge>}
                      </div>
                      <span style={{ fontSize: "0.78rem", color: "#64748b" }}>
                        {a.classroomName} · {a.author_name} ·{" "}
                        {new Date(a.created_at).toLocaleString()}
                      </span>
                    </div>
                    <p style={{ margin: "0.55rem 0 0", whiteSpace: "pre-wrap", color: "#334155" }}>
                      {a.body}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {!loading && !error && tab === "profile" && (
          <section
            style={{
              background: "#ffffff",
              borderRadius: "16px",
              border: "1px solid #e2e8f0",
              padding: "1.35rem",
            }}
            className="viora-stack"
          >
            <h2 style={{ margin: 0, fontSize: "1.1rem" }}>My Academic Profile</h2>
            <div className="viora-grid viora-grid-2">
              <div>
                <div style={{ fontSize: "0.78rem", color: "#64748b" }}>Full Name</div>
                <strong>
                  {profile?.display_name ?? auth.user?.display_name ?? "—"}
                </strong>
              </div>
              <div>
                <div style={{ fontSize: "0.78rem", color: "#64748b" }}>Login Email</div>
                <strong>{profile?.email ?? "—"}</strong>
              </div>
              <div>
                <div style={{ fontSize: "0.78rem", color: "#64748b" }}>Roll Number</div>
                <strong>{profile?.roll_number ?? "—"}</strong>
              </div>
              <div>
                <div style={{ fontSize: "0.78rem", color: "#64748b" }}>Department</div>
                <strong>
                  {profile?.department_name ?? homeroomSection?.department_name ?? "—"}
                </strong>
              </div>
              <div>
                <div style={{ fontSize: "0.78rem", color: "#64748b" }}>
                  Academic Year & Semester
                </div>
                <strong>{semesterLabel}</strong>
              </div>
              <div>
                <div style={{ fontSize: "0.78rem", color: "#64748b" }}>Section</div>
                <strong>{sectionLabel}</strong>
              </div>
              <div>
                <div style={{ fontSize: "0.78rem", color: "#64748b" }}>
                  Homeroom Teacher
                </div>
                <strong>{homeroomTeacherLabel}</strong>
              </div>
              <div>
                <div style={{ fontSize: "0.78rem", color: "#64748b" }}>Phone</div>
                <strong>{profile?.phone ?? "—"}</strong>
              </div>
              <div>
                <div style={{ fontSize: "0.78rem", color: "#64748b" }}>
                  Contact Email
                </div>
                <strong>{profile?.contact_email ?? "—"}</strong>
              </div>
              <div>
                <div style={{ fontSize: "0.78rem", color: "#64748b" }}>
                  Guardian Contact
                </div>
                <strong>{profile?.guardian_contact ?? "—"}</strong>
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
