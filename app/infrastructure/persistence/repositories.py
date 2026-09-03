"""Repositories kapseln SQLModel-Queries für die Application-Schicht.

Sie liefern **Rohzeilen** — Entschlüsselung von Cipher-Text und Mapping auf
Domain-Objekte macht die Application-Schicht (SetupService), damit die
Persistenz keine Krypto-Verantwortung trägt.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session, select

from app.infrastructure.persistence.models import (
    CredentialRow,
    LeagueRow,
    SettingsRow,
    SmtpConfigRow,
    TradeLogRow,
    UserRow,
)


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_singleton(self) -> UserRow | None:
        return self._session.exec(select(UserRow).limit(1)).first()

    def upsert_credentials(self, *, email: str, encrypted_password: bytes) -> UserRow:
        """Legt den User an bzw. aktualisiert das Passwort. Session-Felder bleiben unberührt."""
        existing = self._session.exec(select(UserRow).where(UserRow.email == email)).first()
        if existing is None:
            row = UserRow(email=email, encrypted_password=encrypted_password)
            self._session.add(row)
        else:
            existing.encrypted_password = encrypted_password
            existing.updated_at = datetime.now(UTC)
            row = existing
        self._session.commit()
        self._session.refresh(row)
        return row

    def update_session(
        self,
        *,
        user_id: int,
        kb_user_id: str,
        kb_token: bytes,
        kb_token_expires_at: datetime,
    ) -> UserRow:
        """Aktualisiert nur die Session-bezogenen Felder — Credentials bleiben unangetastet."""
        row = self._session.get(UserRow, user_id)
        if row is None:
            raise LookupError(f"User {user_id} nicht gefunden.")
        row.kb_user_id = kb_user_id
        row.kb_token = kb_token
        row.kb_token_expires_at = kb_token_expires_at
        row.updated_at = datetime.now(UTC)
        self._session.commit()
        self._session.refresh(row)
        return row


class CredentialRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, user_id: int, kind: str) -> CredentialRow | None:
        return self._session.exec(
            select(CredentialRow)
            .where(CredentialRow.user_id == user_id)
            .where(CredentialRow.kind == kind)
        ).first()

    def upsert(self, *, user_id: int, kind: str, encrypted_value: bytes) -> CredentialRow:
        row = self.get(user_id=user_id, kind=kind)
        if row is None:
            row = CredentialRow(user_id=user_id, kind=kind, encrypted_value=encrypted_value)
            self._session.add(row)
        else:
            row.encrypted_value = encrypted_value
            row.verified_at = datetime.now(UTC)
        self._session.commit()
        self._session.refresh(row)
        return row


class SmtpRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: int) -> SmtpConfigRow | None:
        return self._session.exec(
            select(SmtpConfigRow).where(SmtpConfigRow.user_id == user_id)
        ).first()

    def upsert(self, row: SmtpConfigRow) -> SmtpConfigRow:
        existing = self.get(row.user_id)
        if existing is None:
            self._session.add(row)
            self._session.commit()
            self._session.refresh(row)
            return row
        existing.host = row.host
        existing.port = row.port
        existing.username = row.username
        existing.encrypted_password = row.encrypted_password
        existing.from_addr = row.from_addr
        existing.to_addr = row.to_addr
        existing.use_tls = row.use_tls
        existing.use_starttls = row.use_starttls
        existing.verified_at = row.verified_at
        self._session.commit()
        self._session.refresh(existing)
        return existing


class LeagueRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_user(self, user_id: int) -> list[LeagueRow]:
        return list(self._session.exec(select(LeagueRow).where(LeagueRow.user_id == user_id)))

    def active(self, user_id: int) -> LeagueRow | None:
        return self._session.exec(
            select(LeagueRow)
            .where(LeagueRow.user_id == user_id)
            .where(LeagueRow.is_active.is_(True))  # type: ignore[union-attr]
        ).first()

    def replace(self, *, user_id: int, leagues: list[tuple[str, str]]) -> list[LeagueRow]:
        """Ersetzt die Ligen-Menge eines Users vollständig, ohne aktiven Marker zu setzen."""
        existing = self.list_for_user(user_id)
        for row in existing:
            self._session.delete(row)
        self._session.flush()
        created: list[LeagueRow] = []
        for kb_league_id, name in leagues:
            row = LeagueRow(user_id=user_id, kb_league_id=kb_league_id, name=name)
            self._session.add(row)
            created.append(row)
        self._session.commit()
        for row in created:
            self._session.refresh(row)
        return created

    def set_active(self, *, user_id: int, kb_league_id: str) -> LeagueRow:
        rows = self.list_for_user(user_id)
        target: LeagueRow | None = None
        for row in rows:
            new_active = row.kb_league_id == kb_league_id
            if row.is_active != new_active:
                row.is_active = new_active
            if new_active:
                target = row
        self._session.commit()
        if target is None:
            raise LookupError(f"Liga {kb_league_id} nicht in DB")
        self._session.refresh(target)
        return target


class SettingsRepository:
    """Runtime-Overrides für Guardrails + Intervall (F-3, F-10).

    `get_or_default` legt bei Bedarf einen Default-Row an, damit Aufrufer nie
    mit `None` rechnen müssen.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: int) -> SettingsRow | None:
        return self._session.exec(select(SettingsRow).where(SettingsRow.user_id == user_id)).first()

    def get_or_default(self, user_id: int) -> SettingsRow:
        row = self.get(user_id)
        if row is not None:
            return row
        row = SettingsRow(user_id=user_id)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def upsert(self, row: SettingsRow) -> SettingsRow:
        existing = self.get(row.user_id)
        if existing is None:
            self._session.add(row)
            self._session.commit()
            self._session.refresh(row)
            return row
        existing.interval_min = row.interval_min
        existing.dry_run = row.dry_run
        existing.max_trade_pct = row.max_trade_pct
        existing.min_cash_reserve = row.min_cash_reserve
        existing.min_action_score = row.min_action_score
        existing.blacklist = row.blacklist
        existing.digest_enabled = row.digest_enabled
        existing.digest_hour = row.digest_hour
        self._session.commit()
        self._session.refresh(existing)
        return existing


class TradeLogRepository:
    """Persistiert jede Tick-Entscheidung — HOLD inklusive (§ 7 Nachvollziehbarkeit)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, row: TradeLogRow) -> TradeLogRow:
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def list_recent(self, *, user_id: int, limit: int = 50) -> list[TradeLogRow]:
        stmt = (
            select(TradeLogRow)
            .where(TradeLogRow.user_id == user_id)
            .order_by(TradeLogRow.ts.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )
        return list(self._session.exec(stmt))

    def latest(self, user_id: int) -> TradeLogRow | None:
        rows = self.list_recent(user_id=user_id, limit=1)
        return rows[0] if rows else None

    def last_executed_buys(self, user_id: int) -> dict[str, TradeLogRow]:
        """Letzter ausgeführter BUY je player_id — Basis für PROFIT-Exit.

        Wir liefern das gesamte `TradeLogRow`, damit der Aufrufer sowohl
        `context["intent"]` als auch `price` (Kaufpreis) rausziehen kann.
        Spieler, die inzwischen wieder verkauft wurden, filtern wir absichtlich
        nicht raus — die Anwendungsschicht schneidet die Menge mit dem aktuellen
        Squad, wodurch verkaufte Spieler ohnehin nicht mehr benutzt werden.
        """
        stmt = (
            select(TradeLogRow)
            .where(TradeLogRow.user_id == user_id)
            .where(TradeLogRow.action == "BUY")
            .where(TradeLogRow.executed.is_(True))  # type: ignore[union-attr]
            .order_by(TradeLogRow.ts.desc())  # type: ignore[attr-defined]
        )
        rows = self._session.exec(stmt)
        out: dict[str, TradeLogRow] = {}
        for row in rows:
            if row.player_id is None:
                continue
            out.setdefault(row.player_id, row)
        return out

    def last_listing_ts_by_player(self, user_id: int) -> dict[str, datetime]:
        """Letzter ausgeführter LIST_ON_MARKET je player_id — Basis für Stale-Fallback.

        Der Aufrufer schneidet die Menge mit den aktuell tatsächlich auf dem
        Kickbase-Markt liegenden eigenen Spielern; alte Log-Einträge zu
        Spielern, die längst wieder aus dem Markt sind, stören dabei nicht.
        """
        stmt = (
            select(TradeLogRow)
            .where(TradeLogRow.user_id == user_id)
            .where(TradeLogRow.action == "LIST_ON_MARKET")
            .where(TradeLogRow.executed.is_(True))  # type: ignore[union-attr]
            .order_by(TradeLogRow.ts.desc())  # type: ignore[attr-defined]
        )
        rows = self._session.exec(stmt)
        out: dict[str, datetime] = {}
        for row in rows:
            if row.player_id is None:
                continue
            out.setdefault(row.player_id, row.ts)
        return out

    def list_pending_holds(self, user_id: int) -> list[TradeLogRow]:
        """HOLD-Ticks, die noch in keinem Digest gemeldet wurden (F-9).

        `notified_at IS NULL` reicht als Filter: der Tick-UseCase setzt
        `notified_at` nur bei ausgeführten Non-HOLD-Aktionen — HOLD-Rows
        bleiben unmarkiert, bis der Digest sie einsammelt.
        """
        stmt = (
            select(TradeLogRow)
            .where(TradeLogRow.user_id == user_id)
            .where(TradeLogRow.action == "HOLD")
            .where(TradeLogRow.notified_at.is_(None))  # type: ignore[union-attr]
            .order_by(TradeLogRow.ts.asc())  # type: ignore[attr-defined]
        )
        return list(self._session.exec(stmt))

    def mark_notified(self, rows: list[TradeLogRow], *, ts: datetime) -> None:
        for row in rows:
            row.notified_at = ts
        if rows:
            self._session.commit()
