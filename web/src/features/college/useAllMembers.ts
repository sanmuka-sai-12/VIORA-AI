import { useCallback, useEffect, useState } from "react";
import {
  normalizeApiError,
  type MemberSummary,
  type NormalizedApiError,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";

export function useAllMembers(tenantId: string | null) {
  const { apiClient, controller: authController } = useAuth();
  const [members, setMembers] = useState<MemberSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<NormalizedApiError | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  const reload = useCallback(() => {
    setReloadTick((t) => t + 1);
  }, []);

  useEffect(() => {
    if (!tenantId) {
      setMembers([]);
      setError(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    const loadAll = async () => {
      const collected: MemberSummary[] = [];
      let cursor: string | undefined = undefined;
      for (let page = 0; page < 5; page += 1) {
        const res = await authController.runWithFreshToken(() =>
          apiClient.GET("/api/v1/tenants/{tenant_id}/memberships", {
            params: {
              path: { tenant_id: tenantId },
              query: { cursor, limit: 100 },
            },
          }),
        );
        if (cancelled) return;
        if (!res.data) {
          setError(
            normalizeApiError(
              res.error,
              res.response.status,
              "Unable to load institutional members.",
            ),
          );
          setLoading(false);
          return;
        }
        collected.push(...res.data.items);
        if (!res.data.next_cursor) {
          break;
        }
        cursor = res.data.next_cursor;
      }
      if (!cancelled) {
        setMembers(collected);
        setLoading(false);
      }
    };

    void loadAll().catch(() => {
      if (!cancelled) {
        setError({
          status: 0,
          code: "network_error",
          message: "Unable to communicate with backend service.",
        });
        setLoading(false);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [apiClient, authController, reloadTick, tenantId]);

  return { members, loading, error, reload };
}
