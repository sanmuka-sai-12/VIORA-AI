import { useCallback, useEffect, useState } from "react";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import {
  evaluateRouteAccess,
  pathnameForRoute,
  routeFromPathname,
  type AppRouteId,
} from "./auth/routeGuard";
import { ErrorBoundary } from "./components/common/ErrorBoundary";
import { LoadingSpinner } from "./components/common/FeedbackStates";
import { AlertBanner } from "./components/common/UiPrimitives";
import { AppShell } from "./components/layout/AppShell";
import { AcademicYearsSemestersPage } from "./features/academics/AcademicYearsSemestersPage";
import { CampusesPage } from "./features/academics/CampusesPage";
import { DepartmentsPage } from "./features/academics/DepartmentsPage";
import { OfferingsPage } from "./features/academics/OfferingsPage";
import { ProgrammesPage } from "./features/academics/ProgrammesPage";
import { SectionsPage } from "./features/academics/SectionsPage";
import { SubjectsPage } from "./features/academics/SubjectsPage";
import { LoginPage } from "./features/auth/LoginPage";
import { PasswordChangePage } from "./features/auth/PasswordChangePage";
import { GuardiansPage } from "./features/guardians/GuardiansPage";
import { SystemHealthDashboard } from "./features/health/SystemHealthDashboard";
import { InstitutionOverviewPage } from "./features/institution/InstitutionOverviewPage";
import { MembershipsPage } from "./features/memberships/MembershipsPage";
import { TenantProvider, useTenant } from "./tenant/TenantContext";

function PortalRouter() {
  const { snapshot: auth, controller: authController } = useAuth();
  const { snapshot: tenant, controller: tenantController } = useTenant();

  const [requestedRoute, setRequestedRoute] = useState<AppRouteId>(() =>
    routeFromPathname(window.location.pathname),
  );
  const [authBusy, setAuthBusy] = useState(false);

  const navigate = useCallback((nextRoute: AppRouteId) => {
    setRequestedRoute(nextRoute);
    const nextPath = pathnameForRoute(nextRoute);
    if (window.location.pathname !== nextPath) {
      window.history.pushState({ route: nextRoute }, "", nextPath);
    }
  }, []);

  useEffect(() => {
    const onPopState = () => {
      setRequestedRoute(routeFromPathname(window.location.pathname));
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const decision = evaluateRouteAccess({
    authStatus: auth.status,
    requestedRoute,
    hasSelectedInstitution: tenant.selectedInstitution !== null,
    tenantStatus: tenant.status,
  });

  useEffect(() => {
    if (decision.reason === "initializing") {
      return;
    }
    if (decision.reason === "require_auth" && requestedRoute !== "login") {
      setRequestedRoute("login");
    }
    const targetPath = pathnameForRoute(decision.effectiveRoute);
    if (window.location.pathname !== targetPath) {
      window.history.replaceState(
        { route: decision.effectiveRoute },
        "",
        targetPath,
      );
    }
  }, [decision.effectiveRoute, decision.reason, requestedRoute]);

  const handleLogout = async () => {
    setAuthBusy(true);
    tenantController.clear();
    await authController.logout();
    setAuthBusy(false);
    navigate("login");
  };

  const handleLogoutAll = async () => {
    setAuthBusy(true);
    tenantController.clear();
    await authController.logoutAll();
    setAuthBusy(false);
    navigate("login");
  };

  const handleSelectInstitution = (tenantId: string) => {
    if (tenantController.selectInstitution(tenantId)) {
      navigate("institution");
    }
  };

  const workspaceKey = `${auth.user?.user_id ?? "anon"}:${tenant.selectedTenantId ?? "none"}:${tenant.epoch}`;

  const renderMainContent = () => {
    if (decision.reason === "initializing") {
      return <LoadingSpinner message="Restoring your session..." />;
    }

    const activeRoute = decision.effectiveRoute;

    if (activeRoute === "health") {
      return <SystemHealthDashboard />;
    }
    if (activeRoute === "login") {
      return <LoginPage />;
    }
    if (activeRoute === "account-password") {
      return <PasswordChangePage />;
    }
    if (activeRoute === "institution") {
      return <InstitutionOverviewPage onNavigate={navigate} />;
    }
    if (activeRoute === "academics-campuses") {
      return <CampusesPage />;
    }
    if (activeRoute === "academics-departments") {
      return <DepartmentsPage />;
    }
    if (activeRoute === "academics-programmes") {
      return <ProgrammesPage />;
    }
    if (activeRoute === "academics-calendar") {
      return <AcademicYearsSemestersPage />;
    }
    if (activeRoute === "academics-sections") {
      return <SectionsPage />;
    }
    if (activeRoute === "academics-subjects") {
      return <SubjectsPage />;
    }
    if (activeRoute === "academics-offerings") {
      return <OfferingsPage />;
    }
    if (activeRoute === "memberships") {
      return <MembershipsPage />;
    }
    if (activeRoute === "guardians") {
      return <GuardiansPage />;
    }

    return <InstitutionOverviewPage onNavigate={navigate} />;
  };

  return (
    <AppShell
      authenticated={auth.status === "authenticated"}
      user={auth.user}
      institutions={tenant.institutions}
      selectedInstitution={tenant.selectedInstitution}
      currentRoute={decision.effectiveRoute}
      onNavigate={navigate}
      onSelectInstitution={handleSelectInstitution}
      onLogout={() => void handleLogout()}
      onLogoutAll={() => void handleLogoutAll()}
      authBusy={authBusy}
    >
      {auth.status === "authenticated" && auth.expiredReason && (
        <div style={{ marginBottom: "1rem" }}>
          <AlertBanner tone="warning" message={auth.expiredReason} />
        </div>
      )}
      <div key={workspaceKey}>{renderMainContent()}</div>
    </AppShell>
  );
}

export function App() {
  return (
    <ErrorBoundary>
      <AuthProvider>
        <TenantProvider>
          <PortalRouter />
        </TenantProvider>
      </AuthProvider>
    </ErrorBoundary>
  );
}
