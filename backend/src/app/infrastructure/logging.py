"""Structured logging with request/job correlation (doc 13).

The logging setup is deliberately dependency-light: `structlog` renders JSON to
stdout so the platform stays usable without Loki/Grafana/Redis (ADR-0003,
doc 31). Correlation ids are carried in a `contextvars` ContextVar so every log
line emitted while handling a request or a job attempt is tagged.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from contextvars import ContextVar
from types import MappingProxyType
from typing import Any

import structlog

from app.infrastructure.config import get_settings

#: Correlation fields for the current request/task. The default is a
#: module-level empty mapping which is never mutated, so sharing it is safe.
_EMPTY_CORRELATION: Mapping[str, Any] = MappingProxyType({})
_correlation: ContextVar[Mapping[str, Any]] = ContextVar("correlation", default=_EMPTY_CORRELATION)


def set_correlation(**values: Any) -> None:
    """Attach correlation fields (request_id, job_id, ...) to the current context."""
    _correlation.set({k: v for k, v in values.items() if v is not None})


def clear_correlation() -> None:
    _correlation.set({})


def get_correlation() -> dict[str, Any]:
    return dict(_correlation.get())


def _inject_correlation(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key, value in _correlation.get().items():
        event_dict.setdefault(key, value)
    return event_dict


def configure_logging() -> None:
    """Idempotently configure root + structlog logging."""
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)
    for noisy in ("uvicorn.access", "uvicorn.error", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _inject_correlation,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
