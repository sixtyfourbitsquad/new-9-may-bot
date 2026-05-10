"""Environment-based application settings (Pydantic Settings)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core
    bot_token: SecretStr = Field(..., description="Telegram Bot API token")
    webhook_base_url: str = Field(
        ...,
        description="Public HTTPS base URL, e.g. https://bot.example.com",
    )
    webhook_path: str = Field("/tg/webhook/{secret}", description="Path template; {secret} replaced")
    webhook_secret: SecretStr = Field(..., description="Secret token segment for webhook URL")

    # Optional Telegram webhook secret header (setWebhook secret_token)
    telegram_webhook_secret_token: SecretStr | None = Field(
        default=None,
        description="If set, Telegram sends X-Telegram-Bot-Api-Secret-Token header",
    )

    # Call setWebhook on startup (disable for local dev without public URL)
    webhook_register_on_startup: bool = Field(
        default=True,
        validation_alias="WEBHOOK_REGISTER_ON_STARTUP",
    )

    # Admins receive user forwards in private chat (comma-separated user ids). Listed as str so
    # pydantic-settings does not JSON-decode env values before validators run.
    admin_user_ids_csv: str = Field(
        ...,
        validation_alias="ADMIN_USER_IDS",
        description="Comma-separated Telegram user ids",
    )

    @field_validator("admin_user_ids_csv")
    @classmethod
    def validate_admin_user_ids_csv(cls, v: str) -> str:
        parts = [p.strip() for p in v.split(",") if p.strip()]
        if not parts:
            raise ValueError("ADMIN_USER_IDS must contain at least one numeric user id")
        for p in parts:
            int(p)
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def admin_user_ids(self) -> list[int]:
        parts = [p.strip() for p in self.admin_user_ids_csv.split(",") if p.strip()]
        return [int(p) for p in parts]

    # Bootstrap: optional first owner user id (seeded on startup if DB has no owners)
    initial_owner_id: int | None = Field(
        default=None,
        description="Telegram user id granted owner role when admins table is empty",
    )

    # Database
    postgres_dsn: str = Field(
        "postgresql://tg_bot:tg_bot@localhost:5432/tg_bot",
        description="asyncpg DSN",
    )
    postgres_pool_min: int = 2
    postgres_pool_max: int = 20

    # Redis
    redis_url: str = Field("redis://localhost:6379/0")
    redis_broadcast_queue: str = "broadcast:jobs"
    redis_scheduler_queue: str = "scheduler:jobs"
    redis_fsm_prefix: str = "fsm:"
    redis_rate_prefix: str = "rate:"
    redis_livestream_prefix: str = "livestream:"

    # Workers
    broadcast_concurrency: int = Field(25, ge=1, le=100)
    broadcast_chunk_size: int = Field(500, ge=1, le=5000)
    scheduler_tick_seconds: float = Field(2.0, ge=0.5)
    retention_tick_seconds: float = Field(5.0, ge=1.0)

    # Rate limits (anti-spam)
    user_message_rate_per_minute: int = Field(30, ge=1)
    admin_reply_rate_per_minute: int = Field(120, ge=1)
    livestream_cooldown_seconds: int = Field(300, ge=0)

    # HTTP server (webhook receiver)
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Storage for downloaded media cache (optional)
    storage_dir: str = "./storage"

    # Post-/start onboarding drip (+1h/+1d/+3d jobs in Postgres); disable to skip scheduling/sends
    onboarding_drip_enabled: bool = Field(default=True, validation_alias="ONBOARDING_DRIP_ENABLED")

    # Optional path to append-only log file for “download full log” in admin panel
    log_file_path: str | None = Field(default=None, validation_alias="LOG_FILE_PATH")

    @field_validator("webhook_base_url")
    @classmethod
    def strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    def webhook_full_url(self) -> str:
        """Full webhook URL registered with Telegram."""
        secret = self.webhook_secret.get_secret_value()
        path = self.webhook_path.format(secret=secret)
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.webhook_base_url}{path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton for process lifetime."""
    return Settings()
