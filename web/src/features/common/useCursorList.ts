import { useCallback, useEffect, useState } from "react";
import { normalizeApiError, type NormalizedApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import {
  advanceCursorPage,
  createInitialPageState,
  rewindCursorPage,
  type CursorPageState,
} from "../../tenant/validation";

export interface CursorPageResponse<T> {
  items: T[];
  next_cursor?: string | null;
}

export function useCursorList<T>(
  tenantId: string | null,
  fetcher: (params: {
    cursor?: string;
    limit: number;
  }) => Promise<{
    data?: CursorPageResponse<T>;
    error?: unknown;
    response: Response;
  }>,
) {
  const { controller: authController } = useAuth();
  const [limit, setLimit] = useState<number>(25);
  const [page, setPage] = useState<CursorPageState>(createInitialPageState);
  const [items, setItems] = useState<T[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<NormalizedApiError | null>(null);
  const [reloadNonce, setReloadNonce] = useState<number>(0);

  const resetToFirstPage = useCallback(() => {
    setPage(createInitialPageState());
  }, []);

  const reload = useCallback(() => {
    setReloadNonce((n) => n + 1);
  }, []);

  const handleLimitChange = useCallback((nextLimit: number) => {
    setLimit(nextLimit);
    setPage(createInitialPageState());
  }, []);

  const goNext = useCallback(() => {
    if (!nextCursor) {
      return;
    }
    setPage((prev) => advanceCursorPage(prev, nextCursor));
  }, [nextCursor]);

  const goPrevious = useCallback(() => {
    setPage((prev) => rewindCursorPage(prev));
  }, []);

  useEffect(() => {
    setPage(createInitialPageState());
    setItems([]);
    setNextCursor(null);
  }, [tenantId]);

  useEffect(() => {
    if (!tenantId) {
      setLoading(false);
      setItems([]);
      setNextCursor(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    void authController
      .runWithFreshToken(() =>
        fetcher({
          cursor: page.currentCursor ?? undefined,
          limit,
        }),
      )
      .then((res) => {
        if (cancelled) {
          return;
        }
        if (res.data) {
          setItems(res.data.items);
          setNextCursor(res.data.next_cursor ?? null);
          setError(null);
        } else {
          setItems([]);
          setNextCursor(null);
          setError(
            normalizeApiError(
              res.error,
              res.response.status,
              "Failed to load records.",
            ),
          );
        }
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        setItems([]);
        setNextCursor(null);
        setError({
          status: 0,
          code: "network_error",
          message: "Unable to communicate with backend service.",
        });
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [authController, fetcher, limit, page.currentCursor, reloadNonce, tenantId]);

  return {
    items,
    loading,
    error,
    limit,
    hasPrevious: page.history.length > 0,
    hasNext: nextCursor !== null,
    goNext,
    goPrevious,
    setLimit: handleLimitChange,
    resetToFirstPage,
    reload,
  };
}
