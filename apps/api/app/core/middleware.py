"""Pure-ASGI request context middleware (no BaseHTTPMiddleware: it buffers and adds overhead).

Responsibilities:
- assign a request ID (reuse a well-formed inbound X-Request-ID, else generate a UUIDv7)
- bind it to structlog contextvars so every log line of the request carries it
- echo it on the response
- emit one access log line per request with status and duration
- convert unhandled exceptions into the standard 500 error envelope
"""

import json
import re
import time

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uuid_utils.compat import uuid7

from app.core.errors import error_payload
from app.core.logging import get_logger

REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")
_QUIET_PATHS = frozenset({"/health/live", "/health/ready"})
_FIRST_ERROR_STATUS = 400

logger = get_logger("app.access")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _extract_request_id(scope) or str(uuid7())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        scope.setdefault("state", {})["request_id"] = request_id

        start = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            logger.exception("unhandled_exception", method=scope["method"], path=scope["path"])
            if response_started:
                # Headers already sent; nothing sane to return. Let the server drop the connection.
                raise
            await _send_internal_error(send_wrapper)
        finally:
            path = scope["path"]
            if not (path in _QUIET_PATHS and status_code < _FIRST_ERROR_STATUS):
                logger.info(
                    "request",
                    method=scope["method"],
                    path=path,
                    status=status_code,
                    duration_ms=round((time.perf_counter() - start) * 1000, 2),
                )
            structlog.contextvars.clear_contextvars()


def _extract_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER.encode():
            candidate: str = value.decode("latin-1")
            return candidate if _VALID_REQUEST_ID.match(candidate) else None
    return None


async def _send_internal_error(send: Send) -> None:
    body = json.dumps(
        error_payload("internal_error", "An unexpected error occurred."), separators=(",", ":")
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 500,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
