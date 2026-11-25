import type { AuthStatus } from "./session";

export type AppRouteId =
  | "login"
  | "institution"
  | "academics-campuses"
  | "academics-departments"
  | "academics-programmes"
  | "academics-calendar"
  | "academics-sections"
  | "academics-subjects"
  | "academics-offerings"
  | "memberships"
  | "guardians"
  | "account-password"
  | "health";

export interface RouteAccessInput {
  authStatus: AuthStatus;
  requestedRoute: AppRouteId;
  hasSelectedInstitution: boolean;
  tenantStatus?: "idle" | "loading" | "ready" | "error";
}

export interface RouteDecision {
  allowed: boolean;
  effectiveRoute: AppRouteId;
  reason: "ok" | "initializing" | "require_auth" | "require_institution";
}

export const PUBLIC_ROUTES = new Set<AppRouteId>(["login", "health"]);

export const INSTITUTION_SCOPED_ROUTES = new Set<AppRouteId>([
  "institution",
  "academics-campuses",
  "academics-departments",
  "academics-programmes",
  "academics-calendar",
  "academics-sections",
  "academics-subjects",
  "academics-offerings",
  "memberships",
  "guardians",
]);

export function evaluateRouteAccess(input: RouteAccessInput): RouteDecision {
  const {
    authStatus,
    requestedRoute,
    hasSelectedInstitution,
    tenantStatus = "ready",
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

  // Authenticated user
  if (requestedRoute === "login") {
    return {
      allowed: true,
      effectiveRoute: "institution",
      reason: "ok",
    };
  }

  if (
    INSTITUTION_SCOPED_ROUTES.has(requestedRoute) &&
    !hasSelectedInstitution &&
    (tenantStatus === "idle" || tenantStatus === "loading")
  ) {
    return {
      allowed: false,
      effectiveRoute: requestedRoute,
      reason: "initializing",
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
  "/": "institution",
  "/login": "login",
  "/institution": "institution",
  "/academics/campuses": "academics-campuses",
  "/academics/departments": "academics-departments",
  "/academics/programmes": "academics-programmes",
  "/academics/calendar": "academics-calendar",
  "/academics/sections": "academics-sections",
  "/academics/subjects": "academics-subjects",
  "/academics/offerings": "academics-offerings",
  "/memberships": "memberships",
  "/guardians": "guardians",
  "/account/password": "account-password",
  "/health": "health",
};

const ROUTE_TO_PATH: Record<AppRouteId, string> = {
  login: "/login",
  institution: "/institution",
  "academics-campuses": "/academics/campuses",
  "academics-departments": "/academics/departments",
  "academics-programmes": "/academics/programmes",
  "academics-calendar": "/academics/calendar",
  "academics-sections": "/academics/sections",
  "academics-subjects": "/academics/subjects",
  "academics-offerings": "/academics/offerings",
  memberships: "/memberships",
  guardians: "/guardians",
  "account-password": "/account/password",
  health: "/health",
};

export function routeFromPathname(pathname: string): AppRouteId {
  const normalized =
    pathname.length > 1 && pathname.endsWith("/")
      ? pathname.slice(0, -1)
      : pathname;
  return PATH_TO_ROUTE[normalized] ?? "institution";
}

export function pathnameForRoute(route: AppRouteId): string {
  return ROUTE_TO_PATH[route];
}
