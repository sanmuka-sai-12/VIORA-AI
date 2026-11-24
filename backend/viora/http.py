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
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else {}
    return JSONResponse(
        body.model_dump(mode="json"), status_code=status, headers=headers
    )


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
                MutableHeaders(scope=message)["X-Request-ID"] = str(rid)
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
