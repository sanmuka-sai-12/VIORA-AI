import type {
  AuthenticatedUser,
  NormalizedApiError,
  TokenResponse,
  VioraApiClient,
} from "../api/client";
import type { RefreshAttemptResult, RefreshCoordinator } from "./refreshLock";

export type AuthStatus = "initializing" | "authenticated" | "unauthenticated";

export interface AuthSnapshot {
  status: AuthStatus;
  user: AuthenticatedUser | null;
  sessionId: string | null;
  accessExpiresAtMs: number | null;
  refreshExpiresAt: string | null;
  sessionExpiresAt: string | null;
  expiredReason: string | null;
  lastError: NormalizedApiError | null;
}

export interface AuthControllerDeps {
  apiClient: VioraApiClient;
  refreshCoordinator: RefreshCoordinator<TokenResponse>;
  normalizeError: (
    error: unknown,
    status: number,
    fallbackMessage?: string,
  ) => NormalizedApiError;
  setToken: (token: string | null) => void;
  getToken: () => string | null;
  onSessionCleared?: () => void;
  now?: () => number;
}

export interface LoginInput {
  kind?: "email" | "phone";
  identifier: string;
  password: string;
}

export interface AuthController {
  getSnapshot(): AuthSnapshot;
  subscribe(listener: (snapshot: AuthSnapshot) => void): () => void;
  restoreSession(): Promise<boolean>;
  login(input: LoginInput): Promise<{ ok: boolean; error?: NormalizedApiError }>;
  refreshSession(options?: {
    markExpiredOnFailure?: boolean;
  }): Promise<RefreshAttemptResult<TokenResponse>>;
  logout(): Promise<void>;
  logoutAll(): Promise<void>;
  changePassword(
    currentPassword: string,
    newPassword: string,
  ): Promise<{ ok: boolean; error?: NormalizedApiError }>;
  handleRemoteSessionEnded(
    reason: "logout" | "expired" | "ambiguous",
    message?: string,
  ): void;
  handleUnauthorizedResponse(schemaPath: string): void;
  runWithFreshToken<R>(
    operation: () => Promise<{
      data?: R;
      error?: unknown;
      response: Response;
    }>,
  ): Promise<{
    data?: R;
    error?: unknown;
    response: Response;
  }>;
}

export function createAuthController(deps: AuthControllerDeps): AuthController {
  const now = deps.now ?? (() => Date.now());
  const listeners = new Set<(snapshot: AuthSnapshot) => void>();

  let snapshot: AuthSnapshot = {
    status: "initializing",
    user: null,
    sessionId: null,
    accessExpiresAtMs: null,
    refreshExpiresAt: null,
    sessionExpiresAt: null,
    expiredReason: null,
    lastError: null,
  };

  let restorePromise: Promise<boolean> | null = null;
  let handlingUnauthorized = false;

  function emit(next: AuthSnapshot): void {
    snapshot = next;
    for (const listener of listeners) {
      listener(snapshot);
    }
  }

  function applyTokenMetadata(tokenData: TokenResponse): void {
    deps.setToken(tokenData.access_token);
    snapshot = {
      ...snapshot,
      sessionId: tokenData.session_id,
      accessExpiresAtMs: now() + tokenData.expires_in * 1000,
      refreshExpiresAt: tokenData.refresh_expires_at,
      sessionExpiresAt: tokenData.session_expires_at,
      expiredReason: null,
      lastError: null,
    };
  }

  function clearLocalSession(
    expiredReason: string | null = null,
    lastError: NormalizedApiError | null = null,
  ): void {
    deps.setToken(null);
    deps.onSessionCleared?.();
    emit({
      status: "unauthenticated",
      user: null,
      sessionId: null,
      accessExpiresAtMs: null,
      refreshExpiresAt: null,
      sessionExpiresAt: null,
      expiredReason,
      lastError,
    });
  }

  async function fetchCurrentUser(): Promise<{
    user?: AuthenticatedUser;
    error?: NormalizedApiError;
    status: number;
  }> {
    try {
      const res = await deps.apiClient.GET("/api/v1/auth/me");
      if (res.data) {
        return { user: res.data, status: res.response.status };
      }
      return {
        status: res.response.status,
        error: deps.normalizeError(
          res.error,
          res.response.status,
          "Failed to load user profile",
        ),
      };
    } catch {
      return {
        status: 0,
        error: {
          status: 0,
          code: "network_error",
          message: "Unable to reach authentication service",
        },
      };
    }
  }

  async function executeSingleRefresh(): Promise<
    RefreshAttemptResult<TokenResponse>
  > {
    try {
      const res = await deps.apiClient.POST("/api/v1/auth/refresh");
      if (res.data) {
        return { kind: "success", data: res.data };
      }
      if (res.response.status === 401 || res.response.status === 403) {
        const err = deps.normalizeError(
          res.error,
          res.response.status,
          "Session expired. Please log in again.",
        );
        return { kind: "unauthorized", message: err.message };
      }
      const err = deps.normalizeError(
        res.error,
        res.response.status,
        "Session refresh returned an unexpected server response. Please log in again.",
      );
      return { kind: "ambiguous", message: err.message };
    } catch {
      // Strictly never retry on ambiguous network failures (docs/authentication.md).
      return {
        kind: "ambiguous",
        message:
          "Session refresh could not be confirmed due to a network error. Please log in again.",
      };
    }
  }

  async function refreshSession(
    options: { markExpiredOnFailure?: boolean } = {},
  ): Promise<RefreshAttemptResult<TokenResponse>> {
    const wasAuthenticated = snapshot.status === "authenticated";
    const result = await deps.refreshCoordinator.run(executeSingleRefresh);

    if (result.kind === "success") {
      applyTokenMetadata(result.data);
      const profile = await fetchCurrentUser();
      if (profile.user) {
        emit({
          ...snapshot,
          status: "authenticated",
          user: profile.user,
          expiredReason: null,
          lastError: null,
        });
        return result;
      }
      clearLocalSession(
        options.markExpiredOnFailure || wasAuthenticated
          ? "Your session is no longer valid. Please log in again."
          : null,
        profile.error ?? null,
      );
      return {
        kind: "unauthorized",
        message: "Unable to verify authenticated profile",
      };
    }

    if (result.kind === "ambiguous") {
      clearLocalSession(
        result.message,
        {
          status: 0,
          code: "refresh_ambiguous",
          message: result.message,
        },
      );
      return result;
    }

    clearLocalSession(
      options.markExpiredOnFailure || wasAuthenticated
        ? "Your session has expired. Please log in again."
        : null,
      null,
    );
    return result;
  }

  async function restoreSession(): Promise<boolean> {
    if (restorePromise) {
      return restorePromise;
    }
    restorePromise = (async () => {
      emit({
        ...snapshot,
        status: "initializing",
        lastError: null,
      });
      const result = await refreshSession({ markExpiredOnFailure: false });
      return result.kind === "success";
    })().finally(() => {
      restorePromise = null;
    });
    return restorePromise;
  }

  async function login(
    input: LoginInput,
  ): Promise<{ ok: boolean; error?: NormalizedApiError }> {
    const trimmedIdentifier = input.identifier.trim();
    if (!trimmedIdentifier || !input.password) {
      const error: NormalizedApiError = {
        status: 422,
        code: "validation_error",
        message: "Email/identifier and password are required.",
      };
      emit({ ...snapshot, lastError: error });
      return { ok: false, error };
    }

    try {
      const res = await deps.apiClient.POST("/api/v1/auth/login", {
        body: {
          kind: input.kind ?? "email",
          identifier: trimmedIdentifier,
          password: input.password,
        } as never,
      });

      if (!res.data) {
        const error = deps.normalizeError(
          res.error,
          res.response.status,
          "Invalid credentials.",
        );
        clearLocalSession(null, error);
        return { ok: false, error };
      }

      applyTokenMetadata(res.data);
      const profile = await fetchCurrentUser();
      if (!profile.user) {
        const error = profile.error ?? {
          status: profile.status || 401,
          code: "profile_unavailable",
          message: "Login succeeded but profile could not be loaded.",
        };
        clearLocalSession(null, error);
        return { ok: false, error };
      }

      emit({
        ...snapshot,
        status: "authenticated",
        user: profile.user,
        expiredReason: null,
        lastError: null,
      });
      return { ok: true };
    } catch {
      const error: NormalizedApiError = {
        status: 0,
        code: "network_error",
        message: "Unable to connect to authentication server.",
      };
      emit({ ...snapshot, lastError: error });
      return { ok: false, error };
    }
  }

  async function logout(): Promise<void> {
    try {
      if (deps.getToken()) {
        await deps.apiClient.POST("/api/v1/auth/logout", {
          body: { session_id: snapshot.sessionId } as never,
        });
      }
    } catch {
      // Local credentials must still be wiped even if remote logout fails.
    } finally {
      deps.refreshCoordinator.notifySessionEnded("logout");
      clearLocalSession(null, null);
    }
  }

  async function logoutAll(): Promise<void> {
    try {
      if (deps.getToken()) {
        await deps.apiClient.POST("/api/v1/auth/logout-all");
      }
    } catch {
      // Local credentials must still be wiped even if remote logout-all fails.
    } finally {
      deps.refreshCoordinator.notifySessionEnded("logout");
      clearLocalSession("Signed out from all active sessions.", null);
    }
  }

  async function changePassword(
    currentPassword: string,
    newPassword: string,
  ): Promise<{ ok: boolean; error?: NormalizedApiError }> {
    if (!currentPassword) {
      return {
        ok: false,
        error: {
          status: 422,
          code: "validation_error",
          message: "Current password is required.",
        },
      };
    }
    if (newPassword.length < 12 || newPassword.length > 1024) {
      return {
        ok: false,
        error: {
          status: 422,
          code: "validation_error",
          message: "New password must be between 12 and 1024 characters.",
        },
      };
    }

    try {
      const res = await deps.apiClient.POST("/api/v1/auth/password/change", {
        body: {
          current_password: currentPassword,
          new_password: newPassword,
        } as never,
      });
      if (res.response.status === 204 || res.response.ok) {
        deps.refreshCoordinator.notifySessionEnded(
          "logout",
          "Password changed. All sessions have been signed out.",
        );
        clearLocalSession(
          "Password updated. All sessions were revoked — please sign in with your new password.",
          null,
        );
        return { ok: true };
      }

      if (res.response.status === 401) {
        const error = deps.normalizeError(
          res.error,
          401,
          "Current password is incorrect or your session has expired.",
        );
        clearLocalSession(error.message, error);
        return { ok: false, error };
      }

      const error = deps.normalizeError(
        res.error,
        res.response.status,
        "Unable to update password.",
      );
      return { ok: false, error };
    } catch {
      return {
        ok: false,
        error: {
          status: 0,
          code: "network_error",
          message: "Unable to reach server while updating password.",
        },
      };
    }
  }

  function handleRemoteSessionEnded(
    reason: "logout" | "expired" | "ambiguous",
    message?: string,
  ): void {
    if (snapshot.status === "unauthenticated" && !deps.getToken()) {
      return;
    }
    const reasonText =
      reason === "logout"
        ? message ?? "Signed out from another tab."
        : message ?? "Your session has ended. Please log in again.";
    clearLocalSession(reasonText, null);
  }

  function handleUnauthorizedResponse(schemaPath: string): void {
    if (
      handlingUnauthorized ||
      snapshot.status !== "authenticated" ||
      schemaPath.startsWith("/api/v1/auth/")
    ) {
      return;
    }
    handlingUnauthorized = true;
    void refreshSession({ markExpiredOnFailure: true }).finally(() => {
      handlingUnauthorized = false;
    });
  }

  async function runWithFreshToken<R>(
    operation: () => Promise<{
      data?: R;
      error?: unknown;
      response: Response;
    }>,
  ) {
    const first = await operation();
    if (first.response.status !== 401) {
      return first;
    }
    const refreshed = await refreshSession({ markExpiredOnFailure: true });
    if (refreshed.kind !== "success") {
      return first;
    }
    return operation();
  }

  return {
    getSnapshot() {
      return snapshot;
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    restoreSession,
    login,
    refreshSession,
    logout,
    logoutAll,
    changePassword,
    handleRemoteSessionEnded,
    handleUnauthorizedResponse,
    runWithFreshToken,
  };
}
