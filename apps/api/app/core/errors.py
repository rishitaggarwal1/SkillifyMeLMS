"""The single error envelope used by every error response:

{"error": {"code": "<machine_code>", "message": "<human message>", "details": <any | null>}}
"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class AppError(Exception):
    """Base class for expected, client-facing errors raised from the service layer."""

    status_code: int = HTTPStatus.BAD_REQUEST
    code: str = "bad_request"
    message: str = "The request could not be processed."
    headers: Mapping[str, str] | None = None

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details
        if headers is not None:
            self.headers = headers
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = HTTPStatus.NOT_FOUND
    code = "not_found"
    message = "The requested resource was not found."


class ConflictError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "conflict"
    message = "The request conflicts with the current state of the resource."


class PreconditionRequiredError(AppError):
    """A conditional request header (e.g. `If-Match`) is required but was not sent."""

    status_code = HTTPStatus.PRECONDITION_REQUIRED
    code = "precondition_required"
    message = "This request must be conditional."


class UnprocessableError(AppError):
    """Well-formed input that breaks a business rule (e.g. a batch from another organization)."""

    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    code = "validation_error"
    message = "The request is invalid."


class AuthenticationError(AppError):
    status_code = HTTPStatus.UNAUTHORIZED
    code = "unauthenticated"
    message = "Authentication is required."
    headers = {"WWW-Authenticate": "Bearer"}  # noqa: RUF012 - read-only class default


class PermissionDeniedError(AppError):
    status_code = HTTPStatus.FORBIDDEN
    code = "permission_denied"
    message = "You do not have permission to perform this action."


class RateLimitedError(AppError):
    status_code = HTTPStatus.TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Too many requests. Try again later."


class InvalidCursorError(AppError):
    code = "invalid_cursor"
    message = "The pagination cursor is invalid."


_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthenticated",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    428: "precondition_required",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


def error_payload(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return ErrorResponse(error=ErrorBody(code=code, message=message, details=details)).model_dump(
        mode="json"
    )


def _json_error(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_payload(code, message, details),
        headers=dict(headers) if headers else None,
    )


async def _app_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101 - narrowing for the type checker
    return _json_error(
        exc.status_code, exc.code, exc.message, jsonable_encoder(exc.details), headers=exc.headers
    )


async def _http_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    code = _STATUS_CODES.get(exc.status_code, "http_error")
    try:
        default_message = HTTPStatus(exc.status_code).phrase
    except ValueError:
        default_message = "HTTP error"
    message = exc.detail if isinstance(exc.detail, str) else default_message
    details = None if isinstance(exc.detail, str) else jsonable_encoder(exc.detail)
    return _json_error(exc.status_code, code, message, details, headers=exc.headers)


async def _validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    # Echoing raw input back can leak secrets (e.g. passwords), so only location/message/type.
    details = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return _json_error(422, "validation_error", "The request is invalid.", details)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    # Unhandled exceptions are converted to a 500 envelope in RequestContextMiddleware, so the
    # response still carries X-Request-ID (Starlette's ServerErrorMiddleware sits outside it).


# OpenAPI documentation for the envelope, to attach to routers: `responses=ERROR_RESPONSES`.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    "4XX": {"model": ErrorResponse, "description": "Client error"},
    "5XX": {"model": ErrorResponse, "description": "Server error"},
}
