"""Authentication, user administration and API tokens (``/api/v1/auth``)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.deps import (
    CSRF_COOKIE,
    CSRF_HEADER,
    AppSettings,
    CsrfChecked,
    CurrentPrincipal,
    DbSession,
    require_permissions,
)
from app.api.v1.schemas import (
    ApiTokenCreate,
    ApiTokenOut,
    LoginRequest,
    LoginResponse,
    UserCreate,
    UserOut,
)
from app.domain.enums import PermissionCode, RoleCode
from app.infrastructure.logging import get_logger
from app.infrastructure.security import generate_csrf_token
from app.persistence.repositories.users import AuthenticationError, UserRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    session: DbSession,
    settings: AppSettings,
) -> LoginResponse:
    """Create a session and set the httpOnly cookie.

    Rate limiting is expected at the reverse proxy in front of the API; the
    login attempt is still audit-logged so brute force is visible.
    """
    repo = UserRepository(session)
    try:
        user = await repo.authenticate(payload.username, payload.password)
    except AuthenticationError as exc:
        # Deliberately does not distinguish "no such user" from "wrong password".
        logger.warning("auth.login_failed", username=payload.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    if not user.is_active:
        logger.warning("auth.login_inactive", username=payload.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="account disabled")
    client_ip = request.client.host if request.client else None
    issued = await repo.create_session(
        user,
        ttl_hours=max(1, settings.access_token_ttl_minutes // 60),
        user_agent=request.headers.get("user-agent"),
        ip=client_ip,
    )
    csrf = generate_csrf_token()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=issued.token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.access_token_ttl_minutes * 60,
        path="/",
    )
    # CSRF token is readable by JS: it is not a credential on its own, it only
    # proves the request came from our own UI.
    response.set_cookie(
        key=CSRF_COOKIE,
        value=csrf,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.access_token_ttl_minutes * 60,
        path="/",
    )
    # Commit before returning. FastAPI runs ``yield``-dependency teardown after
    # the response is sent, so relying on it left the session row uncommitted
    # while the browser was already issuing its first /auth/me, which then
    # reported "unknown session" and bounced the user back to the login page.
    await session.commit()
    logger.info("auth.login", username=user.username, ip=client_ip)
    return LoginResponse(
        csrf_token=csrf,
        expires_at=issued.expires_at,
        user=UserOut(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            is_active=user.is_active,
            roles=await repo.role_codes(user),
            permissions=sorted(await repo.permissions_for(user)),
        ),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _csrf: CsrfChecked,
    principal: CurrentPrincipal,
) -> Response:
    """Revoke the current session server-side.

    Returns the *injected* response on purpose: a fresh ``Response`` would drop
    the cookie-clearing headers set below.
    """
    repo = UserRepository(session)
    if principal.session_id:
        await repo.revoke_session(principal.session_id)
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    logger.info("auth.logout", user_id=str(principal.user.id))
    await session.commit()
    return response


@router.get("/me", response_model=UserOut)
async def me(principal: CurrentPrincipal, session: DbSession) -> UserOut:
    repo = UserRepository(session)
    user = principal.user
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        is_active=user.is_active,
        roles=await repo.role_codes(user),
        permissions=sorted(principal.permissions),
    )


#: Contract path ``POST /api/v1/tokens`` alongside the grouped
#: ``POST /api/v1/auth/tokens``.
token_router = APIRouter(tags=["auth"])


@router.post(
    "/tokens",
    response_model=ApiTokenOut,
    status_code=status.HTTP_201_CREATED,
)
@token_router.post(
    "/tokens",
    response_model=ApiTokenOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_token(
    payload: ApiTokenCreate,
    session: DbSession,
    _csrf: CsrfChecked,
    principal: CurrentPrincipal,
) -> ApiTokenOut:
    """Mint an API token. The plaintext is returned exactly once."""
    repo = UserRepository(session)
    issued = await repo.create_api_token(
        principal.user,
        name=payload.name,
        scopes=payload.scopes,
        ttl_days=payload.ttl_days,
    )
    logger.info("auth.token_created", user_id=str(principal.user.id), name=payload.name)
    await session.commit()
    return ApiTokenOut(token=issued.token, prefix=issued.prefix, expires_at=issued.expires_at)


# --- administration -----------------------------------------------------
admin_router = APIRouter(prefix="/users", tags=["users"])


@admin_router.get("", response_model=list[UserOut])
async def list_users(
    session: DbSession,
    principal: require_permissions(PermissionCode.USER_READ.value),
    limit: int = 50,
    offset: int = 0,
) -> list[UserOut]:
    repo = UserRepository(session)
    page = await repo.list(limit=limit, offset=offset)
    return [
        UserOut(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            is_active=user.is_active,
            roles=await repo.role_codes(user),
            permissions=sorted(await repo.permissions_for(user)),
        )
        for user in page.items
    ]


@admin_router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    session: DbSession,
    principal: require_permissions(PermissionCode.USER_WRITE.value),
) -> UserOut:
    repo = UserRepository(session)
    try:
        roles = [RoleCode(code) for code in payload.roles]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"unknown role: {exc}") from exc
    try:
        user = await repo.create_user(
            username=payload.username,
            password=payload.password,
            roles=roles,
            email=payload.email,
            display_name=payload.display_name,
            is_active=payload.is_active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info(
        "user.created",
        user_id=str(user.id),
        username=user.username,
        roles=[role.value for role in roles],
        actor_id=str(principal.user.id),
    )
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        is_active=user.is_active,
        roles=await repo.role_codes(user),
        permissions=sorted(await repo.permissions_for(user)),
    )


__all__ = ["CSRF_COOKIE", "CSRF_HEADER", "admin_router", "router", "token_router"]
