import { useMemo } from "react";
import type { SubjectSummary } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { DepartmentScopedCrudPage } from "./DepartmentScopedCrudPage";

export function SubjectsPage() {
  const { apiClient } = useAuth();
  const config = useMemo(
    () => ({
      pageTitle: "Subjects",
      pageSubtitle: "Manage catalog subjects before creating section offerings and teaching assignments.",
      entityLabel: "Subject",
      entityLower: "subject",
      directoryTitle: "Subjects",
      namePlaceholder: "Data Structures & Algorithms",
      filterAriaLabel: "Filter subjects by department",
      emptyTitle: "No subjects configured",
      idColumnHeader: "Subject ID",
      idPrefix: "sub",
      archiveMessage: (s: SubjectSummary) =>
        `Change lifecycle status for "${s.name}"? Archiving prevents creating new subject offerings for this subject.`,
      listCall: (
        tenantId: string,
        query: { department_id?: string; active?: boolean; cursor?: string; limit: number },
      ) =>
        apiClient.GET("/api/v1/tenants/{tenant_id}/subjects", {
          params: { path: { tenant_id: tenantId }, query },
        }),
      createCall: (tenantId: string, body: { name: string; department_id: string | null }) =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/subjects", {
          params: { path: { tenant_id: tenantId } },
          body,
        }),
      updateCall: (
        tenantId: string,
        subjectId: string,
        body: { name: string; department_id: string | null },
      ) =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/subjects/{subject_id}", {
          params: { path: { tenant_id: tenantId, subject_id: subjectId } },
          body,
        }),
      lifecycleCall: (tenantId: string, subjectId: string, body: { active: boolean }) =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/subjects/{subject_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, subject_id: subjectId } },
          body,
        }),
    }),
    [apiClient],
  );
  return <DepartmentScopedCrudPage<SubjectSummary> config={config} />;
}
