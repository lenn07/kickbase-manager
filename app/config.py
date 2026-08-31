"""Zentrale Konfiguration (12-Factor via ENV, Overrides über UI)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"  # noqa: S104 — bind in Container gewünscht
    port: int = 8000

    data_dir: Path = Field(default=Path("/data"))
    secret_file: Path | None = None

    log_level: str = "INFO"
    timezone: str = "Europe/Berlin"

    # Scheduler-Loop (Phase 3) — im Test/CI-Kontext explizit deaktivierbar,
    # damit TestClient-Lifespan-Runs keinen Hintergrund-Job starten.
    scheduler_enabled: bool = True
    default_interval_min: int = 120

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kb.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


def get_settings() -> Settings:
    return Settings()
