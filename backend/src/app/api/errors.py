"""Uniform error handling and request correlation."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.domain.jobs.state_machine import InvalidTransitionError
from app.infrastructure.logging import clear_correlation, get_logger, set_correlation
from app.orchestrator.service import StaleLeaseError, WrongStateError
from app.persistence.repositories.jobs import LeaseExpiredError, StartConflictError
from app.persistence.repositories.workers import WorkerAuthError
from app.services.storage.base import StorageError

logger = get_logger(__name__)

#: Domain error -> HTTP status. Every failure the API can produce is listed here
#: so the frontend never has to guess.
_STATUS_BY_ERROR: list[tuple[type[Exception], int, str]] = [
    (InvalidTransitionError, status.HTTP_409_CONFLICT, "illegal_state_transition"),
    (StaleLeaseError, status.HTTP_409_CONFLICT, "stale_lease"),
    (LeaseExpiredError, status.HTTP_409_CONFLICT, "lease_expired"),
    (StartConflictError, status.HTTP_409_CONFLICT, "start_conflict"),
    (WrongStateError, status.HTTP_409_CONFLICT, "wrong_state"),
    (WorkerAuthError, status.HTTP_401_UNAUTHORIZED, "worker_auth_failed"),
    (StorageError, status.HTTP_507_INSUFFICIENT_STORAGE, "storage_error"),
    (ValueError, status.HTTP_400_BAD_REQUEST, "invalid_request"),
]


def get_request_id() -> str:
    """New correlation id for a request that did not bring its own."""
    return uuid.uuid4().hex


def _error_body(request: Request, code: str, message: str, **extra: object) -> dict[str, object]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", None),
            **extra,
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    """Attach the correlation middleware and the domain error mapping."""

    @app.middleware("http")
    async def correlate(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID") or get_request_id()
        request.state.request_id = request_id
        set_correlation(request_id=request_id, path=request.url.path, method=request.method)
        try:
            response = await call_next(request)
        finally:
            clear_correlation()
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):  # type: ignore[no-untyped-def]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_body(
                request, "validation_error", "request validation failed", errors=exc.errors()
            ),
        )

    for exc_type, http_status, code in _STATUS_BY_ERROR:

        def make_handler(exc_type: type[Exception], http_status: int, code: str):  # type: ignore[no-untyped-def]
            async def handler(request: Request, exc: Exception):  # type: ignore[no-untyped-def]
                logger.info(
                    "api.domain_error",
                    code=code,
                    error=str(exc),
                    path=request.url.path,
                )
                return JSONResponse(
                    status_code=http_status,
                    content=_error_body(request, code, str(exc)),
                )

            return handler

        app.add_exception_handler(exc_type, make_handler(exc_type, http_status, code))

    @app.exception_handler(SQLAlchemyError)
    async def on_db_error(request: Request, exc: SQLAlchemyError):  # type: ignore[no-untyped-def]
        logger.error("api.database_error", error=str(exc), path=request.url.path)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_error_body(request, "database_unavailable", "database error"),
        )

    @app.exception_handler(Exception)
    async def on_unexpected(request: Request, exc: Exception):  # type: ignore[no-untyped-def]
        logger.error("api.unhandled_error", error=str(exc), path=request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body(request, "internal_error", "internal server error"),
        )


__all__ = ["get_request_id", "install_error_handlers"]
