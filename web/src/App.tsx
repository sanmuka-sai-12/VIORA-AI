import { useState } from "react";
import { AppShell } from "./components/layout/AppShell";
import { ErrorBoundary } from "./components/common/ErrorBoundary";
import { SystemHealthDashboard } from "./features/health/SystemHealthDashboard";

export function App() {
  const [currentView] = useState<"health">("health");

  return (
    <ErrorBoundary>
      <AppShell>
        {currentView === "health" && <SystemHealthDashboard />}
      </AppShell>
    </ErrorBoundary>
  );
}
