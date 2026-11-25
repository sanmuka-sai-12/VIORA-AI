import { useEffect, useState, type ReactNode } from "react";
import type { NormalizedApiError } from "../../api/client";

export type BannerTone = "error" | "warning" | "success" | "info";

export function AlertBanner({
  tone = "info",
  title,
  message,
  error,
  onDismiss,
  action,
}: {
  tone?: BannerTone;
  title?: string;
  message?: string;
  error?: NormalizedApiError | null;
  onDismiss?: () => void;
  action?: ReactNode;
}) {
  const displayMessage = message ?? error?.message;
  if (!displayMessage) {
    return null;
  }

  return (
    <div
      className={`viora-alert viora-alert-${tone}`}
      role={tone === "error" ? "alert" : "status"}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: "0.2rem" }}>
        {title && <strong>{title}</strong>}
        <span>{displayMessage}</span>
        {error && (
          <span style={{ fontSize: "0.75rem", opacity: 0.85 }}>
            Code: <code>{error.code}</code>
            {error.status > 0 ? ` (HTTP ${error.status})` : ""}
            {error.requestId ? ` • Request ID: ${error.requestId}` : ""}
          </span>
        )}
      </div>
      <div className="viora-row">
        {action}
        {onDismiss && (
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            onClick={onDismiss}
            aria-label="Dismiss notice"
          >
            Dismiss
          </button>
        )}
      </div>
    </div>
  );
}

export function StatusBadge({
  tone,
  children,
}: {
  tone: "success" | "warning" | "danger" | "neutral" | "brand";
  children: ReactNode;
}) {
  return <span className={`viora-badge viora-badge-${tone}`}>{children}</span>;
}

export function ActiveBadge({ active }: { active: boolean }) {
  return (
    <StatusBadge tone={active ? "success" : "neutral"}>
      {active ? "Active" : "Archived"}
    </StatusBadge>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="viora-page-header">
      <div>
        <h1 className="viora-page-title">{title}</h1>
        {subtitle && <p className="viora-page-subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="viora-row">{actions}</div>}
    </div>
  );
}

export function SectionCard({
  title,
  subtitle,
  actions,
  children,
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="viora-card">
      {(title || subtitle || actions) && (
        <div className="viora-card-header">
          <div>
            {title && (
              <h2 style={{ fontSize: "1.05rem", fontWeight: 600 }}>{title}</h2>
            )}
            {subtitle && (
              <p
                style={{
                  fontSize: "0.84rem",
                  color: "var(--color-text-muted)",
                  marginTop: "0.15rem",
                }}
              >
                {subtitle}
              </p>
            )}
          </div>
          {actions && <div className="viora-row">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export interface TableColumn<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  emptyTitle = "No records found",
  emptyDescription = "No entries match the current selection.",
  emptyAction,
}: {
  columns: TableColumn<T>[];
  rows: readonly T[];
  rowKey: (row: T) => string;
  emptyTitle?: string;
  emptyDescription?: string;
  emptyAction?: ReactNode;
}) {
  if (rows.length === 0) {
    return (
      <div
        style={{
          padding: "2rem 1.25rem",
          border: "1px dashed var(--color-border)",
          borderRadius: "var(--radius-md)",
          textAlign: "center",
          background: "var(--color-bg-surface)",
        }}
      >
        <h3 style={{ fontSize: "0.98rem", marginBottom: "0.35rem" }}>
          {emptyTitle}
        </h3>
        <p
          style={{
            fontSize: "0.85rem",
            color: "var(--color-text-muted)",
            marginBottom: emptyAction ? "0.9rem" : 0,
          }}
        >
          {emptyDescription}
        </p>
        {emptyAction}
      </div>
    );
  }

  return (
    <div className="viora-table-wrap">
      <table className="viora-table">
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key} scope="col">
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((col) => (
                <td key={col.key}>{col.render(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PaginationBar({
  limit,
  onLimitChange,
  hasPrevious,
  hasNext,
  onPrevious,
  onNext,
  count,
}: {
  limit: number;
  onLimitChange: (nextLimit: number) => void;
  hasPrevious: boolean;
  hasNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
  count: number;
}) {
  return (
    <div
      className="viora-row"
      style={{
        justifyContent: "space-between",
        paddingTop: "0.75rem",
        fontSize: "0.84rem",
        color: "var(--color-text-muted)",
      }}
    >
      <span>
        Showing {count} record{count === 1 ? "" : "s"} on this page
      </span>
      <div className="viora-row">
        <label className="viora-row" style={{ gap: "0.4rem" }}>
          <span>Rows:</span>
          <select
            className="viora-select"
            style={{ width: "auto", padding: "0.28rem 0.5rem" }}
            value={limit}
            onChange={(e) => onLimitChange(Number(e.target.value))}
          >
            <option value={10}>10</option>
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </label>
        <button
          type="button"
          className="viora-btn viora-btn-sm"
          disabled={!hasPrevious}
          onClick={onPrevious}
        >
          Previous
        </button>
        <button
          type="button"
          className="viora-btn viora-btn-sm"
          disabled={!hasNext}
          onClick={onNext}
        >
          Next
        </button>
      </div>
    </div>
  );
}

export function FormField({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div className="viora-field">
      <label className="viora-field-label" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
      {hint && <span className="viora-field-hint">{hint}</span>}
    </div>
  );
}

export function ModalDialog({
  open,
  title,
  description,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) {
      return;
    }
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [open, onClose]);

  if (!open) {
    return null;
  }

  return (
    <div
      className="viora-modal-backdrop"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          onClose();
        }
      }}
    >
      <div
        className="viora-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div
          className="viora-row"
          style={{ justifyContent: "space-between", marginBottom: "0.75rem" }}
        >
          <div>
            <h2 style={{ fontSize: "1.05rem", fontWeight: 700 }}>{title}</h2>
            {description && (
              <p
                style={{
                  fontSize: "0.84rem",
                  color: "var(--color-text-muted)",
                  marginTop: "0.2rem",
                }}
              >
                {description}
              </p>
            )}
          </div>
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            onClick={onClose}
            aria-label="Close dialog"
          >
            Close
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  danger = false,
  busy = false,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <ModalDialog open={open} title={title} onClose={onCancel}>
      <div className="viora-stack">
        <p style={{ fontSize: "0.9rem" }}>{message}</p>
        <div className="viora-row" style={{ justifyContent: "flex-end" }}>
          <button
            type="button"
            className="viora-btn"
            disabled={busy}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="button"
            className={`viora-btn ${danger ? "viora-btn-danger" : "viora-btn-primary"}`}
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? "Working..." : confirmLabel}
          </button>
        </div>
      </div>
    </ModalDialog>
  );
}

export function UuidChip({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      // Clipboard API may be restricted outside HTTPS/user focus.
    }
  };

  return (
    <span className="viora-row" style={{ gap: "0.35rem" }}>
      <code className="viora-mono" title={value}>
        {value.slice(0, 8)}...{value.slice(-4)}
      </code>
      <button
        type="button"
        className="viora-btn viora-btn-sm"
        style={{ padding: "0.1rem 0.4rem", fontSize: "0.7rem" }}
        onClick={handleCopy}
        title={`Copy UUID: ${value}`}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </span>
  );
}
