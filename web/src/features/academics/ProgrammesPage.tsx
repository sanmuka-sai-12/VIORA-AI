import { useMemo } from "react";
import type { ProgrammeSummary } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { DepartmentScopedCrudPage } from "./DepartmentScopedCrudPage";

export function ProgrammesPage({ embedded = false }: { embedded?: boolean }) {
  const { apiClient } = useAuth();
  const config = useMemo(
    () => ({
      pageTitle: "Programmes",
      pageSubtitle: "Manage degree or curriculum programmes optionally scoped to a department.",
      entityLabel: "Programme",
      entityLower: "programme",
      directoryTitle: "Programme List",
      namePlaceholder: "B.Tech Computer Science",
      filterAriaLabel: "Filter by department",
      emptyTitle: "No programmes found",
      idColumnHeader: "Programme ID",
      idPrefix: "prog",
      archiveMessage: (p: ProgrammeSummary) =>
        `Change lifecycle status for "${p.name}"? Archiving prevents linking new sections to it.`,
      listCall: (
        tenantId: string,
        query: { department_id?: string; active?: boolean; cursor?: string; limit: number },
      ) =>
        apiClient.GET("/api/v1/tenants/{tenant_id}/programmes", {
          params: { path: { tenant_id: tenantId }, query },
        }),
      createCall: (tenantId: string, body: { name: string; department_id: string | null }) =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/programmes", {
          params: { path: { tenant_id: tenantId } },
          body,
        }),
      updateCall: (
        tenantId: string,
        programmeId: string,
        body: { name: string; department_id: string | null },
      ) =>
        apiClient.PATCH("/api/v1/tenants/{tenant_id}/programmes/{programme_id}", {
          params: { path: { tenant_id: tenantId, programme_id: programmeId } },
          body,
        }),
      lifecycleCall: (tenantId: string, programmeId: string, body: { active: boolean }) =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/programmes/{programme_id}/lifecycle", {
          params: { path: { tenant_id: tenantId, programme_id: programmeId } },
          body,
        }),
    }),
    [apiClient],
  );
  return <DepartmentScopedCrudPage<ProgrammeSummary> config={config} embedded={embedded} />;
}
