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
  id: AppRouteId;
  label: string;
  section: "learning" | "teaching" | "college" | "institution" | "account";
  route: AppRouteId;
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

export const FACULTY_ROLES: readonly InstitutionRole[] = [
  "department_head",
  "class_coordinator",
  "subject_teacher",
];

export const ROLE_LABELS: Record<InstitutionRole, string> = {
  institution_owner: "College Head (Owner)",
  institution_admin: "College Head (Admin)",
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

export interface AssignedResponsibilitiesFlags {
  hasHomeroom: boolean;
  hasTeachingSubjects: boolean;
}

/**
 * Resolves the default landing workspace for the user's active institution roles.
 */
export function defaultRouteForRoles(
  roles: readonly string[],
  hasSelectedInstitution: boolean,
  responsibilities?: AssignedResponsibilitiesFlags,
): AppRouteId {
  if (!hasSelectedInstitution) {
    return "institution";
  }
  const flags = deriveRoleFlags(roles);
  const showTeaching =
    flags.isSubjectTeacher && (responsibilities ? responsibilities.hasTeachingSubjects : true);
  const showHomeroom =
    flags.isClassCoordinator && (responsibilities ? responsibilities.hasHomeroom : true);

  if (flags.isAdmin) {
    return "head-students";
  }
  if (showTeaching) {
    return "teacher-subjects";
  }
  if (showHomeroom) {
    return "teacher-class";
  }
  if (flags.isDepartmentHead) {
    return "teacher-department";
  }
  if (flags.isStudent) {
    return "student-learning";
  }
  if (flags.isGuardian) {
    return "guardians";
  }
  return "institution";
}

/**
 * Computes role-specific navigation items from institution discovery roles.
 * Returned roles are navigation hints only; backend policy and RLS remain authoritative.
 */
export function buildNavigationItems(
  roles: readonly string[],
  hasSelectedInstitution: boolean,
  responsibilities?: AssignedResponsibilitiesFlags,
): NavItemDescriptor[] {
  const flags = deriveRoleFlags(roles);
  const items: NavItemDescriptor[] = [];

  if (!hasSelectedInstitution) {
    items.push({
      id: "institution",
      label: "Select Institution",
      section: "institution",
      route: "institution",
    });
    items.push({
      id: "account-password",
      label: "Account & Security",
      section: "account",
      route: "account-password",
    });
    return items;
  }

  if (flags.isAdmin) {
    items.push(
      {
        id: "head-students",
        label: "Students",
        section: "college",
        route: "head-students",
      },
      {
        id: "head-faculty",
        label: "Faculty",
        section: "college",
        route: "head-faculty",
      },
      {
        id: "head-academics",
        label: "Academic Structure",
        section: "college",
        route: "head-academics",
      },
      {
        id: "head-permissions",
        label: "Permissions",
        section: "college",
        route: "head-permissions",
      },
    );
  }

  const showHomeroom =
    flags.isClassCoordinator && (responsibilities ? responsibilities.hasHomeroom : true);
  const showTeaching =
    flags.isSubjectTeacher && (responsibilities ? responsibilities.hasTeachingSubjects : true);

  if (showHomeroom) {
    items.push({
      id: "teacher-class",
      label: "My Class",
      section: "teaching",
      route: "teacher-class",
    });
  }

  if (showTeaching) {
    items.push({
      id: "teacher-subjects",
      label: "My Teaching Subjects",
      section: "teaching",
      route: "teacher-subjects",
    });
  }

  if (!flags.isAdmin && flags.isDepartmentHead) {
    items.push({
      id: "teacher-department",
      label: "My Department",
      section: "teaching",
      route: "teacher-department",
    });
  }

  if (flags.isStudent) {
    items.push({
      id: "student-learning",
      label: "Personal Learning",
      section: "learning",
      route: "student-learning",
    });
    items.push({
      id: "student-classrooms",
      label: "My Classrooms",
      section: "learning",
      route: "student-classrooms",
    });
    items.push({
      id: "institution",
      label: "My Institution",
      section: "institution",
      route: "institution",
    });
  }

  if (
    flags.isGuardian &&
    !flags.isAdmin &&
    !flags.isStudent &&
    !flags.isSubjectTeacher &&
    !flags.isClassCoordinator &&
    !flags.isDepartmentHead
  ) {
    items.push({
      id: "guardians",
      label: "Linked Students",
      section: "institution",
      route: "guardians",
    });
  }

  if (
    !flags.isAdmin &&
    !flags.isStudent &&
    !flags.isSubjectTeacher &&
    !flags.isClassCoordinator &&
    !flags.isDepartmentHead &&
    !flags.isGuardian
  ) {
    items.push({
      id: "institution",
      label: "My Institution",
      section: "institution",
      route: "institution",
    });
  }

  items.push({
    id: "account-password",
    label: "Account & Security",
    section: "account",
    route: "account-password",
  });

  return items;
}
