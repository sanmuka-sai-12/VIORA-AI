import { useState, type FormEvent } from "react";
import { useAuth } from "../../auth/AuthContext";
import { AlertBanner, FormField, SectionCard } from "../../components/common/UiPrimitives";

export function LoginPage() {
  const { snapshot, controller } = useAuth();
  const [kind, setKind] = useState<"email" | "phone">("email");
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
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

  return (
    <div style={{ maxWidth: "460px", margin: "2rem auto" }}>
      <SectionCard
        title="Sign in to VIORA-AI"
        subtitle="Use your institution-verified email or phone credential."
      >
        <form onSubmit={handleSubmit} className="viora-stack" noValidate>
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

          <p
            style={{
              fontSize: "0.78rem",
              color: "var(--color-text-muted)",
              lineHeight: 1.45,
            }}
          >
            Sign in with your institution-verified email or phone number. Contact
            your college administrator if you need account access.
          </p>
        </form>
      </SectionCard>
    </div>
  );
}
