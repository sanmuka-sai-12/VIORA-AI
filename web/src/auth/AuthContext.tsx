import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  createApiClient,
  normalizeApiError,
  type TokenResponse,
  type VioraApiClient,
} from "../api/client";
import { RefreshCoordinator } from "./refreshLock";
import {
  createAuthController,
  type AuthController,
  type AuthSnapshot,
} from "./session";

export interface AuthContextValue {
  snapshot: AuthSnapshot;
  apiClient: VioraApiClient;
  controller: AuthController;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export interface AuthProviderProps {
  children: ReactNode;
  onSessionCleared?: () => void;
}

export function AuthProvider({ children, onSessionCleared }: AuthProviderProps) {
  // Access tokens remain strictly in-memory in closure state; never stored in web storage.
  const accessTokenRef = useRef<string | null>(null);
  const onSessionClearedRef = useRef(onSessionCleared);
  onSessionClearedRef.current = onSessionCleared;

  const { apiClient, controller, coordinator } = useMemo(() => {
    let authController: AuthController | null = null;

    const refreshCoordinator = new RefreshCoordinator<TokenResponse>({
      extractSessionId: (data) => data.session_id,
      onRemoteSessionEnded: (reason, message) => {
        authController?.handleRemoteSessionEnded(reason, message);
      },
      onRemoteSessionRefreshed: () => {
        if (
          authController &&
          authController.getSnapshot().status === "unauthenticated"
        ) {
          void authController.restoreSession();
        }
      },
    });

    const client = createApiClient({
      getAuthToken: () => accessTokenRef.current,
      onResponseError: (response, schemaPath) => {
        if (response.status === 401) {
          authController?.handleUnauthorizedResponse(schemaPath);
        }
      },
    });

    authController = createAuthController({
      apiClient: client,
      refreshCoordinator,
      normalizeError: normalizeApiError,
      setToken: (token) => {
        accessTokenRef.current = token;
      },
      getToken: () => accessTokenRef.current,
      onSessionCleared: () => {
        onSessionClearedRef.current?.();
      },
    });

    return {
      apiClient: client,
      controller: authController,
      coordinator: refreshCoordinator,
    };
  }, []);

  const [snapshot, setSnapshot] = useState<AuthSnapshot>(() =>
    controller.getSnapshot(),
  );

  useEffect(() => {
    const unsubscribe = controller.subscribe(setSnapshot);
    void controller.restoreSession();
    return () => {
      unsubscribe();
      coordinator.dispose();
    };
  }, [controller, coordinator]);

  const value = useMemo<AuthContextValue>(
    () => ({ snapshot, apiClient, controller }),
    [snapshot, apiClient, controller],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return ctx;
}
