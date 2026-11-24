import type React from "react";

export function LoadingSpinner({ message = "Loading..." }: { message?: string }) {
  return (
    <div style={{ padding: "2rem", textAlign: "center", color: "var(--color-text-muted)" }}>
      <div
        style={{
          display: "inline-block",
          width: "24px",
          height: "24px",
          border: "3px solid var(--color-border)",
          borderTopColor: "var(--color-brand-accent)",
          borderRadius: "50%",
          animation: "spin 1s linear infinite",
          marginBottom: "0.5rem",
        }}
      />
      <style>{`@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }`}</style>
      <div>{message}</div>
    </div>
  );
}

export function ErrorState({
  title = "Error loading content",
  message,
  onRetry,
}: {
  title?: string;
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div
      style={{
        padding: "2rem",
        background: "var(--color-bg-surface)",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-md)",
        textAlign: "center",
      }}
    >
      <h3 style={{ color: "var(--color-danger)", marginBottom: "0.5rem" }}>{title}</h3>
      {message && <p style={{ color: "var(--color-text-muted)", marginBottom: "1rem" }}>{message}</p>}
      {onRetry && (
        <button
          onClick={onRetry}
          style={{
            padding: "0.5rem 1rem",
            background: "var(--color-bg-subtle)",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-sm)",
          }}
        >
          Try Again
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div
      style={{
        padding: "3rem 1.5rem",
        background: "var(--color-bg-surface)",
        border: "1px dashed var(--color-border)",
        borderRadius: "var(--radius-md)",
        textAlign: "center",
      }}
    >
      <h3 style={{ marginBottom: "0.5rem" }}>{title}</h3>
      <p style={{ color: "var(--color-text-muted)", marginBottom: "1rem" }}>{description}</p>
      {action}
    </div>
  );
}
