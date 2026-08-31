"""SQLModel-Tabellen für Setup + Betriebs-Daten.

Alle sensiblen Felder (`encrypted_*`) enthalten Fernet-Cipher-Text, niemals Klartext.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class UserRow(SQLModel, table=True):
    """Single-User-App — trotzdem als Row modelliert, damit Foreign-Keys eindeutig sind."""

    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    encrypted_password: bytes
    kb_user_id: str = Field(default="")
    kb_token: bytes | None = Field(default=None)  # verschlüsselt
    kb_token_expires_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class CredentialRow(SQLModel, table=True):
    """Generischer Store für verifizierte Secrets (aktuell: anthropic-Key)."""

    __tablename__ = "credentials"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    kind: str = Field(index=True)  # z. B. "anthropic"
    encrypted_value: bytes
    verified_at: datetime = Field(default_factory=_now)


class SmtpConfigRow(SQLModel, table=True):
    __tablename__ = "smtp_config"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True, unique=True)
    host: str
    port: int
    username: str
    encrypted_password: bytes
    from_addr: str
    to_addr: str
    use_tls: bool = True
    use_starttls: bool = False
    verified_at: datetime | None = Field(default=None)


class LeagueRow(SQLModel, table=True):
    __tablename__ = "leagues"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    kb_league_id: str = Field(index=True)
    name: str
    is_active: bool = Field(default=False)


class SettingsRow(SQLModel, table=True):
    """Runtime-Overrides zur ENV-Konfiguration (F-3 Intervall, F-10 Guardrails)."""

    __tablename__ = "settings"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True, unique=True)
    interval_min: int = Field(default=120)
    dry_run: bool = Field(default=True)
    max_trade_pct: float = Field(default=0.25)
    min_cash_reserve: int = Field(default=0)
    min_action_score: float = Field(default=0.6)
    blacklist: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    digest_enabled: bool = Field(default=False)
    digest_hour: int = Field(default=20)


class TradeLogRow(SQLModel, table=True):
    """Historie aller Entscheidungen inkl. HOLD-Ticks (Nachvollziehbarkeit im Dashboard)."""

    __tablename__ = "trade_log"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    ts: datetime = Field(default_factory=_now, index=True)
    action: str  # BUY, SELL, ACCEPT_OFFER, DECLINE_OFFER, HOLD
    player_id: str | None = None
    player_name: str | None = None
    price: int | None = None
    reason_text: str = ""
    executed: bool = False
    response_code: int | None = None
    notified_at: datetime | None = None
    context: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
