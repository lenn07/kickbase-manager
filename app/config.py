"""Zentrale Konfiguration (12-Factor via ENV, Overrides über UI)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

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

    # --- Liga-Einstellungen, die die Kickbase-API nicht herausgibt (P1-9/F6).
    #
    # `/leagues/{l}/settings` existiert nicht, und `/me` kennt nur `mppu`
    # (Kaderlimit) und `tpc[]` (aktuelle Verteilung je Verein). Die folgenden
    # drei stehen in den Admin-Einstellungen der Liga: ablesbar, nicht
    # abrufbar. Nicht gesetzt heißt „unbekannt" — das Modell bekommt dann ein
    # `null` plus Flag und behauptet keinen Verstoß, den es nicht kennt.

    # Spielerlimit pro Verein. Zahl (1..11) **oder** `unlimited`, wenn die Liga
    # nicht begrenzt. Die beiden sind nicht dasselbe wie „nicht gesetzt":
    # `unlimited` ist eine Antwort, Weglassen ist keine.
    club_limit: int | Literal["unlimited"] | None = None

    # Admin-Option „Unterbieten deaktivieren". `True` = **jedes** Gebot unter
    # Marktwert ist blockiert, nicht nur eins unter Marktwert minus 10 Prozent.
    underpay_blocked: bool | None = None

    # Wertungsmodus der Liga. `season_points` = Summe aller Spieltagspunkte
    # gewinnt, `head_to_head` = Duell pro Spieltag (3/1/0). Die beiden
    # verlangen unterschiedliche Risikoprofile, siehe Master-Prompt §1.
    scoring_mode: Literal["season_points", "head_to_head"] | None = None

    @property
    def club_limit_value(self) -> int | None:
        """Das Limit als Zahl — `None` bei `unlimited` **und** bei „nicht gesetzt"."""
        return self.club_limit if isinstance(self.club_limit, int) else None

    @property
    def club_limit_is_unlimited(self) -> bool:
        return self.club_limit == "unlimited"

    # Aufstellungs-Writes (P0-4). Default aus: `POST /lineup` ist die einzige
    # Aktion, die unmittelbar Punkte bewegt — eine falsch geschriebene Elf holt
    # kein späterer Tick zurück. Erst einschalten, wenn der Shadow-Lauf die
    # geloggten Aufstellungen gegen die App bestätigt hat (Plan §9).
    lineup_writes_enabled: bool = False

    # Täglicher Login-Bonus (P2-15). Default aus, und das aus einem anderen
    # Grund als bei den Aufstellungs-Writes: `GET /v4/bonus/collect` ist ein
    # **GET, der wie ein Write wirkt**. Was er zurückgibt und ob ein zweiter
    # Aufruf am selben Tag harmlos ist, steht in keiner Doku und ist an keinem
    # der 16 Discovery-Dumps ablesbar — er wurde bewusst nie abgerufen (§3.4).
    # Der erste scharfe Aufruf ist deshalb eine bewusste Handlung, kein
    # Nebeneffekt eines Deploys.
    bonus_collect_enabled: bool = False
    # Wann der Bonus-Job feuert (Stunde, Europe/Berlin). Morgens, weil der
    # Kickbase-Tag um Mitternacht wechselt und ein verpasster Tag die Streak
    # bricht.
    bonus_hour: int = 9

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kb.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


def get_settings() -> Settings:
    return Settings()
