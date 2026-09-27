"""Datenverwaltung — Bestand zeigen, abgleichen, vergessen, exportieren.

**Das Problem, das diese Datei löst.** Der Bot hält zwei Sorten von Wissen:
was er gerade bei Kickbase abgefragt hat, und was er sich selbst gemerkt hat.
Die zweite Sorte steht im `trade_log`, und sie ist die einzige Quelle für drei
Dinge, die Kickbase nicht zurückliefert:

- **laufende eigene Gebote** — das Gebots-Array im Market-Payload ist bis heute
  unbenannt (Plan §8/F1), der Bot rekonstruiert sie aus seinen eigenen BUYs;
- **wann ein eigenes Listing begonnen hat** — Basis des Stale-Fallbacks;
- **warum** ein Spieler gekauft wurde (der Intent) — der Einstandspreis kommt
  seit P1-6 von Kickbase selbst, die Absicht nicht.

Wird in der Kickbase-App von Hand gehandelt, weiß der Bot davon nichts. Ein
zurückgezogenes Gebot gilt ihm weiter als laufend und bindet Budget, das längst
frei ist; ein neu aufgesetztes Listing erbt den Zeitstempel des alten. Beides
führt zu Entscheidungen auf einem Stand, den es nicht mehr gibt.

**Die Antwort ist der Abgleich**, nicht das Löschen: `reconcile` hält das
Gedächtnis gegen Kader und Markt und markiert jede Zeile, die die Wirklichkeit
widerlegt, als überholt (`superseded_at`). Die Historie bleibt dabei vollständig
lesbar — dass ein Kauf stattfand, hört ja nicht auf, wahr zu sein. Nur ableiten
darf der Bot daraus nichts mehr.

Das Löschen gibt es trotzdem, in zwei Stufen — vergessen (alles überholt) und
endgültig löschen (Zeilen weg). Sie sind die grobe Antwort für den Fall, dass
der Abgleich nicht reicht.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session

from app.domain.exceptions import KickbaseError
from app.domain.gateways import KickbaseGateway
from app.infrastructure.persistence.models import LeagueRow, TradeLogRow, UserRow
from app.infrastructure.persistence.repositories import (
    CacheStats,
    CompetitionContextCacheRepository,
    CredentialRepository,
    LeagueRepository,
    MarketMetaRepository,
    MarketValueCacheRepository,
    PlayerPerformanceCacheRepository,
    SettingsRepository,
    SmtpRepository,
    TradeLogRepository,
    TradeLogStats,
    UserRepository,
)

_log = logging.getLogger(__name__)

# Wie viele Zeichen des lokalen Teils einer E-Mail-Adresse sichtbar bleiben.
_MASK_KEEP = 2


# -- Inventar ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccountInfo:
    """Was an Zugängen hinterlegt ist — ohne einen einzigen Geheimniswert."""

    email_masked: str
    kb_user_id: str
    league_name: str | None
    league_id: str | None
    anthropic_verified_at: datetime | None
    smtp_host: str | None
    smtp_verified_at: datetime | None


@dataclass(frozen=True, slots=True)
class CacheInfo:
    """Ein Cache-Block, so wie ihn die Seite zeigt."""

    key: str
    title: str
    description: str
    stats: CacheStats


@dataclass(frozen=True, slots=True)
class DataInventory:
    """Vollständige Antwort auf „was liegt eigentlich gespeichert herum?"."""

    collected_at: datetime
    account: AccountInfo | None
    caches: tuple[CacheInfo, ...]
    market_meta_rows: int
    trade_log: TradeLogStats

    @property
    def cached_rows_total(self) -> int:
        return sum(c.stats.rows for c in self.caches) + self.market_meta_rows


def collect_inventory(session: Session, *, now: datetime | None = None) -> DataInventory:
    """Liest den Bestand aller Tabellen zusammen. Reiner Lesevorgang."""
    moment = now or datetime.now(UTC)
    user = UserRepository(session).get_singleton()

    caches = (
        CacheInfo(
            key="market_values",
            title="Marktwert-Historien",
            description=(
                "Je Spieler ein Jahr Marktwert-Verlauf. Gültig bis zum nächsten "
                "Kickbase-Marktwert-Update (täglich 22:00)."
            ),
            stats=MarketValueCacheRepository(session).stats(now=moment),
        ),
        CacheInfo(
            key="performance",
            title="Spieltags-Leistungen",
            description=(
                "Punkte, Minuten und Startelf-Einsätze je Spieltag. Gültig bis zum "
                "nächsten Anpfiff — während ein Spieltag läuft, greift der Cache nicht."
            ),
            stats=PlayerPerformanceCacheRepository(session).stats(now=moment),
        ),
        CacheInfo(
            key="competition",
            title="Tabelle & Spielplan",
            description="Bundesliga-Tabelle und alle Paarungen. Eine Zeile, täglich erneuert.",
            stats=CompetitionContextCacheRepository(session).stats(now=moment),
        ),
    )

    return DataInventory(
        collected_at=moment,
        account=_account_info(session, user),
        caches=caches,
        market_meta_rows=len(MarketMetaRepository(session).list_all()),
        trade_log=(
            TradeLogRepository(session).stats(user.id)
            if user is not None and user.id is not None
            else TradeLogStats(total=0, executed=0, superseded=0, oldest=None, newest=None)
        ),
    )


def _account_info(session: Session, user: UserRow | None) -> AccountInfo | None:
    if user is None or user.id is None:
        return None
    league: LeagueRow | None = LeagueRepository(session).active(user.id)
    credential = CredentialRepository(session).get(user_id=user.id, kind="anthropic")
    smtp = SmtpRepository(session).get(user.id)
    return AccountInfo(
        email_masked=_mask_email(user.email),
        kb_user_id=user.kb_user_id,
        league_name=league.name if league else None,
        league_id=league.kb_league_id if league else None,
        anthropic_verified_at=credential.verified_at if credential else None,
        smtp_host=smtp.host if smtp else None,
        smtp_verified_at=smtp.verified_at if smtp else None,
    )


def _mask_email(email: str) -> str:
    """`lenn.muster@example.com` → `le…@example.com`.

    Die Adresse ist die Kontokennung bei Kickbase. Sie steht hier nur, damit
    erkennbar ist, *welches* Konto hinterlegt ist — dafür reichen zwei Zeichen,
    und ein versehentlich weitergegebener Export verrät sie dann nicht.
    """
    local, _, domain = email.partition("@")
    if not domain:
        return "—"
    head = local[:_MASK_KEEP] if len(local) > _MASK_KEEP else local[:1]
    return f"{head}…@{domain}"


# -- Caches leeren -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class CacheClearResult:
    market_values: int
    performance: int
    competition: int
    market_meta: int

    @property
    def total(self) -> int:
        return self.market_values + self.performance + self.competition + self.market_meta


def clear_caches(session: Session, *, include_market_meta: bool = False) -> CacheClearResult:
    """Wirft die zwischengespeicherten Kickbase-Antworten weg.

    Verlustfrei: jede gelöschte Zeile ist eine Kopie von etwas, das Kickbase
    weiterhin liefert. Der nächste Tick holt sie neu — und zahlt dafür einmalig
    mit HTTP-Requests, weshalb der Knopf kein Automatismus ist.

    `include_market_meta` nimmt zusätzlich die beiden Markt-Uhren mit. Die sind
    kein Cache im engeren Sinn: ohne sie legt der Scheduler bis zum nächsten
    Tick keine Deadline-Fenster. Deshalb standardmäßig aus.
    """
    result = CacheClearResult(
        market_values=MarketValueCacheRepository(session).clear(),
        performance=PlayerPerformanceCacheRepository(session).clear(),
        competition=CompetitionContextCacheRepository(session).clear(),
        market_meta=MarketMetaRepository(session).clear() if include_market_meta else 0,
    )
    _log.info(
        "Caches geleert: %d Marktwerte, %d Leistungen, %d Wettbewerb, %d Markt-Uhren.",
        result.market_values,
        result.performance,
        result.competition,
        result.market_meta,
    )
    return result


# -- Gedächtnis vergessen / Historie löschen ---------------------------


def forget_memory(session: Session, user_id: int, *, now: datetime | None = None) -> int:
    """Erklärt jede Gedächtnis-Zeile für überholt. Gibt die Anzahl zurück.

    Die Historie bleibt stehen und im Dashboard sichtbar; der Bot stützt sich
    ab sofort nur noch auf das, was er live von Kickbase holt. Praktisch heißt
    das: keine rekonstruierten offenen Gebote, keine Listing-Zeitstempel, keine
    Intents mehr — bis der Bot sich neue macht.
    """
    moment = now or datetime.now(UTC)
    count = TradeLogRepository(session).supersede_all(user_id, ts=moment)
    _log.info("Gedächtnis zurückgesetzt: %d Zeilen als überholt markiert.", count)
    return count


def purge_history(session: Session, user_id: int) -> int:
    """Löscht die Historie endgültig. Nicht umkehrbar."""
    count = TradeLogRepository(session).delete_all(user_id)
    _log.warning("Historie gelöscht: %d Zeilen entfernt.", count)
    return count


# -- Abgleich mit Kickbase ---------------------------------------------


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """Was der Abgleich vorgefunden und was er daraus gemacht hat."""

    checked_at: datetime
    squad_size: int
    market_size: int
    own_listings: int
    superseded_buys: int
    superseded_listings: int
    squad_without_intent: int
    caches_cleared: CacheClearResult | None
    notes: tuple[str, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def superseded_total(self) -> int:
        return self.superseded_buys + self.superseded_listings


async def reconcile(
    session: Session,
    kickbase: KickbaseGateway,
    *,
    user_id: int,
    manager_id: str,
    league_id: str,
    clear_cache: bool = True,
    now: datetime | None = None,
) -> ReconcileReport:
    """Hält das Gedächtnis gegen den echten Kader und den echten Markt.

    Drei Befunde führen dazu, dass eine Zeile als überholt markiert wird:

    1. **Gekauft, aber der Spieler ist weder im Kader noch im Markt.** Dann ist
       das Gebot entschieden und verloren — oder der Spieler wurde von Hand
       verkauft. In beiden Fällen ist die Zeile als „laufendes Gebot" falsch.
    2. **Gekauft, bevor das aktuelle Listing begann.** Der Spieler war
       zwischenzeitlich weg und ist neu gelistet; das alte Gebot ist erloschen,
       bindet im Weltbild des Bots aber weiter Budget.
    3. **Gelistet, liegt aber nicht mehr als eigenes Listing im Markt.** Das
       Listing wurde verkauft oder zurückgezogen.

    Was der Abgleich **nicht** kann: einen Kauf ergänzen, den der Bot nie
    gemacht hat. Kickbase liefert den Einstandspreis solcher Spieler mit
    (`mvgl`), die **Absicht** dahinter aber nicht — `squad_without_intent`
    zählt sie, damit im Bericht steht, wovon der Bot nichts weiß.
    """
    moment = now or datetime.now(UTC)
    trades = TradeLogRepository(session)
    notes: list[str] = []

    # Nur BUY und LIST_ON_MARKET werden geprüft. Alles andere im `trade_log` ist
    # Protokoll: HOLD, ERROR, BONUS und SET_LINEUP behaupten nichts über Besitz
    # oder laufende Gebote, es gibt dort also nichts zu widerlegen.

    try:
        squad = await kickbase.get_squad(league_id, manager_id)
        snapshot = await kickbase.get_market(league_id)
    except KickbaseError as exc:
        _log.warning("Abgleich fehlgeschlagen: %s", exc)
        return ReconcileReport(
            checked_at=moment,
            squad_size=0,
            market_size=0,
            own_listings=0,
            superseded_buys=0,
            superseded_listings=0,
            squad_without_intent=0,
            caches_cleared=None,
            error=str(exc),
        )

    squad_ids = {sp.player.id: sp for sp in squad.players}
    listed_at_by_player = {mp.player.id: mp.listed_at for mp in snapshot.players}
    own_listed_ids = {mp.player.id for mp in snapshot.players if mp.seller_id == manager_id}

    stale_buys = [
        row
        for row in trades.live_by_action(user_id=user_id, action="BUY")
        if row.player_id is not None
        and _buy_is_stale(row, squad_ids=squad_ids.keys(), listed_at_by_player=listed_at_by_player)
    ]
    stale_listings = [
        row
        for row in trades.live_by_action(user_id=user_id, action="LIST_ON_MARKET")
        if row.player_id is not None and row.player_id not in own_listed_ids
    ]

    superseded_buys = trades.mark_superseded(stale_buys, ts=moment)
    superseded_listings = trades.mark_superseded(stale_listings, ts=moment)

    known_intents = {
        pid for pid, row in trades.last_executed_buys(user_id).items() if row.context.get("intent")
    }
    without_intent = sum(1 for pid in squad_ids if pid not in known_intents)

    if superseded_buys:
        notes.append(
            f"{superseded_buys} Kauf-Einträge betrafen Spieler, die weder im Kader noch "
            "im Markt stehen — sie galten dem Bot bis eben als laufende Gebote."
        )
    if superseded_listings:
        notes.append(
            f"{superseded_listings} Verkaufs-Angebote liegen nicht mehr im Markt — "
            "verkauft oder zurückgezogen."
        )
    if not superseded_buys and not superseded_listings:
        notes.append("Das Gedächtnis deckt sich mit Kader und Markt — nichts zu korrigieren.")
    if without_intent:
        notes.append(
            f"{without_intent} von {len(squad_ids)} Kaderspielern hat der Bot nicht selbst "
            "gekauft. Den Einstandspreis liefert Kickbase, die Kaufabsicht nicht — für diese "
            "Spieler gibt es keine Gewinnmitnahme nach Plan, nur eine nach Lage."
        )

    cleared = clear_caches(session) if clear_cache else None

    _log.info(
        "Abgleich: %d Kaderspieler, %d Marktangebote, %d eigene Listings, "
        "%d Zeilen als überholt markiert.",
        len(squad_ids),
        len(snapshot.players),
        len(own_listed_ids),
        superseded_buys + superseded_listings,
    )

    return ReconcileReport(
        checked_at=moment,
        squad_size=len(squad_ids),
        market_size=len(snapshot.players),
        own_listings=len(own_listed_ids),
        superseded_buys=superseded_buys,
        superseded_listings=superseded_listings,
        squad_without_intent=without_intent,
        caches_cleared=cleared,
        notes=tuple(notes),
    )


def _buy_is_stale(
    row: TradeLogRow,
    *,
    squad_ids: Any,
    listed_at_by_player: dict[str, datetime | None],
) -> bool:
    """Ob ein geloggter Kauf nicht mehr zur Wirklichkeit passt — siehe `reconcile`."""
    player_id = row.player_id
    if player_id is None:
        return False
    if player_id in squad_ids:
        return False  # Spieler gehört uns: der Kauf ist durch, die Zeile stimmt.
    listed_at = listed_at_by_player.get(player_id, _NOT_IN_MARKET)
    if listed_at is _NOT_IN_MARKET:
        return True  # Weder Kader noch Markt: das Gebot ist entschieden.
    # Gebot vor dem Beginn des aktuellen Listings = es galt einem früheren.
    return isinstance(listed_at, datetime) and _as_utc(row.ts) < _as_utc(listed_at)


# Sentinel: „Spieler kommt im Markt gar nicht vor" ist etwas anderes als
# „Spieler liegt im Markt, aber ohne bekannten Listing-Beginn" (`None`).
_NOT_IN_MARKET: Any = object()


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# -- Export ------------------------------------------------------------


def build_export(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Der gesamte gespeicherte Bestand als JSON-fähiges Dict.

    Was **nicht** darin steht: Kickbase-Passwort, Kickbase-Token, Anthropic-Key,
    SMTP-Passwort. Die liegen verschlüsselt in der Datenbank und haben in einer
    Datei, die man weitergibt, nichts verloren — die E-Mail-Adresse steht
    maskiert drin, alles andere nur als „wann zuletzt geprüft".
    """
    moment = now or datetime.now(UTC)
    inventory = collect_inventory(session, now=moment)
    user = UserRepository(session).get_singleton()

    settings_block: dict[str, Any] | None = None
    history: list[dict[str, Any]] = []
    if user is not None and user.id is not None:
        row = SettingsRepository(session).get_or_default(user.id)
        settings_block = {
            "interval_min": row.interval_min,
            "dry_run": row.dry_run,
            "max_trade_pct": row.max_trade_pct,
            "min_cash_reserve": row.min_cash_reserve,
            "blacklist": list(row.blacklist),
            "digest_enabled": row.digest_enabled,
            "digest_hour": row.digest_hour,
        }
        history = [_export_row(r) for r in TradeLogRepository(session).list_all(user.id)]

    account = inventory.account
    return {
        "exported_at": moment.isoformat(),
        "account": (
            {
                "email_masked": account.email_masked,
                "kb_user_id": account.kb_user_id,
                "league_name": account.league_name,
                "league_id": account.league_id,
                "anthropic_verified_at": _iso(account.anthropic_verified_at),
                "smtp_host": account.smtp_host,
                "smtp_verified_at": _iso(account.smtp_verified_at),
            }
            if account is not None
            else None
        ),
        "settings": settings_block,
        "caches": {
            cache.key: {
                "title": cache.title,
                "rows": cache.stats.rows,
                "fresh": cache.stats.fresh,
                "oldest_fetch": _iso(cache.stats.oldest_fetch),
                "newest_fetch": _iso(cache.stats.newest_fetch),
            }
            for cache in inventory.caches
        },
        "market_meta_rows": inventory.market_meta_rows,
        "trade_log": {
            "total": inventory.trade_log.total,
            "executed": inventory.trade_log.executed,
            "superseded": inventory.trade_log.superseded,
            "entries": history,
        },
    }


def _export_row(row: TradeLogRow) -> dict[str, Any]:
    return {
        "ts": _iso(row.ts),
        "action": row.action,
        "player_id": row.player_id,
        "player_name": row.player_name,
        "price": row.price,
        "executed": row.executed,
        "reason_text": row.reason_text,
        "response_code": row.response_code,
        "notified_at": _iso(row.notified_at),
        "superseded_at": _iso(row.superseded_at),
        "context": row.context,
    }


def _iso(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None


__all__ = [
    "AccountInfo",
    "CacheClearResult",
    "CacheInfo",
    "DataInventory",
    "ReconcileReport",
    "build_export",
    "clear_caches",
    "collect_inventory",
    "forget_memory",
    "purge_history",
    "reconcile",
]
