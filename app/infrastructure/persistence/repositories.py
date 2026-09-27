"""Repositories kapseln SQLModel-Queries für die Application-Schicht.

Sie liefern **Rohzeilen** — Entschlüsselung von Cipher-Text und Mapping auf
Domain-Objekte macht die Application-Schicht (SetupService), damit die
Persistenz keine Krypto-Verantwortung trägt.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, func
from sqlmodel import Session, select

from app.domain.models import (
    CompetitionContext,
    Fixture,
    MarketValuePoint,
    MatchdayPerformance,
    PlayerPerformance,
    TeamStanding,
)
from app.infrastructure.persistence.models import (
    CompetitionContextCacheRow,
    CredentialRow,
    LeagueRow,
    MarketMetaRow,
    MarketValueCacheRow,
    PlayerPerformanceCacheRow,
    SettingsRow,
    SmtpConfigRow,
    TradeLogRow,
    UserRow,
)


def _as_utc(value: datetime) -> datetime:
    """SQLite gibt naive Datetimes zurück — sie sind per Konvention UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


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

    def list_recent(
        self, *, user_id: int, limit: int = 50, include_superseded: bool = True
    ) -> list[TradeLogRow]:
        """Die jüngsten Zeilen, absteigend nach Zeit.

        `include_superseded=True` (Default) ist die Historie-Sicht fürs
        Dashboard: sie zeigt auch, was der Abgleich als überholt markiert hat —
        dass ein Kauf stattfand, bleibt wahr. Der Tick setzt `False` und sieht
        damit nur, worauf er sich noch stützen darf.
        """
        stmt = select(TradeLogRow).where(TradeLogRow.user_id == user_id)
        if not include_superseded:
            stmt = stmt.where(TradeLogRow.superseded_at.is_(None))  # type: ignore[union-attr]
        stmt = stmt.order_by(TradeLogRow.ts.desc()).limit(limit)  # type: ignore[attr-defined]
        return list(self._session.exec(stmt))

    def latest(self, user_id: int) -> TradeLogRow | None:
        rows = self.list_recent(user_id=user_id, limit=1)
        return rows[0] if rows else None

    def list_by_action(self, *, user_id: int, action: str, limit: int = 60) -> list[TradeLogRow]:
        """Die letzten Zeilen **einer** Aktionsart, absteigend nach Zeit.

        Für den Bonus-Job (P2-15): er braucht sowohl „habe ich heute schon
        gesammelt?" als auch die Streak über die Vortage, und beides aus
        `list_recent` zu filtern hiesse, das Limit gegen einen aktiven
        Handelstag zu verlieren — 20 Trades an einem Tag würden die
        Bonus-Historie aus dem Fenster schieben.

        Überholte Zeilen zählen hier **mit**, anders als bei Käufen und
        Listings. Der Unterschied ist die Rolle: die BONUS-Zeile ist kein
        Weltbild, sondern eine Tagessperre gegen einen zweiten Abruf. Ein
        Gedächtnis-Reset darf sie nicht aufheben — sonst löst ausgerechnet
        das Aufräumen den Doppelabruf aus, den die Sperre verhindern soll.
        """
        stmt = (
            select(TradeLogRow)
            .where(TradeLogRow.user_id == user_id)
            .where(TradeLogRow.action == action)
            .order_by(TradeLogRow.ts.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )
        return list(self._session.exec(stmt))

    def last_executed_buys(self, user_id: int) -> dict[str, TradeLogRow]:
        """Letzter ausgeführter BUY je player_id — Basis für PROFIT-Exit.

        Wir liefern das gesamte `TradeLogRow`, damit der Aufrufer sowohl
        `context["intent"]` als auch `price` (Kaufpreis) rausziehen kann.
        Spieler, die inzwischen wieder verkauft wurden, filtern wir absichtlich
        nicht raus — die Anwendungsschicht schneidet die Menge mit dem aktuellen
        Squad, wodurch verkaufte Spieler ohnehin nicht mehr benutzt werden.

        Überholte Zeilen (`superseded_at`) bleiben draußen: sie beschreiben
        einen Kauf, den die Kickbase-Wirklichkeit widerlegt hat.
        """
        stmt = (
            select(TradeLogRow)
            .where(TradeLogRow.user_id == user_id)
            .where(TradeLogRow.action == "BUY")
            .where(TradeLogRow.executed.is_(True))  # type: ignore[union-attr]
            .where(TradeLogRow.superseded_at.is_(None))  # type: ignore[union-attr]
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
        Überholte Zeilen bleiben wie bei den Käufen draußen.
        """
        stmt = (
            select(TradeLogRow)
            .where(TradeLogRow.user_id == user_id)
            .where(TradeLogRow.action == "LIST_ON_MARKET")
            .where(TradeLogRow.executed.is_(True))  # type: ignore[union-attr]
            .where(TradeLogRow.superseded_at.is_(None))  # type: ignore[union-attr]
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

    def mark_superseded(self, rows: Iterable[TradeLogRow], *, ts: datetime) -> int:
        """Markiert Zeilen als von der Realität überholt. Gibt die Anzahl zurück.

        Bereits markierte Zeilen behalten ihren ursprünglichen Zeitstempel —
        wann etwas ungültig wurde, ist eine Tatsache und kein Zählerstand.
        """
        touched = 0
        for row in rows:
            if row.superseded_at is not None:
                continue
            row.superseded_at = ts
            touched += 1
        if touched:
            self._session.commit()
        return touched

    def supersede_all(self, user_id: int, *, ts: datetime) -> int:
        """Erklärt das gesamte Gedächtnis für ungültig, ohne Historie zu löschen.

        Das ist die schonende Hälfte von „Gedächtnis löschen": der Bot leitet
        aus keiner Zeile mehr etwas ab, das Dashboard zeigt trotzdem weiter,
        was passiert ist. Wer die Zeilen wirklich loswerden will, nimmt
        `delete_all`.
        """
        stmt = (
            select(TradeLogRow)
            .where(TradeLogRow.user_id == user_id)
            .where(TradeLogRow.superseded_at.is_(None))  # type: ignore[union-attr]
        )
        return self.mark_superseded(self._session.exec(stmt), ts=ts)

    def delete_all(self, user_id: int) -> int:
        """Löscht die gesamte Historie des Nutzers. Gibt die Anzahl zurück."""
        count = self.count(user_id)
        self._session.exec(delete(TradeLogRow).where(TradeLogRow.user_id == user_id))  # type: ignore[call-overload]
        self._session.commit()
        return count

    def list_all(self, user_id: int) -> list[TradeLogRow]:
        """Die komplette Historie, älteste zuerst — für den Export."""
        stmt = (
            select(TradeLogRow).where(TradeLogRow.user_id == user_id).order_by(TradeLogRow.ts.asc())  # type: ignore[attr-defined]
        )
        return list(self._session.exec(stmt))

    def live_by_action(self, *, user_id: int, action: str) -> list[TradeLogRow]:
        """Ausgeführte, noch gültige Zeilen einer Aktionsart — Basis des Abgleichs."""
        stmt = (
            select(TradeLogRow)
            .where(TradeLogRow.user_id == user_id)
            .where(TradeLogRow.action == action)
            .where(TradeLogRow.executed.is_(True))  # type: ignore[union-attr]
            .where(TradeLogRow.superseded_at.is_(None))  # type: ignore[union-attr]
            .order_by(TradeLogRow.ts.desc())  # type: ignore[attr-defined]
        )
        return list(self._session.exec(stmt))

    def count(self, user_id: int) -> int:
        return _scalar_count(
            self._session,
            select(func.count()).select_from(TradeLogRow).where(TradeLogRow.user_id == user_id),
        )

    def stats(self, user_id: int) -> TradeLogStats:
        """Kennzahlen für die Datenverwaltung — ein Query je Zahl, alle klein."""
        base = select(func.count()).select_from(TradeLogRow).where(TradeLogRow.user_id == user_id)
        total = _scalar_count(self._session, base)
        executed = _scalar_count(self._session, base.where(TradeLogRow.executed.is_(True)))  # type: ignore[union-attr]
        superseded = _scalar_count(
            self._session,
            base.where(TradeLogRow.superseded_at.is_not(None)),  # type: ignore[union-attr]
        )
        span = self._session.exec(
            select(func.min(TradeLogRow.ts), func.max(TradeLogRow.ts)).where(
                TradeLogRow.user_id == user_id
            )
        ).first()
        oldest, newest = (span[0], span[1]) if span is not None else (None, None)
        return TradeLogStats(
            total=total,
            executed=executed,
            superseded=superseded,
            oldest=_as_utc(oldest) if oldest is not None else None,
            newest=_as_utc(newest) if newest is not None else None,
        )


@dataclass(frozen=True, slots=True)
class TradeLogStats:
    """Was in der Historie steht — für die Anzeige unter `/daten`."""

    total: int
    executed: int
    superseded: int
    oldest: datetime | None
    newest: datetime | None


@dataclass(frozen=True, slots=True)
class CacheStats:
    """Füllstand und Haltbarkeit eines Caches.

    `fresh` zählt die Zeilen, die zum Messzeitpunkt noch gültig sind. Die
    Differenz zu `rows` sind Zeilen, die beim nächsten Zugriff ohnehin neu
    geholt würden — sie kosten nichts als Plattenplatz, deshalb räumt sie
    niemand automatisch weg.
    """

    rows: int
    fresh: int
    oldest_fetch: datetime | None
    newest_fetch: datetime | None


def _scalar_count(session: Session, stmt: object) -> int:
    value = session.exec(stmt).one()  # type: ignore[call-overload]
    # SQLAlchemy liefert je nach Statement einen Skalar oder ein 1-Tupel.
    if isinstance(value, tuple):
        value = value[0]
    return int(value)


def _cache_stats(session: Session, model: type, *, now: datetime) -> CacheStats:
    """Gemeinsame Zählung für alle vier Caches — sie haben dieselben Zeitfelder."""
    rows = _scalar_count(session, select(func.count()).select_from(model))
    fresh = _scalar_count(
        session, select(func.count()).select_from(model).where(model.valid_until > now)
    )
    span = session.exec(select(func.min(model.fetched_at), func.max(model.fetched_at))).first()
    oldest, newest = (span[0], span[1]) if span is not None else (None, None)
    return CacheStats(
        rows=rows,
        fresh=fresh,
        oldest_fetch=_as_utc(oldest) if oldest is not None else None,
        newest_fetch=_as_utc(newest) if newest is not None else None,
    )


def _clear_table(session: Session, model: type) -> int:
    """Leert eine Cache-Tabelle vollständig. Gibt die Zahl der Zeilen zurück."""
    count = _scalar_count(session, select(func.count()).select_from(model))
    session.exec(delete(model))  # type: ignore[call-overload]
    session.commit()
    return count


class MarketValueCacheRepository:
    """Tages-Cache für Marktwert-Historien — implementiert `MarketValueCache`.

    Gültigkeitsgrenze ist `mvud`, der nächste Marktwert-Update-Zeitpunkt, nicht
    eine Zeitspanne. Ein abgelaufener Eintrag wird beim nächsten Schreiben
    überschrieben statt gelöscht: pro Liga und Spieler gibt es nur eine Zeile,
    die Tabelle wächst also mit der Zahl je gesehener Spieler und nicht mit der
    Zahl der Ticks.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_many(
        self, league_id: str, player_ids: Sequence[str], *, now: datetime
    ) -> dict[str, list[MarketValuePoint]]:
        ids = list(player_ids)
        if not ids:
            return {}
        stmt = (
            select(MarketValueCacheRow)
            .where(MarketValueCacheRow.league_id == league_id)
            .where(MarketValueCacheRow.player_id.in_(ids))  # type: ignore[attr-defined]
        )
        out: dict[str, list[MarketValuePoint]] = {}
        for row in self._session.exec(stmt):
            if _as_utc(row.valid_until) <= now:
                continue  # abgelaufen — der Aufrufer holt neu und überschreibt
            out[row.player_id] = [
                MarketValuePoint(day=datetime.fromisoformat(day), value=Decimal(str(value)))
                for day, value in row.points
            ]
        return out

    def put(
        self,
        league_id: str,
        player_id: str,
        points: Sequence[MarketValuePoint],
        *,
        valid_until: datetime,
    ) -> None:
        serialised = [[p.day.isoformat(), str(p.value)] for p in points]
        stmt = (
            select(MarketValueCacheRow)
            .where(MarketValueCacheRow.league_id == league_id)
            .where(MarketValueCacheRow.player_id == player_id)
        )
        row = self._session.exec(stmt).first()
        if row is None:
            row = MarketValueCacheRow(
                league_id=league_id, player_id=player_id, valid_until=valid_until
            )
            self._session.add(row)
        row.points = serialised
        row.fetched_at = datetime.now(UTC)
        row.valid_until = valid_until
        self._session.commit()

    def stats(self, *, now: datetime) -> CacheStats:
        return _cache_stats(self._session, MarketValueCacheRow, now=now)

    def clear(self) -> int:
        return _clear_table(self._session, MarketValueCacheRow)


class PlayerPerformanceCacheRepository:
    """Cache für Spieltags-Historien — implementiert `PlayerPerformanceCache`.

    Aufbau wie `MarketValueCacheRepository`; getrennt gehalten, weil die
    Haltbarkeit eine andere Frage beantwortet (Spieltag statt Marktwert-Update)
    und die Serialisierung eine andere Form hat. Eine gemeinsame generische
    Tabelle würde beides hinter `Any` verstecken.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_many(
        self, league_id: str, player_ids: Sequence[str], *, now: datetime
    ) -> dict[str, PlayerPerformance]:
        ids = list(player_ids)
        if not ids:
            return {}
        stmt = (
            select(PlayerPerformanceCacheRow)
            .where(PlayerPerformanceCacheRow.league_id == league_id)
            .where(PlayerPerformanceCacheRow.player_id.in_(ids))  # type: ignore[attr-defined]
        )
        out: dict[str, PlayerPerformance] = {}
        for row in self._session.exec(stmt):
            if _as_utc(row.valid_until) <= now:
                continue
            out[row.player_id] = PlayerPerformance(
                player_id=row.player_id,
                season=row.season,
                matchdays=tuple(
                    MatchdayPerformance(
                        day=int(day),
                        points=int(points),
                        minutes=int(minutes),
                        was_in_starting_xi=bool(started),
                    )
                    for day, points, minutes, started in row.matchdays
                ),
            )
        return out

    def put(
        self,
        league_id: str,
        player_id: str,
        performance: PlayerPerformance,
        *,
        valid_until: datetime,
    ) -> None:
        serialised = [
            [m.day, m.points, m.minutes, m.was_in_starting_xi] for m in performance.matchdays
        ]
        stmt = (
            select(PlayerPerformanceCacheRow)
            .where(PlayerPerformanceCacheRow.league_id == league_id)
            .where(PlayerPerformanceCacheRow.player_id == player_id)
        )
        row = self._session.exec(stmt).first()
        if row is None:
            row = PlayerPerformanceCacheRow(
                league_id=league_id, player_id=player_id, valid_until=valid_until
            )
            self._session.add(row)
        row.season = performance.season
        row.matchdays = serialised
        row.fetched_at = datetime.now(UTC)
        row.valid_until = valid_until
        self._session.commit()

    def stats(self, *, now: datetime) -> CacheStats:
        return _cache_stats(self._session, PlayerPerformanceCacheRow, now=now)

    def clear(self) -> int:
        return _clear_table(self._session, PlayerPerformanceCacheRow)


class CompetitionContextCacheRepository:
    """Tages-Cache für Tabelle + Spielplan — implementiert `CompetitionContextCache`.

    Eine Zeile je Wettbewerb, und sie wird überschrieben statt gelöscht: die
    Tabelle wächst damit nicht mit der Zahl der Ticks, sondern bleibt bei
    einer Zeile. Abgelaufene Einträge liefert `get` nicht aus — wie in den
    beiden Spieler-Caches wird der Ablauf gelesen, nicht aufgeräumt.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, competition_id: str, *, now: datetime) -> CompetitionContext | None:
        row = self._row(competition_id)
        if row is None or _as_utc(row.valid_until) <= now:
            return None
        return CompetitionContext(
            standings=tuple(
                TeamStanding(
                    team_id=str(team_id),
                    team_name=str(name),
                    rank=int(rank),
                    points=int(points),
                    matches_played=int(matches),
                    goal_difference=int(goal_diff),
                )
                for team_id, name, rank, points, matches, goal_diff in row.standings
            ),
            fixtures=tuple(
                Fixture(
                    matchday=int(matchday),
                    kickoff=datetime.fromisoformat(kickoff),
                    home_team_id=str(home),
                    away_team_id=str(away),
                    is_finished=bool(finished),
                )
                for matchday, kickoff, home, away, finished in row.fixtures
            ),
        )

    def put(
        self, competition_id: str, context: CompetitionContext, *, valid_until: datetime
    ) -> None:
        row = self._row(competition_id)
        if row is None:
            row = CompetitionContextCacheRow(competition_id=competition_id, valid_until=valid_until)
            self._session.add(row)
        row.standings = [
            [s.team_id, s.team_name, s.rank, s.points, s.matches_played, s.goal_difference]
            for s in context.standings
        ]
        row.fixtures = [
            [f.matchday, f.kickoff.isoformat(), f.home_team_id, f.away_team_id, f.is_finished]
            for f in context.fixtures
        ]
        row.fetched_at = datetime.now(UTC)
        row.valid_until = valid_until
        self._session.commit()

    def stats(self, *, now: datetime) -> CacheStats:
        return _cache_stats(self._session, CompetitionContextCacheRow, now=now)

    def clear(self) -> int:
        return _clear_table(self._session, CompetitionContextCacheRow)

    def _row(self, competition_id: str) -> CompetitionContextCacheRow | None:
        stmt = select(CompetitionContextCacheRow).where(
            CompetitionContextCacheRow.competition_id == competition_id
        )
        return self._session.exec(stmt).first()


class MarketMetaRepository:
    """Liest und schreibt die Markt-Uhren für den Scheduler (P1-10)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, league_id: str) -> MarketMetaRow | None:
        stmt = select(MarketMetaRow).where(MarketMetaRow.league_id == league_id)
        return self._session.exec(stmt).first()

    def latest(self) -> MarketMetaRow | None:
        """Irgendeine Zeile — die App ist Single-League (siehe `PROJEKT.md`).

        Der Scheduler kennt beim Start noch keine Liga-ID: die steht hinter dem
        Setup, das zu diesem Zeitpunkt noch nicht gelaufen sein muss.
        """
        stmt = select(MarketMetaRow).order_by(MarketMetaRow.updated_at.desc())  # type: ignore[attr-defined]
        return self._session.exec(stmt).first()

    def upsert(
        self,
        league_id: str,
        *,
        next_matchday_start: datetime | None,
        mv_update_at: datetime | None,
    ) -> MarketMetaRow:
        row = self.get(league_id)
        if row is None:
            row = MarketMetaRow(league_id=league_id)
            self._session.add(row)
        row.next_matchday_start = next_matchday_start
        row.mv_update_at = mv_update_at
        row.updated_at = datetime.now(UTC)
        self._session.commit()
        self._session.refresh(row)
        return row

    def list_all(self) -> list[MarketMetaRow]:
        return list(self._session.exec(select(MarketMetaRow)))

    def clear(self) -> int:
        """Leert die Markt-Uhren.

        Anders als die drei Caches verliert das hier kurz eine Fähigkeit: bis
        zum nächsten Tick kennt der Scheduler den Anpfiff nicht und legt keine
        Deadline-Fenster. Der erste Tick nach dem Leeren holt ihn zurück.
        """
        return _clear_table(self._session, MarketMetaRow)
