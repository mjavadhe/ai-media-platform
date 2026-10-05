"""Application configuration.

All environment-specific values come from environment variables (AGENTS.md:
no hard-coded paths, secrets, GPU names or project names). Secrets are never
logged; `Settings.safe_dump()` is the only representation used for diagnostics.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

WorkerStatusTimeout = Literal["worker_lost"]


class Settings(BaseSettings):
    """Runtime settings loaded from environment / `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APP_",
        extra="ignore",
    )

    # --- Application -----------------------------------------------------
    app_name: str = "AI Media Platform"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    api_prefix: str = "/api/v1"

    # --- Database --------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://ai_media:ai_media@localhost:5432/ai_media",
        description="SQLAlchemy async URL. Secrets belong in the environment, never in code.",
    )
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_echo: bool = False

    # --- Auth / security -------------------------------------------------
    secret_key: SecretStr = SecretStr("dev-insecure-change-me")
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 12
    worker_credential_ttl_hours: int = 24 * 30
    session_cookie_name: str = "session"
    cookie_secure: bool = False
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- GPU backend -----------------------------------------------------
    gpu_backend: Literal["auto", "mock", "nvml"] = "mock"
    mock_gpu_nodes: int = Field(
        default=1,
        description="Number of simulated nodes exposed by MockGpuBackend in dev/CI.",
    )
    mock_gpus_per_node: int = Field(
        default=2, description="GPUs per simulated node in MockGpuBackend."
    )
    mock_gpu_vram_mb: int = Field(default=11_000, description="VRAM per mocked GPU.")
    mock_gpu_name: str = "MockGPU"
    mock_gpu_failure_rate: float = Field(
        default=0.0, description="Probability a mocked execution fails (deterministic tests set 0)."
    )
    mock_gpu_oom_rate: float = Field(
        default=0.0, description="Probability a mocked execution raises OOM."
    )

    # --- Storage ---------------------------------------------------------
    storage_backend: Literal["local"] = "local"
    storage_root: Path = Path("./var/storage")
    storage_bucket: str = "ai-media"

    # --- Scheduler -------------------------------------------------------
    scheduler_enabled: bool = True
    scheduler_loop_interval_seconds: float = 1.0
    scheduler_lease_advisory_key: int = 918_273_645
    scheduler_reconcile_interval_seconds: float = 5.0
    scheduler_release_pending_grace_seconds: float = 2.0

    # --- Job / queue / lease --------------------------------------------
    job_max_retries: int = 3
    job_retry_base_seconds: float = 2.0
    job_retry_max_seconds: float = 300.0
    lease_ttl_seconds: int = 60
    heartbeat_interval_seconds: int = 10
    heartbeat_missed_threshold: int = 3
    progress_min_interval_seconds: float = 0.25

    # --- Logging / observability ----------------------------------------
    log_level: str = "INFO"
    log_json: bool = True
    metrics_enabled: bool = True

    # --- SSE -------------------------------------------------------------
    sse_heartbeat_seconds: float = 15.0
    sse_poll_interval_seconds: float = 1.0

    # --- Worker ----------------------------------------------------------
    worker_id: str | None = Field(
        default=None, description="Explicit worker id; when unset the agent self-registers."
    )
    worker_name: str = "worker-local"
    worker_base_url: str = "http://localhost:8000"
    worker_poll_interval_seconds: float = 1.0
    worker_execution_backend: Literal["mock", "comfyui", "diffusers"] = "mock"
    comfyui_base_url: str = "http://localhost:8188"
    comfyui_timeout_seconds: float = 30.0

    @field_validator("database_url")
    @classmethod
    def _require_async_postgres(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "database_url must use the asyncpg driver "
                "(postgresql+asyncpg://). SQLite is not a supported production database."
            )
        return value

    def safe_dump(self) -> dict[str, object]:
        """Configuration snapshot with every secret redacted."""
        redacted: dict[str, object] = {}
        for name, value in self.model_dump().items():
            redacted[name] = "***" if isinstance(value, SecretStr) else value
        return redacted


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor (dependency-injection entry point)."""
    return Settings()
