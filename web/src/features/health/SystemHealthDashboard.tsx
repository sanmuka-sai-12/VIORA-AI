import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { LoadingSpinner, ErrorState } from "../../components/common/FeedbackStates";

interface HealthStatus {
  live: boolean | null;
  ready: boolean | null;
  readyDetails?: string;
  lastChecked: string | null;
}

export function SystemHealthDashboard() {
  const [health, setHealth] = useState<HealthStatus>({
    live: null,
    ready: null,
    lastChecked: null,
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const checkHealth = async () => {
    setLoading(true);
    setError(null);
    try {
      const [liveRes, readyRes] = await Promise.all([
        api.GET("/api/v1/health/live"),
        api.GET("/api/v1/health/ready"),
      ]);

      const live = liveRes.data?.status === "live";
      const ready = readyRes.response.status === 200;
      let readyDetails = undefined;
      if (!ready && readyRes.error) {
        readyDetails = `${readyRes.error.code}: ${readyRes.error.message}`;
      }

      setHealth({
        live,
        ready,
        readyDetails,
        lastChecked: new Date().toLocaleTimeString(),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to connect to backend");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void checkHealth();
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <div>
        <h1 style={{ fontSize: "1.75rem", fontWeight: 700, color: "var(--color-brand-primary)" }}>
          Platform Foundation Status
        </h1>
        <p style={{ color: "var(--color-text-muted)", marginTop: "0.25rem" }}>
          Phase 0 infrastructure health and backend readiness verification
        </p>
      </div>

      {loading && <LoadingSpinner message="Checking backend service connectivity..." />}

      {error && (
        <ErrorState
          title="Backend Connection Unreachable"
          message={error}
          onRetry={checkHealth}
        />
      )}

      {!loading && !error && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
            gap: "1.5rem",
          }}
        >
          <div
            style={{
              padding: "1.5rem",
              background: "var(--color-bg-surface)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border)",
              boxShadow: "var(--shadow-sm)",
            }}
          >
            <div style={{ fontSize: "0.875rem", color: "var(--color-text-muted)" }}>
              API Liveness (/api/v1/health/live)
            </div>
            <div
              style={{
                fontSize: "1.5rem",
                fontWeight: 600,
                marginTop: "0.5rem",
                color: health.live ? "var(--color-success)" : "var(--color-danger)",
              }}
            >
              {health.live ? "Operational" : "Unavailable"}
            </div>
            <p style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "0.5rem" }}>
              Backend application process is responsive
            </p>
          </div>

          <div
            style={{
              padding: "1.5rem",
              background: "var(--color-bg-surface)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border)",
              boxShadow: "var(--shadow-sm)",
            }}
          >
            <div style={{ fontSize: "0.875rem", color: "var(--color-text-muted)" }}>
              Database Readiness (/api/v1/health/ready)
            </div>
            <div
              style={{
                fontSize: "1.5rem",
                fontWeight: 600,
                marginTop: "0.5rem",
                color: health.ready ? "var(--color-success)" : "var(--color-warning)",
              }}
            >
              {health.ready ? "Database Ready" : "Database Standby"}
            </div>
            <p style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "0.5rem" }}>
              {health.ready
                ? "PostgreSQL connection pool verified"
                : health.readyDetails || "Service container not yet connected"}
            </p>
          </div>
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "1rem" }}>
        <button
          onClick={checkHealth}
          style={{
            padding: "0.5rem 1rem",
            backgroundColor: "var(--color-brand-primary)",
            color: "white",
            border: "none",
            borderRadius: "var(--radius-sm)",
            fontSize: "0.875rem",
          }}
        >
          Refresh Status
        </button>
      </div>
    </div>
  );
}
