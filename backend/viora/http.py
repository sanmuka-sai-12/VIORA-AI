import json
import logging
from time import monotonic
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .contracts import ErrorResponse
from .security import AccessError

logger = logging.getLogger("uvicorn.error")


def failure(request: Request, status: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(
        code=code, message=message, request_id=request.state.request_id
    )
    headers: dict[str, str] = {}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if request.url.path.startswith("/api/v1/auth"):
        headers["Cache-Control"] = "no-store"
    return JSONResponse(
        body.model_dump(mode="json"), status_code=status, headers=headers
    )


class _MultipartLimitExceeded(Exception):
    pass


class MultipartLimitMiddleware:
    """Reject oversized multipart requests before Starlette buffers parts to memory/disk."""

    def __init__(self, app: ASGIApp, *, max_multipart_bytes: int) -> None:
        self.app = app
        self.max_multipart_bytes = max_multipart_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        content_type = headers.get("content-type", "").lower()
        if not content_type.startswith("multipart/form-data"):
            await self.app(scope, receive, send)
            return

        content_length_raw = headers.get("content-length")
        if content_length_raw is not None:
            try:
                content_length = int(content_length_raw)
            except ValueError:
                await failure(
                    Request(scope),
                    400,
                    "invalid_content_length",
                    "Invalid Content-Length header",
                )(scope, receive, send)
                return
            if content_length < 0 or content_length > self.max_multipart_bytes:
                await failure(
                    Request(scope),
                    413,
                    "payload_too_large",
                    f"Multipart request exceeds maximum byte limit of {self.max_multipart_bytes}",
                )(scope, receive, send)
                return

        received = 0
        response_started = False

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_multipart_bytes:
                    raise _MultipartLimitExceeded()
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, guarded_send)
        except _MultipartLimitExceeded:
            if response_started:
                raise
            await failure(
                Request(scope),
                413,
                "payload_too_large",
                f"Multipart request exceeds maximum byte limit of {self.max_multipart_bytes}",
            )(scope, receive, send)


class Correlation:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            rid = UUID(Headers(scope=scope).get("X-Request-ID", ""))
        except ValueError:
            rid = uuid4()
        scope.setdefault("state", {})["request_id"] = rid
        start = monotonic()
        status = 500
        started = False

        async def response(message: Message) -> None:
            nonlocal status, started
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = str(rid)
                if scope["path"].startswith("/api/v1/auth"):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        try:
            await self.app(scope, receive, response)
        except Exception:
            logger.exception(
                json.dumps({"event": "request_failed", "request_id": str(rid)})
            )
            if started:
                raise
            await failure(
                Request(scope), 500, "internal_error", "Internal server error"
            )(scope, receive, response)
        finally:
            logger.info(
                json.dumps(
                    {
                        "event": "request",
                        "method": scope["method"],
                        "request_id": str(rid),
                        "status": status,
                        "duration_ms": round((monotonic() - start) * 1000),
                    }
                )
            )


def configure_errors(app: FastAPI) -> None:
    @app.exception_handler(AccessError)
    async def access_error(request: Request, exc: AccessError) -> Response:
        return failure(request, exc.status, exc.code, str(exc))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request, exc: RequestValidationError
    ) -> Response:
        return failure(request, 422, "invalid_request", "Request validation failed")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        result = failure(request, exc.status_code, "http_error", str(exc.detail))
        if exc.headers:
            result.headers.update(exc.headers)
        return result
