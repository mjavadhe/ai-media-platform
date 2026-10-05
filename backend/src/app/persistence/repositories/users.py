"""User / session / token repository (RBAC-backed, doc 12)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ROLE_PERMISSIONS, RoleCode
from app.infrastructure.security import (
    generate_opaque_token,
    hash_password,
    hash_token,
    token_prefix,
    verify_password,
    verify_token,
)
from app.persistence.models_identity import (
    ApiToken,
    Role,
    Session,
    User,
    UserRole,
)
from app.persistence.repositories.common import ensure_rbac_seeded, resolve_permissions, utcnow


class AuthenticationError(Exception):
    """Bad credentials or an unusable session/token."""


class PermissionDeniedError(Exception):
    """Authenticated but lacking the required permission code."""


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A freshly minted opaque credential. Plaintext is returned only here."""

    token: str
    prefix: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Principal:
    """Resolved caller identity plus effective permissions."""

    user: User
    permissions: frozenset[str]
    session_id: uuid.UUID | None = None
    via: str = "session"  # session | token

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, *permissions: str) -> None:
        missing = [code for code in permissions if code not in self.permissions]
        if missing:
            raise PermissionDeniedError(f"missing permissions: {', '.join(missing)}")


@dataclass(frozen=True, slots=True)
class UserPage:
    items: list[User]
    total: int


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- provisioning -----------------------------------------------------
    async def create_user(
        self,
        *,
        username: str,
        password: str,
        roles: list[RoleCode] | None = None,
        email: str | None = None,
        display_name: str | None = None,
        is_active: bool = True,
    ) -> User:
        await ensure_rbac_seeded(self._session)
        existing = await self.get_by_username(username)
        if existing is not None:
            raise ValueError(f"username {username!r} already exists")
        user = User(
            username=username,
            email=email,
            display_name=display_name or username,
            password_hash=hash_password(password),
            is_active=is_active,
        )
        self._session.add(user)
        await self._session.flush()
        for role in roles or [RoleCode.ARTIST]:
            await self.add_role(user, role)
        return user

    async def add_role(self, user: User, role: RoleCode) -> None:
        role_row = await self._session.scalar(select(Role).where(Role.code == role.value))
        if role_row is None:
            await ensure_rbac_seeded(self._session)
            role_row = await self._session.scalar(select(Role).where(Role.code == role.value))
        if role_row is None:  # pragma: no cover - seeding always creates it
            raise ValueError(f"unknown role {role.value}")
        existing = await self._session.get(UserRole, {"user_id": user.id, "role_id": role_row.id})
        if existing is None:
            self._session.add(UserRole(user_id=user.id, role_id=role_row.id))
            await self._session.flush()

    async def set_password(self, user: User, password: str) -> None:
        user.password_hash = hash_password(password)
        await self._session.flush()

    # --- authentication ---------------------------------------------------
    async def authenticate(self, username: str, password: str) -> User:
        user = await self.get_by_username(username)
        if user is None or not verify_password(password, user.password_hash):
            # Same error for unknown user and bad password: no user enumeration.
            raise AuthenticationError("invalid username or password")
        if not user.is_active:
            raise AuthenticationError("user is inactive")
        user.last_login_at = utcnow()
        await self._session.flush()
        return user

    async def create_session(
        self,
        user: User,
        *,
        ttl_hours: int = 12,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> IssuedToken:
        token = generate_opaque_token()
        session_row = Session(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=utcnow() + timedelta(hours=ttl_hours),
            user_agent=(user_agent or "")[:255] or None,
            ip=ip,
        )
        self._session.add(session_row)
        await self._session.flush()
        return IssuedToken(
            token=token, prefix=token_prefix(token), expires_at=session_row.expires_at
        )

    async def resolve_session(self, token: str) -> Principal:
        """Resolve a session cookie to a principal, enforcing expiry/revocation."""
        row = await self._session.scalar(
            select(Session).where(Session.token_hash == hash_token(token)).with_for_update()
        )
        if row is None:
            raise AuthenticationError("unknown session")
        if row.revoked_at is not None:
            raise AuthenticationError("session revoked")
        if row.expires_at <= utcnow():
            raise AuthenticationError("session expired")
        user = await self._session.get(User, row.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("user unavailable")
        return Principal(
            user=user,
            permissions=frozenset(await resolve_permissions(self._session, user)),
            session_id=row.id,
            via="session",
        )

    async def revoke_session(self, session_id: uuid.UUID) -> None:
        row = await self._session.get(Session, session_id, with_for_update=True)
        if row is not None and row.revoked_at is None:
            row.revoked_at = utcnow()
            await self._session.flush()

    async def revoke_all_sessions(self, user_id: uuid.UUID) -> int:
        rows = await self._session.scalars(
            select(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
        )
        count = 0
        for row in rows.all():
            row.revoked_at = utcnow()
            count += 1
        await self._session.flush()
        return count

    async def purge_expired_sessions(self) -> int:
        rows = await self._session.scalars(select(Session).where(Session.expires_at <= utcnow()))
        count = 0
        for row in rows.all():
            await self._session.delete(row)
            count += 1
        await self._session.flush()
        return count

    # --- API tokens -------------------------------------------------------
    async def create_api_token(
        self, user: User, *, name: str, scopes: list[str] | None = None, ttl_days: int | None = None
    ) -> IssuedToken:
        token = generate_opaque_token(40)
        row = ApiToken(
            user_id=user.id,
            name=name,
            token_hash=hash_token(token),
            prefix=token_prefix(token),
            scopes=scopes or [],
            expires_at=(utcnow() + timedelta(days=ttl_days)) if ttl_days else None,
        )
        self._session.add(row)
        await self._session.flush()
        return IssuedToken(token=token, prefix=row.prefix, expires_at=row.expires_at or utcnow())

    async def resolve_api_token(self, token: str) -> Principal:
        row = await self._session.scalar(
            select(ApiToken).where(ApiToken.prefix == token_prefix(token, 8))
        )
        if row is None or not verify_token(token, row.token_hash):
            raise AuthenticationError("invalid api token")
        if row.revoked_at is not None:
            raise AuthenticationError("api token revoked")
        if row.expires_at is not None and row.expires_at <= utcnow():
            raise AuthenticationError("api token expired")
        user = await self._session.get(User, row.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("user unavailable")
        row.last_used_at = utcnow()
        await self._session.flush()
        granted = set(await resolve_permissions(self._session, user))
        if row.scopes:
            granted &= set(row.scopes)
        return Principal(user=user, permissions=frozenset(granted), via="token")

    async def revoke_api_token(self, token_id: uuid.UUID) -> None:
        row = await self._session.get(ApiToken, token_id, with_for_update=True)
        if row is not None:
            row.revoked_at = utcnow()
            await self._session.flush()

    # --- reads ------------------------------------------------------------
    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_username(self, username: str) -> User | None:
        return await self._session.scalar(select(User).where(User.username == username))

    async def list(self, *, limit: int = 50, offset: int = 0) -> UserPage:
        total = await self._session.scalar(select(func.count()).select_from(User))
        rows = await self._session.scalars(
            select(User).order_by(User.username).limit(limit).offset(offset)
        )
        return UserPage(items=list(rows.all()), total=int(total or 0))

    async def permissions_for(self, user: User) -> frozenset[str]:
        return frozenset(await resolve_permissions(self._session, user))

    async def role_codes(self, user: User) -> list[str]:
        rows = await self._session.scalars(
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user.id)
        )
        return sorted(rows.all())


def permissions_for_role(role: RoleCode) -> tuple[str, ...]:
    return tuple(permission.value for permission in ROLE_PERMISSIONS[role])


__all__ = [
    "AuthenticationError",
    "IssuedToken",
    "PermissionDeniedError",
    "Principal",
    "UserPage",
    "UserRepository",
    "permissions_for_role",
]
