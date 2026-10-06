"""Bootstrap CLI (``app-bootstrap``).

Seeds what the code needs before it can serve anything: RBAC roles/permissions
and (optionally) the first administrator and worker registration.

The admin password is **never** taken from a CLI argument by default, because
arguments leak through ``ps`` and shell history. It comes from
``APP_BOOTSTRAP_ADMIN_PASSWORD`` or, failing that, is read interactively.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import os
import sys

from sqlalchemy import select

from app.domain.enums import RoleCode
from app.infrastructure.logging import configure_logging, get_logger
from app.persistence.database import dispose_engine, get_session_factory, ping_database
from app.persistence.models_job import GpuNode
from app.persistence.repositories.common import ensure_rbac_seeded
from app.persistence.repositories.users import UserRepository
from app.persistence.repositories.workers import WorkerRepository

logger = get_logger("bootstrap")


async def seed_rbac() -> int:
    async with get_session_factory()() as session:
        await ensure_rbac_seeded(session)
        await session.commit()
    logger.info("bootstrap.rbac_seeded")
    return 0


async def seed_admin(username: str, password: str | None, email: str | None) -> int:
    """Create the first ADMIN if no user exists yet.

    Idempotent: an existing deployment is never silently given a new admin.
    """
    if password is None:
        password = os.environ.get("APP_BOOTSTRAP_ADMIN_PASSWORD")
    if not password:
        if not sys.stdin.isatty():
            print(
                "refusing to create an admin without a password: set "
                "APP_BOOTSTRAP_ADMIN_PASSWORD or run interactively",
                file=sys.stderr,
            )
            return 2
        password = getpass.getpass("admin password: ")
    if len(password) < 8:
        print("admin password must be at least 8 characters", file=sys.stderr)
        return 2

    async with get_session_factory()() as session:
        repo = UserRepository(session)
        existing = await repo.get_by_username(username)
        if existing is not None:
            print(f"user {username!r} already exists; leaving it untouched")
            return 0
        user = await repo.create_user(
            username=username, password=password, roles=[RoleCode.ADMIN], email=email
        )
        await session.commit()
        logger.info("bootstrap.admin_created", username=username, user_id=str(user.id))
        print(f"created ADMIN {username}")
    return 0


async def seed_worker(name: str, node_key: str | None) -> tuple[int, str]:
    """Create a worker and print its one-time registration token."""
    async with get_session_factory()() as session:
        node_id = None
        if node_key:
            node_id = await session.scalar(select(GpuNode.id).where(GpuNode.node_key == node_key))
            if node_id is None:
                print(
                    f"node {node_key!r} is unknown; run the Scheduler once first",
                    file=sys.stderr,
                )
                return 2, ""
        registration = await WorkerRepository(session).create_worker(
            name=name, node_id=node_id, registration_ttl_hours=24
        )
        await session.commit()
        logger.info("bootstrap.worker_created", worker_id=str(registration.worker_id))
        return 0, registration.token


async def _run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app-bootstrap", description=__doc__)
    parser.add_argument(
        "command",
        choices=("rbac", "admin", "worker", "all"),
        help="rbac: seed roles/permissions; admin: first admin; worker: registration token",
    )
    parser.add_argument("--username", default=os.environ.get("APP_BOOTSTRAP_ADMIN", "admin"))
    parser.add_argument("--email")
    parser.add_argument("--name", default="worker-local", help="worker name")
    parser.add_argument("--node-key", help="bind the worker to a discovered node")
    args = parser.parse_args(argv)

    configure_logging()
    if not await ping_database():
        print("database unreachable; run `alembic upgrade head` first", file=sys.stderr)
        return 2

    if args.command in ("rbac", "all"):
        await seed_rbac()
    if args.command in ("admin", "all"):
        code = await seed_admin(args.username, None, args.email)
        if code:
            return code
    if args.command in ("worker", "all"):
        code, token = await seed_worker(args.name, args.node_key)
        if code:
            return code
        print(f"registration token (shown once): {token}")
        print("export APP_WORKER_REGISTRATION_TOKEN=...  then run: app-worker")
    return 0


async def _main(argv: list[str] | None = None) -> int:
    try:
        return await _run(argv)
    finally:
        # Same loop as the work: disposing from a second asyncio.run() would
        # touch a pool already bound to a closed loop.
        with contextlib.suppress(Exception):
            await dispose_engine()


def main() -> None:  # pragma: no cover - process entry point
    try:
        code = asyncio.run(_main())
    except KeyboardInterrupt:
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":  # pragma: no cover
    main()
