import { useEffect, useState } from "react";
import type {
  AcademicYearSummary,
  CampusSummary,
  DepartmentSummary,
  MemberSummary,
  ProgrammeSummary,
  SectionSummary,
  SemesterSummary,
  SubjectSummary,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { deriveRoleFlags } from "../../tenant/navigation";
import { useTenant } from "../../tenant/TenantContext";

export interface TenantOptionsFlags {
  campuses?: boolean;
  departments?: boolean;
  programmes?: boolean;
  academicYears?: boolean;
  semesters?: boolean;
  sections?: boolean;
  subjects?: boolean;
  members?: boolean;
}

export interface TenantOptionsData {
  campuses: CampusSummary[];
  departments: DepartmentSummary[];
  programmes: ProgrammeSummary[];
  academicYears: AcademicYearSummary[];
  semesters: SemesterSummary[];
  sections: SectionSummary[];
  subjects: SubjectSummary[];
  members: MemberSummary[];
  loading: boolean;
}

export function resolveLabelById<T extends { id: string; name: string }>(
  items: readonly T[],
  id: string | null | undefined,
): string | null {
  if (!id) {
    return null;
  }
  const found = items.find((item) => item.id === id);
  return found ? found.name : null;
}

export function resolveMemberLabel(
  members: readonly MemberSummary[],
  userId: string,
): string | null {
  const found = members.find((m) => m.user_id === userId);
  return found ? found.display_name : null;
}

export function useTenantOptions(flags: TenantOptionsFlags): TenantOptionsData {
  const { apiClient, controller: authController } = useAuth();
  const { snapshot: tenant } = useTenant();

  const selected = tenant.selectedInstitution;
  const tenantId = selected?.tenant_id ?? null;
  const roleFlags = deriveRoleFlags(selected?.roles ?? []);

  const [data, setData] = useState<TenantOptionsData>({
    campuses: [],
    departments: [],
    programmes: [],
    academicYears: [],
    semesters: [],
    sections: [],
    subjects: [],
    members: [],
    loading: false,
  });

  useEffect(() => {
    if (!tenantId) {
      setData({
        campuses: [],
        departments: [],
        programmes: [],
        academicYears: [],
        semesters: [],
        sections: [],
        subjects: [],
        members: [],
        loading: false,
      });
      return;
    }

    let cancelled = false;
    setData((prev) => ({ ...prev, loading: true }));

    const loadAll = async () => {
      const next: TenantOptionsData = {
        campuses: [],
        departments: [],
        programmes: [],
        academicYears: [],
        semesters: [],
        sections: [],
        subjects: [],
        members: [],
        loading: false,
      };

      const tasks: Promise<void>[] = [];

      if (flags.campuses && roleFlags.isAdmin) {
        tasks.push(
          authController
            .runWithFreshToken(() =>
              apiClient.GET("/api/v1/tenants/{tenant_id}/campuses", {
                params: { path: { tenant_id: tenantId }, query: { limit: 100 } },
              }),
            )
            .then((res) => {
              if (res.data) next.campuses = res.data.items;
            }),
        );
      }

      if (flags.departments && (roleFlags.isAdmin || roleFlags.isDepartmentHead)) {
        tasks.push(
          authController
            .runWithFreshToken(() =>
              apiClient.GET("/api/v1/tenants/{tenant_id}/departments", {
                params: { path: { tenant_id: tenantId }, query: { limit: 100 } },
              }),
            )
            .then((res) => {
              if (res.data) next.departments = res.data.items;
            }),
        );
      }

      if (flags.programmes && (roleFlags.isAdmin || roleFlags.isDepartmentHead)) {
        tasks.push(
          authController
            .runWithFreshToken(() =>
              apiClient.GET("/api/v1/tenants/{tenant_id}/programmes", {
                params: { path: { tenant_id: tenantId }, query: { limit: 100 } },
              }),
            )
            .then((res) => {
              if (res.data) next.programmes = res.data.items;
            }),
        );
      }

      if (flags.academicYears && roleFlags.isAdmin) {
        tasks.push(
          authController
            .runWithFreshToken(() =>
              apiClient.GET("/api/v1/tenants/{tenant_id}/academic-years", {
                params: { path: { tenant_id: tenantId }, query: { limit: 100 } },
              }),
            )
            .then((res) => {
              if (res.data) next.academicYears = res.data.items;
            }),
        );
      }

      if (flags.semesters && roleFlags.isAdmin) {
        tasks.push(
          authController
            .runWithFreshToken(() =>
              apiClient.GET("/api/v1/tenants/{tenant_id}/semesters", {
                params: { path: { tenant_id: tenantId }, query: { limit: 100 } },
              }),
            )
            .then((res) => {
              if (res.data) next.semesters = res.data.items;
            }),
        );
      }

      if (flags.sections && (roleFlags.isAdmin || roleFlags.isClassCoordinator)) {
        tasks.push(
          authController
            .runWithFreshToken(() =>
              apiClient.GET("/api/v1/tenants/{tenant_id}/sections", {
                params: { path: { tenant_id: tenantId }, query: { limit: 100 } },
              }),
            )
            .then((res) => {
              if (res.data) next.sections = res.data.items;
            }),
        );
      }

      if (flags.subjects && (roleFlags.isAdmin || roleFlags.isDepartmentHead)) {
        tasks.push(
          authController
            .runWithFreshToken(() =>
              apiClient.GET("/api/v1/tenants/{tenant_id}/subjects", {
                params: { path: { tenant_id: tenantId }, query: { limit: 100 } },
              }),
            )
            .then((res) => {
              if (res.data) next.subjects = res.data.items;
            }),
        );
      }

      if (flags.members && roleFlags.isAdmin) {
        tasks.push(
          authController
            .runWithFreshToken(() =>
              apiClient.GET("/api/v1/tenants/{tenant_id}/memberships", {
                params: { path: { tenant_id: tenantId }, query: { limit: 100 } },
              }),
            )
            .then((res) => {
              if (res.data) next.members = res.data.items;
            }),
        );
      }

      await Promise.allSettled(tasks);
      if (!cancelled) {
        setData(next);
      }
    };

    void loadAll();
    return () => {
      cancelled = true;
    };
  }, [
    apiClient,
    authController,
    flags.academicYears,
    flags.campuses,
    flags.departments,
    flags.members,
    flags.programmes,
    flags.sections,
    flags.semesters,
    flags.subjects,
    roleFlags.isAdmin,
    roleFlags.isClassCoordinator,
    roleFlags.isDepartmentHead,
    tenantId,
  ]);

  return data;
}
