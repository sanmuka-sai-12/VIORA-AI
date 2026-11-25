export interface RefreshSuccessResult<T> {
  kind: "success";
  data: T;
}

export interface RefreshUnauthorizedResult {
  kind: "unauthorized";
  message: string;
}

export interface RefreshAmbiguousResult {
  kind: "ambiguous";
  message: string;
}

export type RefreshAttemptResult<T> =
  | RefreshSuccessResult<T>
  | RefreshUnauthorizedResult
  | RefreshAmbiguousResult;

export type SessionBroadcastMessage =
  | {
      type: "session_refreshed";
      senderId: string;
      sessionId: string;
    }
  | {
      type: "session_ended";
      senderId: string;
      reason: "logout" | "expired" | "ambiguous";
      message?: string;
    };

export interface LockProvider {
  request<T>(name: string, callback: () => Promise<T>): Promise<T>;
}

export interface BroadcastChannelLike {
  postMessage(message: unknown): void;
  addEventListener(
    type: "message",
    listener: (event: MessageEvent) => void,
  ): void;
  removeEventListener(
    type: "message",
    listener: (event: MessageEvent) => void,
  ): void;
  close(): void;
}

export interface RefreshCoordinatorOptions<T> {
  lockName?: string;
  channelName?: string;
  tabId?: string;
  lockProvider?: LockProvider | null;
  channel?: BroadcastChannelLike | null;
  onRemoteSessionEnded?: (reason: "logout" | "expired" | "ambiguous", message?: string) => void;
  onRemoteSessionRefreshed?: (sessionId: string) => void;
  extractSessionId?: (data: T) => string;
}

function resolveDefaultLockProvider(): LockProvider | null {
  if (
    typeof navigator !== "undefined" &&
    navigator.locks &&
    typeof navigator.locks.request === "function"
  ) {
    return {
      request<R>(name: string, callback: () => Promise<R>): Promise<R> {
        return navigator.locks.request(name, callback);
      },
    };
  }
  return null;
}

function resolveDefaultChannel(name: string): BroadcastChannelLike | null {
  if (typeof BroadcastChannel !== "undefined") {
    return new BroadcastChannel(name);
  }
  return null;
}

/**
 * Serializes refresh attempts within a tab and across browser tabs.
 * Never retries after an ambiguous failure so rotated refresh tokens are
 * not falsely flagged as replay attacks.
 */
export class RefreshCoordinator<T> {
  private readonly lockName: string;
  private readonly tabId: string;
  private readonly lockProvider: LockProvider | null;
  private readonly channel: BroadcastChannelLike | null;
  private readonly onRemoteSessionEnded?: RefreshCoordinatorOptions<T>["onRemoteSessionEnded"];
  private readonly onRemoteSessionRefreshed?: RefreshCoordinatorOptions<T>["onRemoteSessionRefreshed"];
  private readonly extractSessionId?: (data: T) => string;

  private inFlight: Promise<RefreshAttemptResult<T>> | null = null;
  private tailQueue: Promise<unknown> = Promise.resolve();
  private readonly messageListener: ((event: MessageEvent) => void) | null;

  constructor(options: RefreshCoordinatorOptions<T> = {}) {
    this.lockName = options.lockName ?? "viora-auth-refresh";
    this.tabId =
      options.tabId ??
      (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `tab-${Math.random().toString(36).slice(2)}`);
    this.lockProvider =
      options.lockProvider !== undefined
        ? options.lockProvider
        : resolveDefaultLockProvider();
    this.channel =
      options.channel !== undefined
        ? options.channel
        : resolveDefaultChannel(options.channelName ?? "viora-auth-channel");
    this.onRemoteSessionEnded = options.onRemoteSessionEnded;
    this.onRemoteSessionRefreshed = options.onRemoteSessionRefreshed;
    this.extractSessionId = options.extractSessionId;

    if (this.channel) {
      this.messageListener = (event: MessageEvent) => {
        const msg = event.data as SessionBroadcastMessage | null | undefined;
        if (!msg || typeof msg !== "object" || msg.senderId === this.tabId) {
          return;
        }
        if (msg.type === "session_ended") {
          this.onRemoteSessionEnded?.(msg.reason, msg.message);
        } else if (msg.type === "session_refreshed") {
          this.onRemoteSessionRefreshed?.(msg.sessionId);
        }
      };
      this.channel.addEventListener("message", this.messageListener);
    } else {
      this.messageListener = null;
    }
  }

  /**
   * Executes a single refresh operation, coalescing concurrent callers in
   * the same tab and serializing across tabs via Web Locks when supported.
   * Never performs automatic retries.
   */
  run(execute: () => Promise<RefreshAttemptResult<T>>): Promise<RefreshAttemptResult<T>> {
    if (this.inFlight) {
      return this.inFlight;
    }

    const runSerialized = async (): Promise<RefreshAttemptResult<T>> => {
      if (this.lockProvider) {
        return this.lockProvider.request(this.lockName, execute);
      }
      const next = this.tailQueue.then(execute, execute);
      this.tailQueue = next.then(
        () => undefined,
        () => undefined,
      );
      return next;
    };

    const promise = runSerialized()
      .then((result) => {
        if (result.kind === "success") {
          const sid = this.extractSessionId ? this.extractSessionId(result.data) : "";
          this.broadcast({
            type: "session_refreshed",
            senderId: this.tabId,
            sessionId: sid,
          });
        } else if (result.kind === "unauthorized") {
          this.broadcast({
            type: "session_ended",
            senderId: this.tabId,
            reason: "expired",
            message: result.message,
          });
        } else {
          this.broadcast({
            type: "session_ended",
            senderId: this.tabId,
            reason: "ambiguous",
            message: result.message,
          });
        }
        return result;
      })
      .finally(() => {
        if (this.inFlight === promise) {
          this.inFlight = null;
        }
      });

    this.inFlight = promise;
    return promise;
  }

  notifySessionEnded(
    reason: "logout" | "expired" | "ambiguous",
    message?: string,
  ): void {
    this.broadcast({
      type: "session_ended",
      senderId: this.tabId,
      reason,
      message,
    });
  }

  dispose(): void {
    if (this.channel && this.messageListener) {
      this.channel.removeEventListener("message", this.messageListener);
      this.channel.close();
    }
  }

  private broadcast(message: SessionBroadcastMessage): void {
    try {
      this.channel?.postMessage(message);
    } catch {
      // BroadcastChannel delivery is best-effort; Web Locks serialize cookies.
    }
  }
}

export function createRefreshCoordinator<T>(
  options: RefreshCoordinatorOptions<T> = {},
): RefreshCoordinator<T> {
  return new RefreshCoordinator<T>(options);
}
