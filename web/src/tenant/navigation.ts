import type { InstitutionRole } from "../api/client";
import type { AppRouteId } from "../auth/routeGuard";

export interface RoleFlags {
  isOwner: boolean;
  isAdmin: boolean;
  isDepartmentHead: boolean;
  isClassCoordinator: boolean;
  isSubjectTeacher: boolean;
  isStudent: boolean;
  isGuardian: boolean;
}

export interface NavItemDescriptor {
  id: AppRouteId | string;
  label: string;
  section: "institution" | "academics" | "people" | "account" | "future";
  route?: AppRouteId;
  disabled?: boolean;
  badge?: string;
}

export const ALL_INSTITUTION_ROLES: readonly InstitutionRole[] = [
  "institution_owner",
  "institution_admin",
  "department_head",
  "class_coordinator",
  "subject_teacher",
  "student",
  "guardian",
];

export const ROLE_LABELS: Record<InstitutionRole, string> = {
  institution_owner: "Institution Owner",
  institution_admin: "Institution Admin",
  department_head: "Department Head",
  class_coordinator: "Class Coordinator",
  subject_teacher: "Subject Teacher",
  student: "Student",
  guardian: "Guardian",
};

export function deriveRoleFlags(roles: readonly string[]): RoleFlags {
  const roleSet = new Set(roles);
  const isOwner = roleSet.has("institution_owner");
  const isAdmin = isOwner || roleSet.has("institution_admin");
  return {
    isOwner,
    isAdmin,
    isDepartmentHead: roleSet.has("department_head"),
    isClassCoordinator: roleSet.has("class_coordinator"),
    isSubjectTeacher: roleSet.has("subject_teacher"),
    isStudent: roleSet.has("student"),
    isGuardian: roleSet.has("guardian"),
  };
}

/**
 * Computes navigation items from institution discovery roles.
 * Returned roles are navigation hints only; backend policy and RLS remain authoritative.
 */
export function buildNavigationItems(
  roles: readonly string[],
  hasSelectedInstitution: boolean,
): NavItemDescriptor[] {
  const flags = deriveRoleFlags(roles);
  const items: NavItemDescriptor[] = [];

  items.push({
    id: "institution",
    label: hasSelectedInstitution ? "Institution Overview" : "Select Institution",
    section: "institution",
    route: "institution",
  });

  if (hasSelectedInstitution) {
    if (flags.isAdmin) {
      items.push(
        {
          id: "academics-campuses",
          label: "Campuses",
          section: "academics",
          route: "academics-campuses",
        },
        {
          id: "academics-departments",
          label: "Departments & Heads",
          section: "academics",
          route: "academics-departments",
        },
        {
          id: "academics-programmes",
          label: "Programmes",
          section: "academics",
          route: "academics-programmes",
        },
        {
          id: "academics-calendar",
          label: "Years & Semesters",
          section: "academics",
          route: "academics-calendar",
        },
        {
          id: "academics-sections",
          label: "Class Sections",
          section: "academics",
          route: "academics-sections",
        },
        {
          id: "academics-subjects",
          label: "Subjects",
          section: "academics",
          route: "academics-subjects",
        },
        {
          id: "academics-offerings",
          label: "Offerings & Enrolments",
          section: "academics",
          route: "academics-offerings",
        },
        {
          id: "memberships",
          label: "Members & Roles",
          section: "people",
          route: "memberships",
        },
        {
          id: "guardians",
          label: "Guardian Links",
          section: "people",
          route: "guardians",
        },
      );
    } else {
      if (flags.isDepartmentHead) {
        items.push(
          {
            id: "academics-departments",
            label: "My Departments",
            section: "academics",
            route: "academics-departments",
          },
          {
            id: "academics-programmes",
            label: "Programmes",
            section: "academics",
            route: "academics-programmes",
          },
          {
            id: "academics-subjects",
            label: "Subjects",
            section: "academics",
            route: "academics-subjects",
          },
        );
      }

      if (flags.isClassCoordinator) {
        items.push({
          id: "academics-sections",
          label: "Coordinated Sections",
          section: "academics",
          route: "academics-sections",
        });
      }

      if (flags.isClassCoordinator || flags.isSubjectTeacher || flags.isStudent) {
        items.push({
          id: "academics-offerings",
          label: flags.isStudent ? "My Enrolled Offerings" : "Subject Offerings",
          section: "academics",
          route: "academics-offerings",
        });
      }

      if (flags.isSubjectTeacher || flags.isStudent) {
        items.push({
          id: "sources",
          label:
            flags.isSubjectTeacher
              ? "Resources & Curriculum"
              : "Lessons & Notes",
          section: "academics",
          route: "sources",
        });
      }

      if (flags.isStudent || flags.isGuardian) {
        items.push({
          id: "guardians",
          label: flags.isGuardian ? "Linked Students" : "Guardian Consent",
          section: "people",
          route: "guardians",
        });
      }
    }
  }

  items.push(
    {
      id: "account-password",
      label: "Password & Security",
      section: "account",
      route: "account-password",
    },
    {
      id: "health",
      label: "System Health",
      section: "account",
      route: "health",
    },
    {
      id: "future-tutor",
      label: "AI Tutor",
      section: "future",
      disabled: true,
      badge: "Phase 2",
    },
    {
      id: "future-lessons",
      label: "Lessons",
      section: "future",
      disabled: true,
      badge: "Phase 2",
    },
    {
      id: "future-assessments",
      label: "Assessments",
      section: "future",
      disabled: true,
      badge: "Phase 2",
    },
    {
      id: "future-analytics",
      label: "Analytics",
      section: "future",
      disabled: true,
      badge: "Phase 2",
    },
  );

  return items;
}
