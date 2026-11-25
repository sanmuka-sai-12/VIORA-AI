"""HTTP transport for authentication endpoints."""

import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request, Response, Security
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field, SecretStr, ValidationError
from sqlalchemy import select

from ..config import Settings
from ..contracts import Contract, ErrorResponse
from ..db import Database
from ..http import failure
from ..security import AccessError
from .auth import AuthFailure, AuthService, TokenPair
from .models import User
from .rate_limit import PostgresRateLimiter, RateLimiter, client_bucket_key
from .schemas import AccountStatus, IdentityAttributeKind

MAX_AUTH_BODY_BYTES = 16 * 1024
CSRF_HEADER = "X-Viora-CSRF"
CSRF_VALUE = "1"
PROD_REFRESH_COOKIE = "__Host-viora-refresh"
DEV_REFRESH_COOKIE = "viora-refresh"


class LoginRequest(Contract):
    kind: IdentityAttributeKind = IdentityAttributeKind.email
    identifier: str = Field(min_length=1, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=1024)


class LogoutRequest(Contract):
    session_id: UUID | None = None


class PasswordChangeRequest(Contract):
    current_password: SecretStr = Field(min_length=1, max_length=1024)
    new_password: SecretStr = Field(min_length=12, max_length=1024)


class TokenResponse(Contract):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    session_id: UUID
    refresh_expires_at: datetime
    session_expires_at: datetime


class AuthenticatedUser(Contract):
    user_id: UUID
    display_name: str
    status: AccountStatus
    session_id: UUID


def cookie_name(config: Settings) -> str:
    if (
        config.environment == "development"
        and config.auth is not None
        and not config.auth.cookie_secure
    ):
        return DEV_REFRESH_COOKIE
    return PROD_REFRESH_COOKIE


def cookie_secure(config: Settings) -> bool:
    if config.environment == "production" or config.auth is None:
        return True
    return config.auth.cookie_secure


def _verify_origin(request: Request, config: Settings) -> None:
    origin = request.headers.get("Origin")
    if not origin or origin == "null" or origin not in config.cors_origins:
        raise AccessError(403, "untrusted_origin", "Origin not allowed")


def _verify_csrf(request: Request) -> None:
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise AccessError(403, "csrf_required", "Missing or invalid CSRF header")


async def _read_bounded_json(
    request: Request, *, required: bool
) -> dict[str, Any] | None:
    content_type = request.headers.get("Content-Type")
    if content_type is not None:
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            raise AccessError(
                415, "unsupported_media_type", "Content-Type must be application/json"
            )
    elif required:
        raise AccessError(
            415, "unsupported_media_type", "Content-Type must be application/json"
        )

    content_length = request.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_AUTH_BODY_BYTES:
                raise AccessError(413, "payload_too_large", "Request body too large")
        except ValueError:
            raise AccessError(
                422, "invalid_request", "Invalid Content-Length header"
            ) from None

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_AUTH_BODY_BYTES:
            raise AccessError(413, "payload_too_large", "Request body too large")
        chunks.append(chunk)
    raw = b"".join(chunks)

    if not raw.strip():
        if required:
            raise AccessError(422, "invalid_request", "Request body is required")
        return None

    if content_type is None:
        raise AccessError(
            415, "unsupported_media_type", "Content-Type must be application/json"
        )

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AccessError(422, "invalid_request", "Invalid JSON payload") from None

    if not isinstance(parsed, dict):
        raise AccessError(422, "invalid_request", "Expected JSON object")
    return parsed


def _set_refresh_cookie(
    response: Response, config: Settings, pair: TokenPair
) -> None:
    max_age = max(0, int((pair.refresh_expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        key=cookie_name(config),
        value=pair.refresh_token.get_secret_value(),
        max_age=max_age,
        expires=pair.refresh_expires_at,
        path="/",
        domain=None,
        secure=cookie_secure(config),
        httponly=True,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response, config: Settings) -> None:
    response.delete_cookie(
        key=cookie_name(config),
        path="/",
        domain=None,
        secure=cookie_secure(config),
        httponly=True,
        samesite="strict",
    )


def _auth_failure(
    request: Request, config: Settings, *, clear_cookie: bool
) -> JSONResponse:
    response = failure(
        request, 401, "unauthenticated", "Invalid authentication credentials"
    )
    response.headers["Cache-Control"] = "no-store"
    if clear_cookie:
        _clear_refresh_cookie(response, config)
    return response


def _token_response(
    config: Settings, pair: TokenPair
) -> JSONResponse:
    body = TokenResponse(
        access_token=pair.access_token.get_secret_value(),
        expires_in=pair.access_expires_in,
        session_id=pair.session_id,
        refresh_expires_at=pair.refresh_expires_at,
        session_expires_at=pair.session_expires_at,
    )
    response = JSONResponse(
        content=body.model_dump(mode="json"),
        status_code=200,
        headers={"Cache-Control": "no-store"},
    )
    _set_refresh_cookie(response, config, pair)
    return response


def _require_service(auth_service: AuthService | None) -> AuthService:
    if auth_service is None:
        raise AccessError(
            503, "auth_unavailable", "Authentication service is not configured"
        )
    return auth_service


def _require_bearer(
    bearer: HTTPAuthorizationCredentials | None,
) -> str:
    if bearer is None or not bearer.credentials:
        raise AuthFailure()
    return bearer.credentials


async def _enforce_rate_limit(
    request: Request,
    limiter: RateLimiter,
    scope: str,
    limit: int,
    window_seconds: int,
) -> JSONResponse | None:
    key = client_bucket_key(request.client.host if request.client else None)
    retry_after = await run_in_threadpool(
        limiter.check, scope, key, limit, window_seconds
    )
    if retry_after is not None:
        response = failure(
            request, 429, "rate_limited", "Too many authentication requests"
        )
        response.headers["Retry-After"] = str(retry_after)
        response.headers["Cache-Control"] = "no-store"
        return response
    return None


def build_auth_router(
    config: Settings,
    db: Database,
    auth_service: AuthService | None,
    rate_limiter: RateLimiter | None = None,
) -> APIRouter:
    limiter = rate_limiter or PostgresRateLimiter(db.sessions)
    router = APIRouter(
        prefix="/auth",
        tags=["auth"],
        responses={429: {"model": ErrorResponse}},
    )

    @router.post(
        "/login",
        operation_id="login",
        response_model=TokenResponse,
    )
    async def login(request: Request) -> Response:
        _verify_origin(request, config)
        _verify_csrf(request)
        payload = await _read_bounded_json(request, required=True)
        try:
            body = LoginRequest.model_validate(payload)
        except ValidationError:
            raise AccessError(
                422, "invalid_request", "Request validation failed"
            ) from None

        service = _require_service(auth_service)
        assert config.auth is not None
        limited = await _enforce_rate_limit(
            request,
            limiter,
            "login",
            config.auth.login_rate_limit,
            config.auth.login_rate_window_seconds,
        )
        if limited is not None:
            return limited

        try:
            pair = await run_in_threadpool(
                service.login, body.kind, body.identifier, body.password
            )
        except AuthFailure:
            return _auth_failure(request, config, clear_cookie=True)
        return _token_response(config, pair)

    @router.post(
        "/refresh",
        operation_id="refresh",
        response_model=TokenResponse,
        description=(
            "Rotate the HttpOnly refresh cookie and issue a new access token. "
            "Clients MUST serialize refresh requests across browser tabs and MUST NOT "
            "automatically retry a submitted refresh after an ambiguous network failure; "
            "require a fresh login instead."
        ),
    )
    async def refresh(request: Request) -> Response:
        _verify_origin(request, config)
        _verify_csrf(request)
        if request.query_params:
            raise AccessError(
                422,
                "invalid_request",
                "Refresh accepts credentials only via cookie",
            )
        payload = await _read_bounded_json(request, required=False)
        if payload:
            raise AccessError(
                422,
                "invalid_request",
                "Refresh accepts credentials only via cookie",
            )

        service = _require_service(auth_service)
        assert config.auth is not None
        limited = await _enforce_rate_limit(
            request,
            limiter,
            "refresh",
            config.auth.refresh_rate_limit,
            config.auth.refresh_rate_window_seconds,
        )
        if limited is not None:
            return limited

        raw_cookie = request.cookies.get(cookie_name(config))
        if not raw_cookie:
            return _auth_failure(request, config, clear_cookie=True)

        try:
            pair = await run_in_threadpool(service.refresh, SecretStr(raw_cookie))
        except AuthFailure:
            return _auth_failure(request, config, clear_cookie=True)
        return _token_response(config, pair)

    @router.post(
        "/logout",
        operation_id="logout",
        status_code=204,
    )
    async def logout(
        request: Request,
        bearer: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(HTTPBearer(auto_error=False)),
        ],
    ) -> Response:
        _verify_origin(request, config)
        _verify_csrf(request)
        payload = await _read_bounded_json(request, required=False)
        try:
            body = LogoutRequest.model_validate(payload or {})
        except ValidationError:
            raise AccessError(
                422, "invalid_request", "Request validation failed"
            ) from None

        service = _require_service(auth_service)
        try:
            token = _require_bearer(bearer)
            target_session = body.session_id
            if target_session is None:
                _, target_session = service.tokens.decode(token)
            await run_in_threadpool(service.revoke_session, token, target_session)
        except AuthFailure:
            return _auth_failure(request, config, clear_cookie=True)

        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        _clear_refresh_cookie(response, config)
        return response

    @router.post(
        "/logout-all",
        operation_id="logoutAll",
        status_code=204,
    )
    async def logout_all(
        request: Request,
        bearer: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(HTTPBearer(auto_error=False)),
        ],
    ) -> Response:
        _verify_origin(request, config)
        _verify_csrf(request)
        payload = await _read_bounded_json(request, required=False)
        if payload:
            raise AccessError(422, "invalid_request", "Request validation failed")

        service = _require_service(auth_service)
        try:
            token = _require_bearer(bearer)
            await run_in_threadpool(service.logout_all, token)
        except AuthFailure:
            return _auth_failure(request, config, clear_cookie=True)

        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        _clear_refresh_cookie(response, config)
        return response

    @router.get(
        "/me",
        operation_id="currentUser",
        response_model=AuthenticatedUser,
    )
    async def me(
        request: Request,
        bearer: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(HTTPBearer(auto_error=False)),
        ],
    ) -> Response:
        service = _require_service(auth_service)
        try:
            token = _require_bearer(bearer)
            actor = await run_in_threadpool(service.verify, token)
            if actor is None:
                raise AuthFailure()
            _, session_id = service.tokens.decode(token)
        except AuthFailure:
            return _auth_failure(request, config, clear_cookie=False)

        def _load_user() -> AuthenticatedUser | None:
            with db.sessions() as session:
                row = session.execute(
                    select(User.user_id, User.display_name, User.status).where(
                        User.user_id == actor.actor_id,
                        User.status == AccountStatus.active,
                    )
                ).one_or_none()
                if row is None:
                    return None
                return AuthenticatedUser(
                    user_id=row.user_id,
                    display_name=row.display_name,
                    status=row.status,
                    session_id=session_id,
                )

        user = await run_in_threadpool(_load_user)
        if user is None:
            return _auth_failure(request, config, clear_cookie=False)
        return JSONResponse(
            content=user.model_dump(mode="json"),
            status_code=200,
            headers={"Cache-Control": "no-store"},
        )

    @router.post(
        "/password/change",
        operation_id="changePassword",
        status_code=204,
    )
    async def change_password(
        request: Request,
        bearer: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(HTTPBearer(auto_error=False)),
        ],
    ) -> Response:
        _verify_origin(request, config)
        _verify_csrf(request)
        payload = await _read_bounded_json(request, required=True)
        try:
            body = PasswordChangeRequest.model_validate(payload)
        except ValidationError:
            raise AccessError(
                422, "invalid_request", "Request validation failed"
            ) from None

        service = _require_service(auth_service)
        try:
            token = _require_bearer(bearer)
            await run_in_threadpool(
                service.change_password,
                token,
                body.current_password,
                body.new_password,
            )
        except AuthFailure:
            return _auth_failure(request, config, clear_cookie=True)
        except ValueError:
            raise AccessError(
                422,
                "invalid_request",
                "Password must contain 12 to 1024 characters",
            ) from None

        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        _clear_refresh_cookie(response, config)
        return response

    return router
