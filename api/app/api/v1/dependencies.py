"""
Auth + error handling for the /v1 Audit API.

Auth is per-environment API-key auth: one or more keys configured via the
``AUDIT_API_KEYS`` secret (comma-separated, each entry a bare key or
``label:key``), presented as ``X-API-Key: <key>``. Multiple keys are valid at
once so keys can be rotated with zero downtime (add new, migrate callers, drop
old) purely by updating the secret — no code change, no rebuild. The secret is
read fresh from the environment on every request, so a rotation takes effect
immediately.

Errors follow the /v1 contract: standard HTTP status codes with a body of
``{"error": {"code", "message", "details"}}``. These handlers are scoped to the
/v1 path so the existing admin/JWT routes keep FastAPI's default error format.
"""
import hmac
import os
import re

import structlog
from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings

logger = structlog.get_logger()

_API_KEY_HEADER = "X-API-Key"
# A label is a short human tag before the first colon; anything else is treated
# as a bare key (so keys that happen to contain ':' are not mis-split).
_LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _parse_api_keys(raw: str) -> dict[str, str]:
    """Parse an ``AUDIT_API_KEYS`` value into ``{key: label}``.

    Entries are comma-separated. Each entry is either ``label:key`` (label is a
    short ``[A-Za-z0-9_.-]+`` tag) or a bare ``key``. Blank entries are ignored.
    Later entries win if a key is repeated. Labels are for logging only.
    """
    keys: dict[str, str] = {}
    for i, entry in enumerate(raw.split(",")):
        entry = entry.strip()
        if not entry:
            continue
        prefix, sep, rest = entry.partition(":")
        if sep and _LABEL_RE.match(prefix) and rest:
            label, key = prefix, rest.strip()
        else:
            label, key = f"key{i + 1}", entry
        if key:
            keys[key] = label
    return keys


def _configured_api_keys() -> dict[str, str]:
    """Current valid API keys as ``{key: label}``, read fresh on every call.

    Prefers the live ``AUDIT_API_KEYS`` environment variable so a rotated secret
    takes effect without a rebuild; falls back to the parsed settings value
    (e.g. from ``.env``) when the variable is unset.
    """
    raw = os.environ.get("AUDIT_API_KEYS")
    if raw is None:
        raw = settings.audit_api_keys
    return _parse_api_keys(raw or "")


class V1Error(Exception):
    """A /v1 error rendered as {"error": {code, message, details}}."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict | list | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


def _error_body(code: str, message: str, details=None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


# ── Auth dependency ───────────────────────────────────────────────────────────

async def require_api_key(request: Request) -> str:
    """Validate the X-API-Key header against the configured per-env keys.

    Returns the matched key's label on success (also stashed on
    ``request.state.api_key_label`` for logging). Raises V1Error(401) if the
    header is missing or matches no configured key. If no keys are configured
    for this environment, all /v1 requests are rejected (fail closed).
    """
    keys = _configured_api_keys()
    if not keys:
        logger.warning("v1_api_key_not_configured")
        raise V1Error(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="unauthorized",
            message="Audit API is not configured for this environment.",
        )

    presented = request.headers.get(_API_KEY_HEADER, "")
    if presented:
        # Constant-time comparison against each key to avoid leaking a key via
        # timing. The set of keys / labels is not secret, so a match may return.
        for key, label in keys.items():
            if hmac.compare_digest(presented, key):
                request.state.api_key_label = label
                logger.info("v1_auth_ok", key_label=label)
                return label

    raise V1Error(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="unauthorized",
        message=f"Missing or invalid {_API_KEY_HEADER} header.",
    )


# ── Exception handlers (registered on the app, path-scoped to /v1) ────────────

async def v1_error_handler(request: Request, exc: V1Error) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.code, exc.message, exc.details),
    )


async def v1_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Reformat request-validation errors for /v1 only.

    Non-/v1 routes fall back to FastAPI's default 422 body so existing admin
    behaviour is unchanged.
    """
    if not request.url.path.startswith("/v1"):
        # Mirror FastAPI's default response for everything else.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": exc.errors()},
        )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_body(
            "validation_error",
            "Request body failed validation.",
            details=[
                {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
                for e in exc.errors()
            ],
        ),
    )


# HTTP status → stable error code used in the /v1 envelope. Falls back to
# "http_error" for anything unlisted.
_STATUS_CODE_TO_ERROR: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "bad_request",
    status.HTTP_401_UNAUTHORIZED: "unauthorized",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_422_UNPROCESSABLE_ENTITY: "validation_error",
    status.HTTP_429_TOO_MANY_REQUESTS: "rate_limited",
}


async def v1_http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Render HTTPExceptions raised under /v1 as the standard error envelope.

    Non-/v1 routes fall back to FastAPI's default ``{"detail": ...}`` body so
    existing admin/JWT behaviour is unchanged.
    """
    if not request.url.path.startswith("/v1"):
        from fastapi.exception_handlers import http_exception_handler

        return await http_exception_handler(request, exc)

    code = _STATUS_CODE_TO_ERROR.get(exc.status_code, "http_error")
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(code, str(exc.detail)),
        headers=getattr(exc, "headers", None),
    )


async def v1_unhandled_exception_handler(request: Request, exc: Exception):
    """Last-resort 500 handler: envelope for /v1, default behaviour elsewhere.

    Internal details are logged (with traceback) but never returned to the
    caller — the body is a generic message so we don't leak internals. For
    non-/v1 paths this reproduces Starlette's default plain-text 500 response so
    admin/JWT behaviour is unchanged. (Starlette's ServerErrorMiddleware still
    re-raises after the response is sent, so the traceback is logged as usual.)
    """
    if not request.url.path.startswith("/v1"):
        from starlette.responses import PlainTextResponse

        return PlainTextResponse("Internal Server Error", status_code=500)

    logger.exception("v1_unhandled_error", path=request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body("internal_error", "An internal error occurred."),
    )


def register_v1_error_handlers(app) -> None:
    """Attach the /v1 error handlers to a FastAPI app (additive).

    Covers the full envelope surface: explicit V1Errors, request validation
    (422), any HTTPException (401/403/404/405/409/…), and unhandled 500s — all
    path-scoped to /v1 so admin/JWT routes keep FastAPI's default error format.
    """
    app.add_exception_handler(V1Error, v1_error_handler)
    app.add_exception_handler(RequestValidationError, v1_validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, v1_http_exception_handler)
    app.add_exception_handler(Exception, v1_unhandled_exception_handler)
