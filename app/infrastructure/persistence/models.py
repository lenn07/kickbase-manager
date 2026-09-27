"""SQLModel-Tabellen für Setup + Betriebs-Daten.

Alle sensiblen Felder (`encrypted_*`) enthalten Fernet-Cipher-Text, niemals Klartext.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
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
    blacklist: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    digest_enabled: bool = Field(default=False)
    digest_hour: int = Field(default=20)


class MarketValueCacheRow(SQLModel, table=True):
    """Gecachte Marktwert-Historie eines Spielers (P1-7).

    Eine Zeile je (Liga, Spieler). `valid_until` ist der nächste
    Marktwert-Update-Zeitpunkt (`mvud`) — bis dahin ist die Serie garantiert
    unverändert, denn Kickbase schreibt Marktwerte nur einmal täglich fort.

    `points` hält die Serie als JSON-Liste `[[ISO-Tag, Wert], …]`. Bewusst
    keine eigene Zeile pro Punkt: die Serie wird immer als Ganzes geholt und
    als Ganzes ersetzt, und 365 Punkte mal 20 Spieler wären 7.300 Zeilen, die
    nie einzeln gelesen werden.
    """

    __tablename__ = "market_value_cache"
    __table_args__ = (UniqueConstraint("league_id", "player_id", name="uq_mv_cache_league_player"),)

    id: int | None = Field(default=None, primary_key=True)
    league_id: str = Field(index=True)
    player_id: str = Field(index=True)
    points: list[list[Any]] = Field(default_factory=list, sa_column=Column(JSON))
    fetched_at: datetime = Field(default_factory=_now)
    valid_until: datetime = Field(index=True)


class PlayerPerformanceCacheRow(SQLModel, table=True):
    """Gecachte Spieltags-Historie eines Spielers (P1-8).

    Andere Haltbarkeit als der Marktwert-Cache: Spieltagspunkte stehen fest,
    sobald der Spieltag durch ist, und ändern sich erst wieder, wenn der
    nächste angepfiffen wird. Gültigkeitsgrenze ist deshalb
    `next_matchday_start` — und solange ein Spieltag **läuft**, liegt der in
    der Vergangenheit, der Cache greift also nicht und jeder Tick sieht die
    Live-Punkte.

    `matchdays` hält `[[day, points, minutes, war_startelf], …]`.
    """

    __tablename__ = "player_performance_cache"
    __table_args__ = (
        UniqueConstraint("league_id", "player_id", name="uq_perf_cache_league_player"),
    )

    id: int | None = Field(default=None, primary_key=True)
    league_id: str = Field(index=True)
    player_id: str = Field(index=True)
    season: str = Field(default="")
    matchdays: list[list[Any]] = Field(default_factory=list, sa_column=Column(JSON))
    fetched_at: datetime = Field(default_factory=_now)
    valid_until: datetime = Field(index=True)


class CompetitionContextCacheRow(SQLModel, table=True):
    """Gecachte Tabelle + Spielplan eines Wettbewerbs (P2-11).

    Eine Zeile je Wettbewerb — die Bundesliga-Tabelle ist für jede Kickbase-Liga
    dieselbe, anders als bei den beiden Spieler-Caches gibt es hier also keinen
    Liga-Schlüssel.

    `standings` hält `[[team_id, name, platz, punkte, spiele, tordifferenz], …]`,
    `fixtures` `[[spieltag, anpfiff-ISO, heim, gast, beendet], …]`. Wie beim
    Marktwert-Cache bewusst als JSON statt als Zeilen pro Eintrag: die 306
    Paarungen werden immer als Ganzes geholt und als Ganzes ersetzt.
    """

    __tablename__ = "competition_context_cache"

    id: int | None = Field(default=None, primary_key=True)
    competition_id: str = Field(index=True, unique=True)
    standings: list[list[Any]] = Field(default_factory=list, sa_column=Column(JSON))
    fixtures: list[list[Any]] = Field(default_factory=list, sa_column=Column(JSON))
    fetched_at: datetime = Field(default_factory=_now)
    valid_until: datetime = Field(index=True)


class MarketMetaRow(SQLModel, table=True):
    """Die beiden Uhren aus dem Market-Root, zwischen Ticks aufbewahrt (P1-10).

    Der Scheduler braucht `next_matchday_start`, um die beweglichen Fenster zu
    legen — bekommt ihn aber nur aus einem Tick, und der läuft beim Start des
    Containers noch nicht. Ohne diese Zeile stünde nach jedem Neustart bis zum
    ersten Intervall-Tick kein Deadline-Fenster; startet der Container am
    Freitagabend neu, ist genau der Moment weg, an dem das Konto ins Plus muss.

    Eine Zeile je Liga. Reiner Ableseplatz für den Scheduler, keine Historie.
    """

    __tablename__ = "market_meta"

    id: int | None = Field(default=None, primary_key=True)
    league_id: str = Field(index=True, unique=True)
    next_matchday_start: datetime | None = Field(default=None)
    mv_update_at: datetime | None = Field(default=None)
    updated_at: datetime = Field(default_factory=_now)


class TradeLogRow(SQLModel, table=True):
    """Historie aller Entscheidungen inkl. HOLD-Ticks (Nachvollziehbarkeit im Dashboard).

    Die Tabelle hat zwei Rollen, und die Datenverwaltung trennt sie:

    - **Historie** — was ist passiert. Wird im Dashboard gezeigt und nur auf
      ausdrückliche Anweisung gelöscht.
    - **Gedächtnis** — woraus der Bot seinen Weltzustand ableitet: Kaufpreise
      für die Gewinn-Mitnahme, laufende eigene Gebote, eigene Listings. Kickbase
      liefert diese drei nicht zurück, sie stehen nur hier.

    Genau die zweite Rolle wird falsch, sobald in der Kickbase-App von Hand
    gehandelt wird: ein selbst verkaufter Spieler steht weiter als offener Kauf
    im Log. `superseded_at` ist die Antwort darauf — gesetzt heißt „die Realität
    hat diese Zeile überholt": die Historie bleibt lesbar, das Gedächtnis liest
    sie nicht mehr.
    """

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
    # Gesetzt = von der Realität überholt, siehe Klassen-Docstring. Die Zeile
    # bleibt in der Historie stehen; die Gedächtnis-Queries überspringen sie.
    superseded_at: datetime | None = Field(default=None, index=True)
