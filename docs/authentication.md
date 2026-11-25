# Step 4.2A: authentication core and HTTP handoff

## Composition and public contract

Import `AuthService`, `AuthSettings`, `AuthFailure`, `TokenPair` and `IdentityAttributeKind` from `viora.identity.public`. Construct one service per application using `AuthService(database.sessions, settings.auth)` after explicitly rejecting `settings.auth is None`. `Settings.load()` reads the `AUTH_*` variables in `.env.example`; no signing key is generated or defaulted. Use at least 32 cryptographically random bytes, provisioned through secret management. HS256 is pinned; changing keys invalidates existing access tokens. Keep the key identical across workers.

```python
login(kind: IdentityAttributeKind, identifier: str, password: SecretStr) -> TokenPair
refresh(refresh_token: SecretStr) -> TokenPair
verify(bearer: str) -> Actor | None
revoke_session(bearer: str, session_id: UUID) -> None
logout_all(bearer: str) -> None
change_password(bearer: str, current_password: SecretStr, new_password: SecretStr) -> None
```

`TokenPair` contains secret-wrapped access/refresh tokens, `session_id`, `access_expires_in`, `refresh_expires_at` and `session_expires_at`. Explicitly unwrap only where writing the access response or refresh cookie. Never serialize this contract directly as an HTTP response. `verify` implements the existing `security.Authentication` port. Authentication failures return `None` there and raise the same `AuthFailure` elsewhere; database/signing failures propagate and must become generic server errors, never authenticated results. New passwords require 12–1024 characters; map that validation failure to an appropriate input error. Existing verified passwords can be rehashed without applying the new-password minimum. No password trimming or normalization occurs. Email identifiers use the existing case-insensitive contract.

## Security invariants and transaction ownership

- Argon2id only, through existing pwdlib; absent/malformed credentials perform dummy verification. Account absence, bad password, disabled/pending accounts and missing credentials share one public authentication error. This reduces enumeration signals; it is not a claim of constant-time database behavior.
- Access tokens default to 15 minutes. PyJWT validates fixed HS256, signature, issuer, exact audience, expiry and issuance time; required subject/session claims must parse as UUIDs. Roles and tenant permissions are never authoritative claims. Every access verification queries the current account and session, including idle and absolute deadlines.
- Each login creates one session family. Refresh secrets contain 256 random bits (43 unpadded URL-safe characters); only SHA-256 hashes persist. Seven-day sliding refresh lifetime is capped by a configurable, explicit 30-day absolute lifetime. Rotation cannot extend that absolute deadline.
- Consumed generations remain stored until their family expires. Reusing any consumed generation revokes the family, including all descendant access and refresh tokens. Other login families are unaffected. Do not delete consumed hashes while their family remains usable.
- Service methods create independent PostgreSQL READ COMMITTED transactions. Construction rejects caller-bound connections and non-PostgreSQL engines. All mutations lock the account before its session using `FOR UPDATE`; logout-all and password change serialize against login/refresh. Account-wide locking is intentionally conservative and serializes simultaneous authentication operations for one account.
- Refresh locates the family without loading mutable token state, locks the account/family, then reloads the generation. Exactly one concurrent refresh can rotate; the second is replay and revokes the winner's family. There is no retry grace window. Clients must coordinate refresh across tabs and never automatically retry a submitted refresh after an ambiguous network failure; require login again.
- Replay revocation commits **before** `AuthFailure` is raised. Do not introduce a request transaction around this service. Successful tokens are returned only after commit. Password updates and all-session revocation commit or roll back together. Password change revokes the current session too and requires a fresh login.
- Verification reflects committed database state at its query. It does not retroactively cancel an already authorized in-flight operation; sensitive business operations retain their own authorization checks.

The explicit resource limits are a 4096-character access token, a 43-character refresh token, and a 1024-character password. HTTP body limits and rate limiting remain transport responsibilities. Account-wide serialization is a deliberate throughput tradeoff, not tenant authorization.

## Required browser transport contract (HTTP implementation remains separate)

1. Return only the access token, token type `Bearer`, access lifetime and suitable nonsecret session metadata in JSON. Keep access tokens in browser memory only; never localStorage, sessionStorage, URLs or cookies. Send access tokens in `Authorization: Bearer ...`.
2. Deliver refresh tokens exclusively through a host-only `__Host-viora-refresh` cookie: `HttpOnly; Secure; SameSite=Strict; Path=/`, with no Domain. Production must use HTTPS and Secure. Set Max-Age/Expires to the returned refresh deadline, capped by the absolute deadline. Local plain-HTTP development may use a separate unprefixed name with Secure disabled only under explicit development configuration. Never weaken the production cookie.
3. Login, refresh, logout, logout-all and password changes use POST (never state-changing GET). Rotate the refresh cookie only after successful commit. Clear it on logout, password change and authentication rejection using the same name/path attributes. Do not clear it merely because of a server/database failure.
4. Require an exact allowlisted Origin for every browser authentication mutation, including login; reject missing, null and untrusted origins. Use an explicit same-origin or same-site deployment. Require a custom CSRF header (for example `X-Viora-CSRF: 1`) and JSON request content type; disallow simple form requests. The custom header is a preflight barrier, not a secret. Configure credentialed CORS only for exact trusted origins, methods and headers; never reflect arbitrary origins or allow wildcard origins. Validate Origin server-side even when CORS is enabled. SameSite is defense in depth, not the sole CSRF control. Refresh accepts credentials only from its cookie, never JSON/query parameters. Cross-site deployments need a separately reviewed SameSite=None/CSRF design.
5. Apply `Cache-Control: no-store` to all auth responses. Never log passwords, bearer tokens, refresh tokens, signing keys, cookie headers or token response bodies. Use the identical 401 response for `AuthFailure`; preserve independent revocation commits when mapping that exception. Apply rate limits to login and refresh with no account-existence disclosure.
6. Wire access verification into the existing `Access` dependency; tenant membership and authorization remain separate. Wire logout to `revoke_session` using a verified current session ID, or the owned session ID requested by the client. Derive identity through the service; do not trust client-supplied actor IDs. Execute synchronous service calls in a worker thread when using async endpoints.
7. Browser clients MUST serialize `/api/v1/auth/refresh` requests across all open tabs (for example with a Web Locks / BroadcastChannel single-flight coordinator) and MUST NOT automatically retry a submitted refresh after an ambiguous network failure (timeout, connection reset, or unknown response state); require a fresh login instead, because refresh rotation is strictly single-use with no grace window.

## Verification and Gravitas scope

Policy: compact planning, dependency-level context, impact verification, no delegation. Gravitas applies only to refresh concurrency and security invariants. Acceptance evidence is `test_auth.py`, `test_auth_postgres.py` and the migration round-trip test. The PostgreSQL marker includes the new tests in the existing mandatory CI job; unavailable local PostgreSQL must be reported as unverified, never as a concurrency pass.

Run `pytest`, `ruff check .` and `alembic upgrade head --sql`. For live verification set `VIORA_TEST_DATABASE_URL` to a dedicated test database and run the mandatory CI commands (`alembic upgrade head`, `pytest -m postgres -v`). Auth integration tests create and drop an isolated schema, use real committed transactions and separate connections, and cover replay durability, simultaneous refresh, expiry, account status, revocation, password changes and rollback/commit failure injection.
