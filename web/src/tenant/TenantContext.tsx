import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { normalizeApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import {
  createTenantController,
  type TenantController,
  type TenantSnapshot,
} from "./tenantState";

export interface TenantContextValue {
  snapshot: TenantSnapshot;
  controller: TenantController;
}

const TenantContext = createContext<TenantContextValue | null>(null);

export function TenantProvider({ children }: { children: ReactNode }) {
  const { apiClient, snapshot: authSnapshot } = useAuth();

  const controller = useMemo(
    () =>
      createTenantController({
        apiClient,
        normalizeError: normalizeApiError,
      }),
    [apiClient],
  );

  const [snapshot, setSnapshot] = useState<TenantSnapshot>(() =>
    controller.getSnapshot(),
  );

  useEffect(() => {
    const unsubscribe = controller.subscribe(setSnapshot);
    return unsubscribe;
  }, [controller]);

  useEffect(() => {
    if (authSnapshot.status === "authenticated" && authSnapshot.user) {
      void controller.discover();
    } else if (authSnapshot.status === "unauthenticated") {
      controller.clear();
    }
  }, [authSnapshot.status, authSnapshot.user?.user_id, controller]);

  const value = useMemo<TenantContextValue>(
    () => ({ snapshot, controller }),
    [snapshot, controller],
  );

  return (
    <TenantContext.Provider value={value}>{children}</TenantContext.Provider>
  );
}

export function useTenant(): TenantContextValue {
  const ctx = useContext(TenantContext);
  if (!ctx) {
    throw new Error("useTenant must be used inside TenantProvider");
  }
  return ctx;
}
