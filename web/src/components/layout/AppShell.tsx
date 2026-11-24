import type React from "react";

export function AppHeader() {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0.75rem 1.5rem",
        backgroundColor: "var(--color-bg-surface)",
        borderBottom: "1px solid var(--color-border)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
        <span
          style={{
            fontSize: "1.25rem",
            fontWeight: 700,
            letterSpacing: "-0.025em",
            color: "var(--color-brand-primary)",
          }}
        >
          VIORA-AI
        </span>
        <span
          style={{
            fontSize: "0.75rem",
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            backgroundColor: "var(--color-bg-subtle)",
            color: "var(--color-text-muted)",
            padding: "0.15rem 0.5rem",
            borderRadius: "var(--radius-sm)",
          }}
        >
          Academic Platform
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "1rem", fontSize: "0.875rem" }}>
        <span style={{ color: "var(--color-text-muted)" }}>Workspace: Global</span>
      </div>
    </header>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <AppHeader />
      <main style={{ flex: 1, padding: "2rem", maxWidth: "1200px", margin: "0 auto", width: "100%" }}>
        {children}
      </main>
      <footer
        style={{
          padding: "1rem 1.5rem",
          textAlign: "center",
          fontSize: "0.75rem",
          color: "var(--color-text-muted)",
          borderTop: "1px solid var(--color-border)",
          backgroundColor: "var(--color-bg-surface)",
        }}
      >
        VIORA-AI Platform &copy; 2026. All rights reserved.
      </footer>
    </div>
  );
}
