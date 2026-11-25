import test from "node:test";
import assert from "node:assert/strict";
import { createApiClient, normalizeApiError } from "./client.ts";

test("createApiClient attaches credentials, CSRF header on auth mutations, and bearer token on protected calls", async () => {
  const captured: Array<{
    url: string;
    method: string;
    credentials?: RequestCredentials;
    authHeader: string | null;
    csrfHeader: string | null;
    requestId: string | null;
  }> = [];

  let token: string | null = "mem-access-token";

  const mockFetch: typeof globalThis.fetch = async (input, init) => {
    const req = input instanceof Request ? input : new Request(String(input), init);
    captured.push({
      url: req.url,
      method: req.method,
      credentials: req.credentials,
      authHeader: req.headers.get("Authorization"),
      csrfHeader: req.headers.get("X-Viora-CSRF"),
      requestId: req.headers.get("X-Request-ID"),
    });
    return new Response(JSON.stringify({ status: "ok" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  const client = createApiClient({
    baseUrl: "http://localhost:5173",
    getAuthToken: () => token,
    fetch: mockFetch,
  });

  await client.POST("/api/v1/auth/login", {
    body: { kind: "email", identifier: "user@example.edu", password: "password123456" } as never,
  });
  await client.POST("/api/v1/auth/refresh");
  await client.GET("/api/v1/institutions");
  await client.POST("/api/v1/auth/logout", {
    body: {} as never,
  });

  assert.equal(captured.length, 4);

  // Login: credentials=include, CSRF=1, no stale Bearer header
  assert.equal(captured[0].credentials, "include");
  assert.equal(captured[0].csrfHeader, "1");
  assert.equal(captured[0].authHeader, null);
  assert.ok(captured[0].requestId);

  // Refresh: credentials=include, CSRF=1, cookie-only (no Bearer header)
  assert.equal(captured[1].credentials, "include");
  assert.equal(captured[1].csrfHeader, "1");
  assert.equal(captured[1].authHeader, null);

  // Protected institution discovery: bearer token attached, no auth CSRF header
  assert.equal(captured[2].credentials, "include");
  assert.equal(captured[2].authHeader, "Bearer mem-access-token");
  assert.equal(captured[2].csrfHeader, null);

  // Logout: both CSRF header and Bearer token attached
  assert.equal(captured[3].credentials, "include");
  assert.equal(captured[3].csrfHeader, "1");
  assert.equal(captured[3].authHeader, "Bearer mem-access-token");

  token = null;
});

test("normalizeApiError extracts backend code, message, and request_id or falls back cleanly", () => {
  const structured = normalizeApiError(
    {
      code: "forbidden",
      message: "Institution role denied",
      request_id: "11111111-1111-1111-1111-111111111111",
    },
    403,
  );
  assert.deepEqual(structured, {
    status: 403,
    code: "forbidden",
    message: "Institution role denied",
    requestId: "11111111-1111-1111-1111-111111111111",
  });

  const fallback500 = normalizeApiError(undefined, 502, "Upstream unavailable");
  assert.deepEqual(fallback500, {
    status: 502,
    code: "server_error",
    message: "Upstream unavailable",
  });
});
