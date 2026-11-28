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
import {
  AcademicStructurePage,
  academicTabFromPathname,
  type AcademicTabId,
} from "./features/college/AcademicStructurePage";
import { CollegeFacultyPage } from "./features/college/CollegeFacultyPage";
import { CollegePermissionsPage } from "./features/college/CollegePermissionsPage";
import { CollegeStudentsPage } from "./features/college/CollegeStudentsPage";
import { LoginPage } from "./features/auth/LoginPage";
import { PasswordChangePage } from "./features/auth/PasswordChangePage";
import { GuardiansPage } from "./features/guardians/GuardiansPage";
import { SystemHealthDashboard } from "./features/health/SystemHealthDashboard";
import { InstitutionOverviewPage } from "./features/institution/InstitutionOverviewPage";
import { StudentLearningPage } from "./features/learning/StudentLearningPage";
import { clearAllLearningSessionHints } from "./features/learning/learningSessionState";
import { TeacherClassPage } from "./features/teaching/TeacherClassPage";
import { TeacherDepartmentPage } from "./features/teaching/TeacherDepartmentPage";
import { TeacherSubjectsPage } from "./features/teaching/TeacherSubjectsPage";
import { defaultRouteForRoles } from "./tenant/navigation";
import { TenantProvider, useTenant } from "./tenant/TenantContext";

function PortalRouter() {
  const { snapshot: auth, controller: authController } = useAuth();
  const { snapshot: tenant, controller: tenantController } = useTenant();

  const [requestedRoute, setRequestedRoute] = useState<AppRouteId>(() =>
    routeFromPathname(window.location.pathname),
  );
  const [academicTab, setAcademicTab] = useState<AcademicTabId>(() =>
    academicTabFromPathname(window.location.pathname),
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
      setAcademicTab(academicTabFromPathname(window.location.pathname));
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const defaultRoute = defaultRouteForRoles(
    tenant.selectedInstitution?.roles ?? [],
    tenant.selectedInstitution !== null,
  );

  const decision = evaluateRouteAccess({
    authStatus: auth.status,
    requestedRoute,
    hasSelectedInstitution: tenant.selectedInstitution !== null,
    tenantStatus: tenant.status,
    defaultRoute,
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
    clearAllLearningSessionHints();
    tenantController.clear();
    await authController.logout();
    setAuthBusy(false);
    navigate("login");
  };

  const handleSelectInstitution = (tenantId: string) => {
    const inst = tenant.institutions.find((item) => item.tenant_id === tenantId);
    if (tenantController.selectInstitution(tenantId)) {
      navigate(defaultRouteForRoles(inst?.roles ?? [], true));
    }
  };

  const workspaceKey = `${auth.user?.user_id ?? "anon"}:${tenant.selectedTenantId ?? "none"}:${tenant.epoch}`;

  const renderMainContent = () => {
    if (decision.reason === "initializing") {
      return <LoadingSpinner message="Restoring your workspace..." />;
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
    if (activeRoute === "student-learning") {
      return <StudentLearningPage />;
    }
    if (activeRoute === "teacher-class") {
      return <TeacherClassPage />;
    }
    if (activeRoute === "teacher-subjects") {
      return <TeacherSubjectsPage />;
    }
    if (activeRoute === "teacher-department") {
      return <TeacherDepartmentPage />;
    }
    if (activeRoute === "head-students") {
      return <CollegeStudentsPage />;
    }
    if (activeRoute === "head-faculty") {
      return <CollegeFacultyPage />;
    }
    if (activeRoute === "head-academics") {
      return <AcademicStructurePage initialTab={academicTab} />;
    }
    if (activeRoute === "head-permissions") {
      return <CollegePermissionsPage />;
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
