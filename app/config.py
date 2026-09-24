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

    # Spielerlimit pro Verein (Kickbase-Admin-Einstellung, real 1 bis 11).
    # Die API liefert es **nicht**: `/leagues/{l}/settings` existiert nicht und
    # `/me` kennt nur `mppu` (Kaderlimit) und `tpc[]` (aktuelle Verteilung).
    # Ablesbar ist es in den Admin-Einstellungen der Liga — deshalb hier
    # konfigurierbar statt geraten. `None` heißt „unbekannt": das Modell
    # bekommt ein `null` plus Flag und behauptet dann keinen Verstoß (P1-9).
    club_limit: int | None = None

    # Aufstellungs-Writes (P0-4). Default aus: `POST /lineup` ist die einzige
    # Aktion, die unmittelbar Punkte bewegt — eine falsch geschriebene Elf holt
    # kein späterer Tick zurück. Erst einschalten, wenn der Shadow-Lauf die
    # geloggten Aufstellungen gegen die App bestätigt hat (Plan §9).
    lineup_writes_enabled: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kb.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


def get_settings() -> Settings:
    return Settings()
