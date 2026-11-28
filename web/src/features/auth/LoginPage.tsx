import { useState, type FormEvent } from "react";
import { normalizeApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { AlertBanner, FormField, SectionCard } from "../../components/common/UiPrimitives";

function initialInviteTokenFromUrl(): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get("invite") ?? "";
}

export function LoginPage() {
  const { snapshot, controller } = useAuth();
  const initialInvite = initialInviteTokenFromUrl();

  const [mode, setMode] = useState<"login" | "invite">(
    initialInvite ? "invite" : "login",
  );
  const [kind, setKind] = useState<"email" | "phone">("email");
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");

  const [inviteToken, setInviteToken] = useState(initialInvite);
  const [invitePassword, setInvitePassword] = useState("");
  const [inviteConfirm, setInviteConfirm] = useState("");
  const [inviteSuccess, setInviteSuccess] = useState<string | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setFormError(null);
    if (!identifier.trim() || !password) {
      setFormError("Please enter both your identifier and password.");
      return;
    }

    setSubmitting(true);
    const result = await controller.login({
      kind,
      identifier,
      password,
    });
    setSubmitting(false);

    if (!result.ok && result.error) {
      setFormError(result.error.message);
    } else {
      setPassword("");
    }
  };

  const handleAcceptInvite = async (e: FormEvent) => {
    e.preventDefault();
    setFormError(null);
    setInviteSuccess(null);

    if (!inviteToken.trim()) {
      setFormError("Please enter your institution invitation token.");
      return;
    }
    if (invitePassword.length < 12) {
      setFormError("Password must contain at least 12 characters.");
      return;
    }
    if (invitePassword !== inviteConfirm) {
      setFormError("Passwords do not match.");
      return;
    }

    setSubmitting(true);
    try {
      const response = await fetch("/api/v1/auth/accept-invite", {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-Viora-CSRF": "1",
        },
        body: JSON.stringify({
          token: inviteToken.trim(),
          new_password: invitePassword,
        }),
      });
      if (!response.ok) {
        let errorBody: unknown = null;
        try {
          errorBody = await response.json();
        } catch {
          errorBody = null;
        }
        const err = normalizeApiError(
          errorBody,
          response.status,
          "Invitation token is invalid or expired.",
        );
        setFormError(err.message);
      } else {
        setInviteToken("");
        setInvitePassword("");
        setInviteConfirm("");
        setInviteSuccess(
          "Your account is now active. Sign in with your email and new password.",
        );
        setMode("login");
        if (typeof window !== "undefined" && window.location.search.includes("invite=")) {
          const url = new URL(window.location.href);
          url.searchParams.delete("invite");
          window.history.replaceState({}, "", url.toString());
        }
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ maxWidth: "460px", margin: "2rem auto" }}>
      <SectionCard
        title={mode === "login" ? "Sign in to VIORA-AI" : "Accept Institution Invitation"}
        subtitle={
          mode === "login"
            ? "Use your institution-verified email or phone credential."
            : "Set your password to activate your institution-provisioned account."
        }
      >
        {mode === "login" ? (
          <form onSubmit={handleSubmit} className="viora-stack" noValidate>
            {inviteSuccess && (
              <AlertBanner
                tone="success"
                title="Account activated"
                message={inviteSuccess}
              />
            )}

            {snapshot.expiredReason && (
              <AlertBanner
                tone="warning"
                title="Session notice"
                message={snapshot.expiredReason}
              />
            )}

            {(formError || snapshot.lastError) && !snapshot.expiredReason && (
              <AlertBanner
                tone="error"
                title="Authentication failed"
                message={formError ?? snapshot.lastError?.message}
                error={snapshot.lastError}
              />
            )}

            {formError && snapshot.expiredReason && (
              <AlertBanner
                tone="error"
                title="Authentication failed"
                message={formError}
                error={snapshot.lastError}
              />
            )}

            <FormField label="Credential type" htmlFor="login-kind">
              <select
                id="login-kind"
                className="viora-select"
                value={kind}
                onChange={(e) => setKind(e.target.value as "email" | "phone")}
                disabled={submitting}
              >
                <option value="email">Verified Email</option>
                <option value="phone">Verified Phone</option>
              </select>
            </FormField>

            <FormField
              label={kind === "email" ? "Email address" : "Phone number"}
              htmlFor="login-identifier"
            >
              <input
                id="login-identifier"
                type={kind === "email" ? "email" : "tel"}
                className="viora-input"
                autoComplete="username"
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
                placeholder={
                  kind === "email" ? "owner@northbridge.edu" : "+15550101234"
                }
                disabled={submitting}
                required
              />
            </FormField>

            <FormField label="Password" htmlFor="login-password">
              <input
                id="login-password"
                type="password"
                className="viora-input"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={submitting}
                required
              />
            </FormField>

            <button
              type="submit"
              className="viora-btn viora-btn-primary"
              disabled={submitting}
            >
              {submitting ? "Signing in..." : "Sign in"}
            </button>

            <div className="viora-row" style={{ justifyContent: "space-between", alignItems: "center" }}>
              <p className="viora-muted" style={{ fontSize: "0.8rem", margin: 0 }}>
                Accounts are provisioned by your institution administrator.
              </p>
              <button
                type="button"
                className="viora-btn viora-btn-sm"
                onClick={() => {
                  setFormError(null);
                  setMode("invite");
                }}
              >
                Accept Invite
              </button>
            </div>
          </form>
        ) : (
          <form onSubmit={handleAcceptInvite} className="viora-stack" noValidate>
            {formError && (
              <AlertBanner
                tone="error"
                title="Unable to activate account"
                message={formError}
              />
            )}

            <FormField label="Invitation Token" htmlFor="invite-token">
              <input
                id="invite-token"
                type="text"
                className="viora-input viora-mono"
                value={inviteToken}
                onChange={(e) => setInviteToken(e.target.value)}
                placeholder="Paste your one-time invitation token"
                disabled={submitting}
                required
              />
            </FormField>

            <FormField label="New Password (12+ chars)" htmlFor="invite-password">
              <input
                id="invite-password"
                type="password"
                className="viora-input"
                autoComplete="new-password"
                value={invitePassword}
                onChange={(e) => setInvitePassword(e.target.value)}
                disabled={submitting}
                required
              />
            </FormField>

            <FormField label="Confirm Password" htmlFor="invite-confirm">
              <input
                id="invite-confirm"
                type="password"
                className="viora-input"
                autoComplete="new-password"
                value={inviteConfirm}
                onChange={(e) => setInviteConfirm(e.target.value)}
                disabled={submitting}
                required
              />
            </FormField>

            <div className="viora-row" style={{ justifyContent: "space-between" }}>
              <button
                type="button"
                className="viora-btn"
                onClick={() => {
                  setFormError(null);
                  setMode("login");
                }}
                disabled={submitting}
              >
                Back to Sign in
              </button>
              <button
                type="submit"
                className="viora-btn viora-btn-primary"
                disabled={submitting}
              >
                {submitting ? "Activating..." : "Activate Account"}
              </button>
            </div>
          </form>
        )}
      </SectionCard>
    </div>
  );
}
