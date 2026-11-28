import type { AuthStatus } from "./session";

export type AppRouteId =
  | "root"
  | "login"
  | "institution"
  | "student-learning"
  | "student-classrooms"
  | "teacher-class"
  | "teacher-subjects"
  | "teacher-department"
  | "head-students"
  | "head-faculty"
  | "head-academics"
  | "head-permissions"
  | "guardians"
  | "account-password"
  | "health";

export interface RouteAccessInput {
  authStatus: AuthStatus;
  requestedRoute: AppRouteId;
  hasSelectedInstitution: boolean;
  tenantStatus?: "idle" | "loading" | "ready" | "error";
  defaultRoute?: AppRouteId;
}

export interface RouteDecision {
  allowed: boolean;
  effectiveRoute: AppRouteId;
  reason: "ok" | "initializing" | "require_auth" | "require_institution";
}

export const PUBLIC_ROUTES = new Set<AppRouteId>(["login", "health"]);

export const INSTITUTION_SCOPED_ROUTES = new Set<AppRouteId>([
  "institution",
  "student-learning",
  "student-classrooms",
  "teacher-class",
  "teacher-subjects",
  "teacher-department",
  "head-students",
  "head-faculty",
  "head-academics",
  "head-permissions",
  "guardians",
]);

export function evaluateRouteAccess(input: RouteAccessInput): RouteDecision {
  const {
    authStatus,
    requestedRoute,
    hasSelectedInstitution,
    tenantStatus = "ready",
    defaultRoute = "institution",
  } = input;

  if (requestedRoute === "health") {
    return { allowed: true, effectiveRoute: "health", reason: "ok" };
  }

  if (authStatus === "initializing") {
    return {
      allowed: false,
      effectiveRoute: requestedRoute,
      reason: "initializing",
    };
  }

  if (authStatus === "unauthenticated") {
    return {
      allowed: requestedRoute === "login",
      effectiveRoute: "login",
      reason: requestedRoute === "login" ? "ok" : "require_auth",
    };
  }

  // Authenticated user: wait for initial institution discovery before resolving landing/scoped routes.
  if (
    !hasSelectedInstitution &&
    (tenantStatus === "idle" || tenantStatus === "loading") &&
    (requestedRoute === "login" ||
      requestedRoute === "root" ||
      INSTITUTION_SCOPED_ROUTES.has(requestedRoute))
  ) {
    return {
      allowed: false,
      effectiveRoute: requestedRoute,
      reason: "initializing",
    };
  }

  if (requestedRoute === "login" || requestedRoute === "root") {
    return {
      allowed: true,
      effectiveRoute: hasSelectedInstitution ? defaultRoute : "institution",
      reason: "ok",
    };
  }

  if (
    INSTITUTION_SCOPED_ROUTES.has(requestedRoute) &&
    !hasSelectedInstitution
  ) {
    return {
      allowed: true,
      effectiveRoute: "institution",
      reason: "require_institution",
    };
  }

  return {
    allowed: true,
    effectiveRoute: requestedRoute,
    reason: "ok",
  };
}

const PATH_TO_ROUTE: Record<string, AppRouteId> = {
  "/": "root",
  "/login": "login",
  "/institution": "institution",
  "/learning": "student-learning",
  "/learning/classrooms": "student-classrooms",
  "/teaching/class": "teacher-class",
  "/teaching/subjects": "teacher-subjects",
  "/teaching/department": "teacher-department",
  "/college/students": "head-students",
  "/college/faculty": "head-faculty",
  "/college/academics": "head-academics",
  "/college/permissions": "head-permissions",
  "/academics/campuses": "head-academics",
  "/academics/departments": "head-academics",
  "/academics/programmes": "head-academics",
  "/academics/calendar": "head-academics",
  "/academics/sections": "head-academics",
  "/academics/subjects": "head-academics",
  "/academics/offerings": "head-academics",
  "/sources": "teacher-subjects",
  "/memberships": "head-permissions",
  "/guardians": "guardians",
  "/account/password": "account-password",
  "/health": "health",
};

const ROUTE_TO_PATH: Record<AppRouteId, string> = {
  root: "/",
  login: "/login",
  institution: "/institution",
  "student-learning": "/learning",
  "student-classrooms": "/learning/classrooms",
  "teacher-class": "/teaching/class",
  "teacher-subjects": "/teaching/subjects",
  "teacher-department": "/teaching/department",
  "head-students": "/college/students",
  "head-faculty": "/college/faculty",
  "head-academics": "/college/academics",
  "head-permissions": "/college/permissions",
  guardians: "/guardians",
  "account-password": "/account/password",
  health: "/health",
};

export function routeFromPathname(pathname: string): AppRouteId {
  const normalized =
    pathname.length > 1 && pathname.endsWith("/")
      ? pathname.slice(0, -1)
      : pathname;
  return PATH_TO_ROUTE[normalized] ?? "root";
}

export function pathnameForRoute(route: AppRouteId): string {
  return ROUTE_TO_PATH[route];
}
