"""API dependencies: principal resolution, worker auth, RBAC guards, services.

Authentication uses an httpOnly cookie for the browser UI and a bearer token for
API clients; workers use a distinct ``Authorization: Worker <credential>``
scheme so a stolen user token cannot drive a worker.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.config import Settings
from app.infrastructure.security import constant_time_equals
from app.persistence.database import get_db, session_scope
from app.persistence.models_identity import Worker
from app.persistence.repositories.users import (
    AuthenticationError,
    PermissionDeniedError,
    Principal,
    UserRepository,
)
from app.persistence.repositories.workers import WorkerAuthError, WorkerRepository
from app.scheduler.service import SchedulerService
from app.services.registry import ModelRegistry, get_registry
from app.services.storage import build_storage_backend
from app.services.storage.base import StorageBackend
from worker.gpu.registry import build_gpu_backend

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_app_settings(request: Request) -> Settings:
    """The settings this app instance was built with.

    Not ``get_settings()``: that reads the process environment, which ignores the
    ``create_app(settings=...)`` override and any per-test configuration. A
    dependency on the app's own state keeps one process able to serve several
    configurations.
    """
    return request.app.state.settings


AppSettings = Annotated[Settings, Depends(get_app_settings)]

_registry: ModelRegistry = get_registry()
_storage: StorageBackend | None = None


def get_model_registry() -> ModelRegistry:
    return _registry


def get_storage(settings: AppSettings) -> StorageBackend:
    """Process-wide storage backend."""
    global _storage
    if _storage is None:
        _storage = build_storage_backend(
            settings.storage_backend, root=settings.storage_root, bucket=settings.storage_bucket
        )
    return _storage


StorageDep = Annotated[StorageBackend, Depends(get_storage)]
RegistryDep = Annotated[ModelRegistry, Depends(get_model_registry)]


async def get_current_principal(
    request: Request,
    settings: AppSettings,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Resolve the caller from a session cookie or a bearer API token.

    The cookie name is configuration, never a literal, and how the caller
    authenticated is recorded on ``request.state`` so :func:`require_csrf` can
    demand a CSRF proof from cookie-authenticated browsers only.

    The session is opened and closed *inside* this dependency rather than being
    declared as a request-scoped ``DbSession``. A request-scoped session is
    held open until the whole response is produced, so on a long-lived SSE
    stream every subscriber would pin a pooled connection for the life of the
    connection and exhaust the pool after a handful of dashboards. Endpoints
    that only need the identity (streams) therefore never hold one open.
    """
    via_cookie = False
    token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    else:
        token = request.cookies.get(settings.session_cookie_name) or None
        via_cookie = token is not None

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        async with session_scope() as lookup_session:
            repo = UserRepository(lookup_session)
            principal = await (
                repo.resolve_session(token) if via_cookie else repo.resolve_api_token(token)
            )
            await lookup_session.commit()
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    request.state.user_id = principal.user.id
    request.state.auth_via = principal.via
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]

#: Methods that may not be driven by ambient cookie authority alone.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"


async def verify_csrf(
    request: Request,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
) -> None:
    """Double-submit CSRF check for cookie-authenticated requests.

    A bearer token is not sent ambiently by the browser, so it is not
    CSRF-able and is exempt. A cookie-authenticated request must echo the
    non-httpOnly ``csrf_token`` cookie in the ``X-CSRF-Token`` header, which a
    cross-origin attacker cannot read or set.
    """
    if request.method.upper() not in UNSAFE_METHODS:
        return
    if getattr(request.state, "auth_via", None) != "session":
        return
    cookie_value = request.cookies.get(CSRF_COOKIE)
    if not csrf_header or not cookie_value or not constant_time_equals(csrf_header, cookie_value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing or invalid; send the csrf_token cookie in X-CSRF-Token",
        )


CsrfChecked = Annotated[None, Depends(verify_csrf)]


def require_permissions(*codes: str):
    """Dependency factory enforcing RBAC permission codes.

    Returns an ``Annotated`` type, so it is used directly in the signature::

        async def list_jobs(principal: require_permissions("job:read"), ...)

    Building the ``Annotated`` alias (rather than a ``Depends(...)`` default)
    keeps FastAPI from rejecting a parameter that carries both an
    ``Annotated`` dependency and an explicit default.
    """

    async def _guard(principal: CurrentPrincipal) -> Principal:
        try:
            principal.require(*codes)
        except PermissionDeniedError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return principal

    _guard.__name__ = "require_" + "_and_".join(code.replace(":", "_") for code in codes)
    return Annotated[Principal, Depends(_guard)]


async def get_current_worker(
    session: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> Worker:
    """Authenticate a worker agent via ``Authorization: Worker <credential>``."""
    scheme, _, credential = (authorization or "").partition(" ")
    if scheme.lower() != "worker" or not credential.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="worker credential required (Authorization: Worker <credential>)",
        )
    # partition(), not [8:]: the scheme length is easy to get wrong and a
    # silently truncated credential fails as "invalid credential" much later.
    credential = credential.strip()
    try:
        return await WorkerRepository(session).authenticate(credential)
    except WorkerAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


CurrentWorker = Annotated[Worker, Depends(get_current_worker)]


def get_scheduler(session: DbSession, settings: AppSettings) -> SchedulerService:
    """A SchedulerService bound to the API's own session.

    Only ever used for Scheduler-owned writes that an admin explicitly triggers
    (device quarantine, forced release). The GPU backend is constructed because
    the service requires one, not because the API allocates GPUs.
    """
    backend = build_gpu_backend(settings.gpu_backend)
    return SchedulerService(session, backend, settings)


SchedulerDep = Annotated[SchedulerService, Depends(get_scheduler)]


async def get_session() -> AsyncIterator[AsyncSession]:  # pragma: no cover - alias
    async for session in get_db():
        yield session


def parse_uuid(value: str, *, field: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} must be a UUID",
        ) from exc


__all__ = [
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "AppSettings",
    "CsrfChecked",
    "CurrentPrincipal",
    "CurrentWorker",
    "DbSession",
    "RegistryDep",
    "SchedulerDep",
    "StorageDep",
    "get_model_registry",
    "get_scheduler",
    "get_storage",
    "parse_uuid",
    "require_permissions",
    "verify_csrf",
]
