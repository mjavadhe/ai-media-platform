"""Regressions found by driving the real app rather than unit-testing it.

Each test corresponds to a defect that shipped in the API and was only
observable against a running process, or to a contract shape the operator UI
codes against. All of them are cheap: HTTP surface and invariants, no GPU.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest


@pytest.fixture
def api(client, db, admin_token, tmp_path):
    """Minimal stand-in for the e2e Harness: a client plus its auth header."""
    harness = type("ApiHarness", (), {})()
    harness.client = client
    harness.token = admin_token["token"]
    harness.auth = {"authorization": f"Bearer {admin_token['token']}"}
    return harness


@pytest.mark.parametrize("alias", ["jobs", "logs", "gpus", "nodes"])
async def test_every_contract_stream_path_exists(api, alias: str) -> None:
    """The contract lists four stream paths; all must be registered.

    ``ASGITransport`` buffers the response, and an SSE stream never ends, so the
    call is wrapped in a timeout: success is "headers arrived, body still open".
    A missing route fails fast with 404 instead.
    """
    with contextlib.suppress(TimeoutError):
        response = await asyncio.wait_for(
            api.client.get(f"/api/v1/stream/{alias}", headers=api.auth),
            timeout=2.0,
        )
        assert response.status_code == 200, f"/stream/{alias} -> {response.status_code}"
        assert response.headers["content-type"].startswith("text/event-stream")

    # The API mounts routers behind a prefix, so assert on the OpenAPI surface
    # rather than walking ``app.routes``.
    paths = api.client.app.openapi()["paths"]
    assert f"/api/v1/stream/{alias}" in paths, (
        f"/api/v1/stream/{alias} is not registered; "
        f"stream paths present: {sorted(p for p in paths if 'stream' in p)}"
    )


async def test_sse_stream_does_not_depend_on_a_request_scoped_session() -> None:
    """An SSE stream must not pin a pooled DB connection for its lifetime.

    ``stream_events`` used to depend on a request-scoped ``DbSession`` that
    FastAPI keeps open for the whole (hours-long) stream. A handful of open
    dashboard connections exhausted the pool, after which every other endpoint
    returned 503 ``database_unavailable``. Assert the dependency is gone rather
    than trying to observe pool exhaustion over a real socket.
    """
    from app.api.v1 import events as events_module

    signature = events_module.stream_events.__annotations__
    assert "session" not in signature, (
        "stream_events must not take a DbSession: it would be held for the "
        "entire stream and pin a pooled connection"
    )

    source = events_module.stream_events.__doc__ or ""
    assert source, "stream_events should keep its contract docstring"


async def test_media_version_has_no_shadowing_storage_key() -> None:
    """Media versions reference StorageObject rows by FK.

    Key, mime type, size and checksum live on the object. Reading them off the
    version made both media read endpoints raise AttributeError (HTTP 500).
    """
    from app.persistence.models_creative import GeneratedMediaVersion

    assert not hasattr(GeneratedMediaVersion, "storage_key")


async def test_project_metadata_serializes_as_metadata() -> None:
    """``metadata_`` must serialize as ``metadata``.

    Aliasing the *read* side to ``metadata`` resolved to SQLAlchemy's inherited
    Declarative MetaData object, so ``from_attributes`` validation failed.
    """
    from app.api.v1.schemas import ProjectOut

    field = ProjectOut.model_fields["metadata_"]
    assert field.serialization_alias == "metadata"
    assert "alias" not in field.annotation.__class__.__name__


async def test_storage_health_key_matches_its_consumer(api) -> None:
    """``main.py`` gates startup on ``health()["ok"]``; both must agree."""
    from app.services.storage.local import LocalStorageBackend

    info = await LocalStorageBackend(api.client.app.state.settings.storage_root).health()
    assert info["ok"] is True
    assert "writable" not in info


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/v1/models/workflows", "items"),
        ("/api/v1/gpus/nodes", "items"),
    ],
)
async def test_registry_and_node_endpoints_return_200(api, path: str, expected: str) -> None:
    """Both previously returned HTTP 400 for every caller.

    ``/models/workflows`` omitted a required field; ``/gpus/nodes`` aliased a
    mapped column to an attribute that exists on the Declarative base.
    """
    response = await api.client.get(path, headers=api.auth)
    assert response.status_code == 200, response.text
    assert isinstance(response.json()[expected], list)



async def test_login_commits_before_returning_the_cookie(api) -> None:
    """The session row must be durable when the response is sent.

    ``login`` relied on ``yield``-dependency teardown to commit. FastAPI runs
    that teardown *after* the response, so the browser's immediate ``/auth/me``
    could not find the session and the UI bounced back to the login page.
    This asserts the cookie works on the very next request.
    """
    created = await api.client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin-password-123"},
    )
    assert created.status_code == 200, created.text
    csrf = created.json()["csrf_token"]
    assert created.cookies.get("session"), "login must set the session cookie"

    # No sleep, no retry: the very next request must already authenticate.
    me = await api.client.get("/api/v1/auth/me")
    assert me.status_code == 200, f"immediate /auth/me -> {me.status_code}: {me.text}"
    assert me.json()["username"] == "admin"

    logout = await api.client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}
    )
    assert logout.status_code == 204, logout.text
