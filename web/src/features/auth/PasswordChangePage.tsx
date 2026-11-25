import { useState, type FormEvent } from "react";
import { useAuth } from "../../auth/AuthContext";
import {
  AlertBanner,
  ConfirmDialog,
  FormField,
  PageHeader,
  SectionCard,
  UuidChip,
} from "../../components/common/UiPrimitives";
import { validatePasswordChange } from "../../tenant/validation";
import type { NormalizedApiError } from "../../api/client";

export function PasswordChangePage() {
  const { snapshot, controller } = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [apiError, setApiError] = useState<NormalizedApiError | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [confirmLogoutAll, setConfirmLogoutAll] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setValidationError(null);
    setApiError(null);

    const problem = validatePasswordChange(
      currentPassword,
      newPassword,
      confirmPassword,
    );
    if (problem) {
      setValidationError(problem);
      return;
    }

    setSubmitting(true);
    const result = await controller.changePassword(
      currentPassword,
      newPassword,
    );
    setSubmitting(false);

    if (!result.ok && result.error) {
      setApiError(result.error);
    } else {
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    }
  };

  return (
    <div className="viora-stack">
      <PageHeader
        title="Account Security & Sessions"
        subtitle="Inspect active session metadata or rotate your password."
      />

      {snapshot.user && (
        <SectionCard title="Current Identity & Session">
          <div className="viora-grid-2">
            <div>
              <div className="viora-field-hint">Display Name</div>
              <div style={{ fontWeight: 600 }}>{snapshot.user.display_name}</div>
            </div>
            <div>
              <div className="viora-field-hint">Account Status</div>
              <div style={{ fontWeight: 600 }}>{snapshot.user.status}</div>
            </div>
            <div>
              <div className="viora-field-hint">User ID</div>
              <UuidChip value={snapshot.user.user_id} />
            </div>
            <div>
              <div className="viora-field-hint">Session ID</div>
              <UuidChip value={snapshot.user.session_id} />
            </div>
            {snapshot.refreshExpiresAt && (
              <div>
                <div className="viora-field-hint">Refresh Window Ends</div>
                <div>{new Date(snapshot.refreshExpiresAt).toLocaleString()}</div>
              </div>
            )}
            {snapshot.sessionExpiresAt && (
              <div>
                <div className="viora-field-hint">Absolute Session Expiry</div>
                <div>{new Date(snapshot.sessionExpiresAt).toLocaleString()}</div>
              </div>
            )}
          </div>

          <div className="viora-row" style={{ marginTop: "1.1rem" }}>
            <button
              type="button"
              className="viora-btn"
              onClick={() => void controller.logout()}
            >
              Sign out this session
            </button>
            <button
              type="button"
              className="viora-btn viora-btn-danger"
              onClick={() => setConfirmLogoutAll(true)}
            >
              Sign out all active sessions
            </button>
          </div>
        </SectionCard>
      )}

      <SectionCard
        title="Change Password"
        subtitle="Passwords must contain 12 to 1024 characters. Changing your password revokes all active sessions and returns you to login."
      >
        <form
          onSubmit={handleSubmit}
          className="viora-stack"
          style={{ maxWidth: "460px" }}
          noValidate
        >
          {(validationError || apiError) && (
            <AlertBanner
              tone="error"
              title="Password update unsuccessful"
              message={validationError ?? apiError?.message}
              error={apiError}
            />
          )}

          <FormField label="Current password" htmlFor="pw-current">
            <input
              id="pw-current"
              type="password"
              className="viora-input"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              disabled={submitting}
              required
            />
          </FormField>

          <FormField
            label="New password"
            htmlFor="pw-new"
            hint="Minimum 12 characters."
          >
            <input
              id="pw-new"
              type="password"
              className="viora-input"
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              disabled={submitting}
              required
            />
          </FormField>

          <FormField label="Confirm new password" htmlFor="pw-confirm">
            <input
              id="pw-confirm"
              type="password"
              className="viora-input"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              disabled={submitting}
              required
            />
          </FormField>

          <div>
            <button
              type="submit"
              className="viora-btn viora-btn-primary"
              disabled={submitting}
            >
              {submitting ? "Updating password..." : "Update password"}
            </button>
          </div>
        </form>
      </SectionCard>

      <ConfirmDialog
        open={confirmLogoutAll}
        title="Sign out all active sessions?"
        message="This revokes every active refresh and access session for your account across all browser tabs and devices."
        confirmLabel="Sign out everywhere"
        danger
        onCancel={() => setConfirmLogoutAll(false)}
        onConfirm={() => {
          setConfirmLogoutAll(false);
          void controller.logoutAll();
        }}
      />
    </div>
  );
}
