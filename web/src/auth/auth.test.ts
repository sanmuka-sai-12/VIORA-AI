import test from "node:test";
import assert from "node:assert/strict";
import { createApiClient, normalizeApiError, type TokenResponse } from "../api/client.ts";
import {
  RefreshCoordinator,
  type BroadcastChannelLike,
  type LockProvider,
} from "./refreshLock.ts";
import { createAuthController } from "./session.ts";
import {
  evaluateRouteAccess,
  pathnameForRoute,
  routeFromPathname,
} from "./routeGuard.ts";

class InMemoryBroadcastHub {
  private channels = new Set<FakeBroadcastChannel>();

  createChannel(): FakeBroadcastChannel {
    const ch = new FakeBroadcastChannel(this);
    this.channels.add(ch);
    return ch;
  }

  dispatch(sender: FakeBroadcastChannel, data: unknown) {
    for (const ch of this.channels) {
      if (ch !== sender && !ch.closed) {
        ch.emit(data);
      }
    }
  }

  remove(ch: FakeBroadcastChannel) {
    this.channels.delete(ch);
  }
}

class FakeBroadcastChannel implements BroadcastChannelLike {
  closed = false;
  private listeners = new Set<(event: MessageEvent) => void>();
  private hub: InMemoryBroadcastHub;

  constructor(hub: InMemoryBroadcastHub) {
    this.hub = hub;
  }

  postMessage(message: unknown): void {
    if (!this.closed) {
      this.hub.dispatch(this, message);
    }
  }

  addEventListener(
    _type: "message",
    listener: (event: MessageEvent) => void,
  ): void {
    this.listeners.add(listener);
  }

  removeEventListener(
    _type: "message",
    listener: (event: MessageEvent) => void,
  ): void {
    this.listeners.delete(listener);
  }

  emit(data: unknown): void {
    const evt = { data } as MessageEvent;
    for (const listener of this.listeners) {
      listener(evt);
    }
  }

  close(): void {
    this.closed = true;
    this.hub.remove(this);
  }
}

function createSharedLockProvider(): {
  provider: LockProvider;
  activeInsideLock: () => number;
  maxConcurrent: () => number;
} {
  let tail: Promise<unknown> = Promise.resolve();
  let active = 0;
  let maxSeen = 0;

  return {
    provider: {
      request<T>(_name: string, callback: () => Promise<T>): Promise<T> {
        const next = tail.then(async () => {
          active += 1;
          maxSeen = Math.max(maxSeen, active);
          try {
            return await callback();
          } finally {
            active -= 1;
          }
        });
        tail = next.then(
          () => undefined,
          () => undefined,
        );
        return next;
      },
    },
    activeInsideLock: () => active,
    maxConcurrent: () => maxSeen,
  };
}

test("RefreshCoordinator coalesces concurrent same-tab calls, serializes across tabs, and never retries on ambiguous errors", async () => {
  const hub = new InMemoryBroadcastHub();
  const sharedLock = createSharedLockProvider();

  let remoteEndedTabB: { reason: string; message?: string } | null = null;
  let remoteRefreshedTabB = 0;

  const tabA = new RefreshCoordinator<TokenResponse>({
    tabId: "tab-a",
    lockProvider: sharedLock.provider,
    channel: hub.createChannel(),
    extractSessionId: (d) => d.session_id,
  });

  const tabB = new RefreshCoordinator<TokenResponse>({
    tabId: "tab-b",
    lockProvider: sharedLock.provider,
    channel: hub.createChannel(),
    extractSessionId: (d) => d.session_id,
    onRemoteSessionEnded: (reason, message) => {
      remoteEndedTabB = { reason, message };
    },
    onRemoteSessionRefreshed: () => {
      remoteRefreshedTabB += 1;
    },
  });

  let callCountA = 0;
  const tokenPayload: TokenResponse = {
    access_token: "tok-a",
    token_type: "Bearer",
    expires_in: 600,
    session_id: "11111111-1111-1111-1111-111111111111",
    refresh_expires_at: "2026-12-01T00:00:00Z",
    session_expires_at: "2026-12-02T00:00:00Z",
  };

  // Concurrent calls within tabA coalesce to a single execution
  const [res1, res2] = await Promise.all([
    tabA.run(async () => {
      callCountA += 1;
      await new Promise((r) => setTimeout(r, 15));
      return { kind: "success", data: tokenPayload };
    }),
    tabA.run(async () => {
      callCountA += 1;
      return { kind: "success", data: tokenPayload };
    }),
  ]);
  assert.equal(callCountA, 1);
  assert.equal(res1.kind, "success");
  assert.equal(res2.kind, "success");
  assert.equal(remoteRefreshedTabB, 1);

  // Cross-tab concurrent refresh calls serialize via lock (never overlap)
  await Promise.all([
    tabA.run(async () => {
      await new Promise((r) => setTimeout(r, 10));
      return { kind: "success", data: tokenPayload };
    }),
    tabB.run(async () => {
      await new Promise((r) => setTimeout(r, 10));
      return { kind: "success", data: tokenPayload };
    }),
  ]);
  assert.equal(sharedLock.maxConcurrent(), 1);
  assert.equal(sharedLock.activeInsideLock(), 0);

  // Ambiguous error does NOT trigger any retry and notifies sibling tabs
  let ambiguousAttempts = 0;
  const errResult = await tabA.run(async () => {
    ambiguousAttempts += 1;
    return { kind: "ambiguous", message: "Network timeout" };
  });
  assert.equal(ambiguousAttempts, 1);
  assert.equal(errResult.kind, "ambiguous");
  assert.deepEqual(remoteEndedTabB, {
    reason: "ambiguous",
    message: "Network timeout",
  });

  tabA.dispose();
  tabB.dispose();
});

test("AuthController manages memory-only tokens, session restore, expiry on ambiguous refresh, logout, and password change", async () => {
  let memoryToken: string | null = null;
  let clearedCount = 0;
  let refreshCallCount = 0;
  let refreshBehavior: "401" | "ok" | "network_error" = "401";

  const mockFetch: typeof globalThis.fetch = async (input, init) => {
    const req = input instanceof Request ? input : new Request(String(input), init);
    const url = new URL(req.url);

    if (url.pathname === "/api/v1/auth/refresh") {
      refreshCallCount += 1;
      if (refreshBehavior === "network_error") {
        throw new TypeError("Failed to fetch");
      }
      if (refreshBehavior === "401") {
        return new Response(
          JSON.stringify({
            code: "unauthorized",
            message: "Refresh cookie missing",
            request_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(
        JSON.stringify({
          access_token: "refreshed-token",
          token_type: "Bearer",
          expires_in: 900,
          session_id: "22222222-2222-2222-2222-222222222222",
          refresh_expires_at: "2026-12-01T00:00:00Z",
          session_expires_at: "2026-12-02T00:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }

    if (url.pathname === "/api/v1/auth/login") {
      return new Response(
        JSON.stringify({
          access_token: "initial-login-token",
          token_type: "Bearer",
          expires_in: 900,
          session_id: "11111111-1111-1111-1111-111111111111",
          refresh_expires_at: "2026-12-01T00:00:00Z",
          session_expires_at: "2026-12-02T00:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }

    if (url.pathname === "/api/v1/auth/me") {
      return new Response(
        JSON.stringify({
          user_id: "33333333-3333-3333-3333-333333333333",
          display_name: "Alice Owner",
          status: "active",
          session_id:
            req.headers.get("Authorization") === "Bearer refreshed-token"
              ? "22222222-2222-2222-2222-222222222222"
              : "11111111-1111-1111-1111-111111111111",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }

    if (url.pathname === "/api/v1/institutions") {
      if (req.headers.get("Authorization") === "Bearer initial-login-token") {
        return new Response(
          JSON.stringify({
            code: "unauthorized",
            message: "Expired access token",
            request_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }

    if (
      url.pathname === "/api/v1/auth/password/change" ||
      url.pathname === "/api/v1/auth/logout"
    ) {
      return new Response(null, { status: 204 });
    }

    return new Response(null, { status: 404 });
  };

  const apiClient = createApiClient({
    baseUrl: "http://localhost:5173",
    getAuthToken: () => memoryToken,
    fetch: mockFetch,
  });

  const coordinator = new RefreshCoordinator<TokenResponse>({
    lockProvider: null,
    channel: null,
    extractSessionId: (d) => d.session_id,
  });

  const controller = createAuthController({
    apiClient,
    refreshCoordinator: coordinator,
    normalizeError: normalizeApiError,
    setToken: (t) => {
      memoryToken = t;
    },
    getToken: () => memoryToken,
    onSessionCleared: () => {
      clearedCount += 1;
    },
  });

  // 1. Initial restore without cookie cleanly settles on unauthenticated without scary error
  const restored = await controller.restoreSession();
  assert.equal(restored, false);
  assert.equal(controller.getSnapshot().status, "unauthenticated");
  assert.equal(controller.getSnapshot().expiredReason, null);
  assert.equal(memoryToken, null);

  // 2. Login stores token in memory closure and loads user profile
  const loginRes = await controller.login({
    identifier: "alice@northbridge.edu",
    password: "ValidPassword123!",
  });
  assert.equal(loginRes.ok, true);
  assert.equal(memoryToken, "initial-login-token");
  assert.equal(controller.getSnapshot().status, "authenticated");
  assert.equal(controller.getSnapshot().user?.display_name, "Alice Owner");

  // 3. runWithFreshToken catches 401 on expired access token, refreshes once, and retries resource call
  refreshBehavior = "ok";
  const countBeforeRefresh = refreshCallCount;
  const retriedRes = await controller.runWithFreshToken(() =>
    apiClient.GET("/api/v1/institutions"),
  );
  assert.equal(retriedRes.response.status, 200);
  assert.equal(refreshCallCount, countBeforeRefresh + 1);
  assert.equal(memoryToken, "refreshed-token");

  // 4. Ambiguous refresh network failure does NOT retry refresh and clears session cleanly
  refreshBehavior = "network_error";
  const beforeAmbiguous = refreshCallCount;
  const ambiguousResult = await controller.refreshSession({ markExpiredOnFailure: true });
  assert.equal(ambiguousResult.kind, "ambiguous");
  assert.equal(refreshCallCount, beforeAmbiguous + 1);
  assert.equal(memoryToken, null);
  assert.equal(controller.getSnapshot().status, "unauthenticated");
  assert.ok(controller.getSnapshot().expiredReason?.includes("Please log in again"));

  // 5. Re-login + password change revokes sessions & clears local memory token
  await controller.login({
    identifier: "alice@northbridge.edu",
    password: "ValidPassword123!",
  });
  assert.equal(controller.getSnapshot().status, "authenticated");

  const pwRes = await controller.changePassword(
    "ValidPassword123!",
    "NewStrongPassword456!",
  );
  assert.equal(pwRes.ok, true);
  assert.equal(memoryToken, null);
  assert.equal(controller.getSnapshot().status, "unauthenticated");
  assert.ok(clearedCount >= 2);

  coordinator.dispose();
});

test("Route guard enforces authentication and institution selection without clobbering deep links during load", () => {
  assert.equal(
    evaluateRouteAccess({
      authStatus: "initializing",
      requestedRoute: "academics-departments",
      hasSelectedInstitution: false,
    }).reason,
    "initializing",
  );

  assert.equal(
    evaluateRouteAccess({
      authStatus: "unauthenticated",
      requestedRoute: "memberships",
      hasSelectedInstitution: false,
    }).effectiveRoute,
    "login",
  );

  assert.equal(
    evaluateRouteAccess({
      authStatus: "unauthenticated",
      requestedRoute: "health",
      hasSelectedInstitution: false,
    }).effectiveRoute,
    "health",
  );

  // Authenticated deep link while tenant discovery is still running waits without redirecting
  assert.equal(
    evaluateRouteAccess({
      authStatus: "authenticated",
      requestedRoute: "academics-departments",
      hasSelectedInstitution: false,
      tenantStatus: "loading",
    }).reason,
    "initializing",
  );

  // Authenticated once tenant discovery is ready but 0 or unselected institution -> redirects to institution view
  assert.equal(
    evaluateRouteAccess({
      authStatus: "authenticated",
      requestedRoute: "academics-departments",
      hasSelectedInstitution: false,
      tenantStatus: "ready",
    }).effectiveRoute,
    "institution",
  );

  // Selected institution -> permits scoped routes
  assert.equal(
    evaluateRouteAccess({
      authStatus: "authenticated",
      requestedRoute: "academics-offerings",
      hasSelectedInstitution: true,
      tenantStatus: "ready",
    }).effectiveRoute,
    "academics-offerings",
  );

  assert.equal(routeFromPathname("/academics/offerings/"), "academics-offerings");
  assert.equal(pathnameForRoute("memberships"), "/memberships");
});
