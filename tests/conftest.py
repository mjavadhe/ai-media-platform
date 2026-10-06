"""Shared fixtures.

Two databases are involved and they must not be confused:

* ``ai_media`` — whatever ``.env`` points at. Integration tests never touch it
  unless explicitly asked to.
* ``ai_media_test`` — created once per session and truncated between tests.

The test database URL is derived from the app settings, so there is exactly one
place that knows the credentials.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.config import Settings, get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]


def _test_database_url() -> str:
    """``postgresql+asyncpg://…/ai_media`` -> ``…/ai_media_test``."""
    override = os.environ.get("APP_TEST_DATABASE_URL")
    if override:
        return override
    settings = get_settings()
    parts = urlsplit(settings.database_url)
    name = parts.path.lstrip("/") or "ai_media"
    base = name.removesuffix("_test")
    return urlunsplit(parts._replace(path=f"/{base}_test"))


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def database_url() -> str:
    return _test_database_url()


def test_settings(tmp_path) -> Settings:
    """Settings for the app under test.

    Storage goes to a per-test directory, and the release grace period is zero so
    Tx2 happens on the very next Scheduler cycle instead of after a real delay.
    """
    return Settings(
        storage_root=str(tmp_path / "objects"),
        scheduler_release_pending_grace_seconds=0.0,
    )


@pytest.fixture(scope="session")
def event_loop_policy():  # pragma: no cover - pytest-asyncio plumbing
    import asyncio

    return asyncio.DefaultEventLoopPolicy()


@pytest_asyncio.fixture(scope="session")
async def engine(database_url: str):
    """Session-wide engine against ``ai_media_test``, created from metadata.

    Alembic is the production migration path; tests use ``create_all`` so a
    fresh checkout can be tested without running migrations first.
    """
    # Importing the model modules registers every table on Base.metadata.
    import app.persistence.models_creative
    import app.persistence.models_identity
    import app.persistence.models_job  # noqa: F401
    from app.persistence.base import Base

    admin_url = database_url
    eng = create_async_engine(admin_url, poolclass=None)
    async with eng.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await eng.dispose()

    eng = create_async_engine(database_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    import app.persistence.models_creative
    import app.persistence.models_identity
    import app.persistence.models_job  # noqa: F401
    from app.persistence.base import Base

    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    # One statement, so tests cannot leak rows into each other.
    tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture
async def session(db) -> AsyncIterator[AsyncSession]:
    async with db() as s:
        yield s


@pytest_asyncio.fixture
async def client(db, monkeypatch, tmp_path) -> AsyncIterator[AsyncClient]:
    """An in-process API client wired to the test database and a throwaway volume.

    The app's own engine is patched out so requests share the test session's
    schema; without this the app would connect to ``ai_media``. Storage points
    at a per-test directory so artifacts never land on a developer's disk.
    """
    from app.main import create_app
    from app.persistence import database as database_module

    real_get_session_factory = database_module.get_session_factory

    monkeypatch.setattr(database_module, "get_session_factory", lambda: db)
    monkeypatch.setattr("app.api.deps.get_session_factory", lambda: db, raising=False)

    # The app now serves the settings it was built with, so tests get an
    # isolated storage volume without touching the developer's disk.
    settings = test_settings(tmp_path)
    import app.api.deps as deps_module

    deps_module._storage = None  # type: ignore[attr-defined]
    app = create_app(settings)
    # The lifespan would connect through app.dependencies.get_session_factory,
    # which the app factory already bound to the test factory above.
    app.state.session_factory = db  # type: ignore[attr-defined]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        c.app = app  # type: ignore[attr-defined]
        yield c
    assert real_get_session_factory is not None


@pytest_asyncio.fixture
async def admin_token(client: AsyncClient) -> dict:
    """A logged-in ADMIN session plus a bearer token for API convenience."""
    from app.persistence.repositories.users import UserRepository

    async with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        repo = UserRepository(session)
        user = await repo.create_user(
            username="admin", password="admin-password-123", roles=[_ADMIN_ROLE()]
        )
        await session.commit()

    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin-password-123"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    token = await client.post(
        "/api/v1/auth/tokens",
        json={"name": "pytest"},
        headers={"X-CSRF-Token": body["csrf_token"]},
    )
    assert token.status_code == 201, token.text
    return {"token": token.json()["token"], "csrf": body["csrf_token"], "user_id": str(user.id)}


def _ADMIN_ROLE():
    from app.domain.enums import RoleCode

    return RoleCode.ADMIN


@pytest.fixture
def unique_suffix() -> str:
    return uuid.uuid4().hex[:8]
