import { useCallback, useState } from "react";
import { normalizeApiError, type NormalizedApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { AlertBanner } from "../../components/common/UiPrimitives";

export interface ApiResultLike<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

export function useActionFeedback() {
  const { controller: authController } = useAuth();
  const [actionError, setActionError] = useState<NormalizedApiError | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  const setValidationError = useCallback((message: string) => {
    setActionError({ status: 422, code: "validation_error", message });
  }, []);

  const clearFeedback = useCallback(() => {
    setActionError(null);
    setActionSuccess(null);
  }, []);

  const runApiAction = useCallback(
    async <T,>({
      call,
      fallbackError,
      onSuccess,
      onFinally,
      setBusy,
    }: {
      call: () => Promise<ApiResultLike<T>>;
      fallbackError: string;
      onSuccess: (data: T) => void;
      onFinally?: () => void;
      setBusy?: (busy: boolean) => void;
    }): Promise<boolean> => {
      clearFeedback();
      setBusy?.(true);
      try {
        const res = await authController.runWithFreshToken(call);
        if (res.data !== undefined) {
          onSuccess(res.data);
          return true;
        }
        setActionError(normalizeApiError(res.error, res.response.status, fallbackError));
        return false;
      } catch {
        setActionError({
          status: 0,
          code: "network_error",
          message: "Unable to communicate with backend service.",
        });
        return false;
      } finally {
        onFinally?.();
        setBusy?.(false);
      }
    },
    [authController, clearFeedback],
  );

  const alerts = (
    <>
      {actionSuccess && (
        <AlertBanner
          tone="success"
          message={actionSuccess}
          onDismiss={() => setActionSuccess(null)}
        />
      )}
      {actionError && (
        <AlertBanner
          tone="error"
          title="Operation failed"
          error={actionError}
          onDismiss={() => setActionError(null)}
        />
      )}
    </>
  );

  return {
    actionError,
    setActionError,
    actionSuccess,
    setActionSuccess,
    setValidationError,
    clearFeedback,
    runApiAction,
    alerts,
  };
}
