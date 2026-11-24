import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./generated";

export interface ApiClientOptions {
  baseUrl?: string;
  getAuthToken?: () => string | null;
}

export function createApiClient(options: ApiClientOptions = {}) {
  const envApiUrl =
    typeof import.meta !== "undefined" && import.meta.env
      ? import.meta.env.VITE_API_URL
      : undefined;
  const baseUrl = options.baseUrl ?? envApiUrl ?? "";

  const authMiddleware: Middleware = {
    async onRequest({ request }) {
      // Correlation ID propagation
      if (!request.headers.get("X-Request-ID")) {
        const correlationId = crypto.randomUUID();
        request.headers.set("X-Request-ID", correlationId);
      }

      // Future auth integration hook
      const token = options.getAuthToken ? options.getAuthToken() : null;
      if (token) {
        request.headers.set("Authorization", `Bearer ${token}`);
      }

      return request;
    },
    async onResponse({ response }) {
      if (!response.ok) {
        // Centralized handling or observability points can hook here
      }
      return response;
    },
  };

  const client = createClient<paths>({ baseUrl });
  client.use(authMiddleware);
  return client;
}

export const api = createApiClient();

