"""CollectDailyBonusUseCase — der tägliche Login-Bonus (P2-15).

Eigener Cron-Job, **nicht** Teil des Entscheidungs-Ticks: der Bonus ist keine
Entscheidung, sondern eine Abholung. Im Tick würde er einen der wenigen
Aktions-Slots belegen und wäre an die Tick-Fenster gebunden, die sich am
Spieltag orientieren — der Kickbase-Tag wechselt aber um Mitternacht.

**Drei Sicherungen, weil dieser Call anders ist als alle anderen.**
`GET /v4/bonus/collect` ist ein GET, der wie ein Write wirkt. Er wurde bewusst
nie abgerufen (§3.4 des Plans), seine Antwortfelder sind unbekannt, und ob ein
zweiter Aufruf am selben Tag harmlos ist, steht nirgends:

1. **Kill-Switch** `KB_BONUS_COLLECT_ENABLED`, Default aus. Der erste scharfe
   Aufruf ist eine bewusste Handlung.
2. **`dry_run` gilt auch hier.** Im Dry-Run wird nichts abgerufen, aber eine
   Zeile geschrieben — so lässt sich der Job im Betrieb beobachten, bevor er
   etwas tut.
3. **Höchstens ein Versuch pro Kalendertag** (Europe/Berlin), geprüft am
   `trade_log`. Damit ist die offene Idempotenz-Frage entschärft: selbst wenn
   der Endpunkt bei doppeltem Aufruf etwas Unerwartetes tut, kommt es nicht
   dazu. Ein Container-Neustart um 9:05 löst keinen zweiten Call aus.

**Was der Bonus gebracht hat, wird am Kontostand gemessen**, nicht an der
Antwort: `budget` vor und nach dem Call. Die Rohantwort landet unverändert im
`trade_log` — der erste echte Lauf liefert damit die Feldnamen, aus denen
später ein DTO werden kann. Geratene Felder wären schlimmer als keine (§9).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlmodel import Session

from app.application.setup_state import read_setup_state
from app.domain.exceptions import KickbaseError
from app.domain.gateways import KickbaseGateway
from app.infrastructure.persistence.models import TradeLogRow
from app.infrastructure.persistence.repositories import (
    LeagueRepository,
    SettingsRepository,
    TradeLogRepository,
    UserRepository,
)

_log = logging.getLogger(__name__)

# Die Aktionsart im `trade_log`. Bewusst **kein** `TradeAction`-Mitglied: das
# Enum ist die Menge der Aktionen, die das Sprachmodell wählen darf, und der
# Bonus gehört nicht dazu. `_load_recent_actions` überspringt unbekannte
# Aktionen und hält ihn damit aus dem Prompt heraus — richtig so, denn der
# Effekt steht ohnehin im Kontostand.
BONUS_ACTION = "BONUS"

# Der Kickbase-Tag wechselt um Mitternacht deutscher Zeit; „heute schon
# gesammelt?" muss in derselben Zeitzone beantwortet werden, sonst sammelt der
# Job um 00:30 MESZ ein zweites Mal für den Vortag.
_BERLIN = ZoneInfo("Europe/Berlin")


@dataclass(frozen=True, slots=True)
class BonusOutcome:
    """Ergebnis eines Bonus-Laufs.

    `outcome` ist eines von: `collected`, `dry_run`, `already_today`,
    `disabled`, `skipped_setup`, `error`.
    """

    outcome: str
    # Differenz des Kontostands über den Call hinweg. `None`, wenn nicht
    # gemessen werden konnte (Kickbase-Fehler beim zweiten `/me`).
    amount: Decimal | None = None
    streak: int = 0
    error: str | None = None


class CollectDailyBonusUseCase:
    def __init__(
        self,
        *,
        session: Session,
        kickbase: KickbaseGateway,
        enabled: bool = False,
    ) -> None:
        self._session = session
        self._kickbase = kickbase
        self._enabled = enabled
        self._users = UserRepository(session)
        self._leagues = LeagueRepository(session)
        self._settings = SettingsRepository(session)
        self._trades = TradeLogRepository(session)

    async def run(self, *, now: datetime | None = None) -> BonusOutcome:
        now = now or datetime.now(UTC)
        state = read_setup_state(self._session)
        if not state.is_complete:
            return BonusOutcome(outcome="skipped_setup")

        user = self._users.get_singleton()
        assert user is not None and user.id is not None  # von state.is_complete garantiert

        history = self._trades.list_by_action(user_id=user.id, action=BONUS_ACTION)
        if _collected_on(history, now):
            return BonusOutcome(outcome="already_today", streak=_streak(history, now))

        if not self._enabled:
            # Kein Log-Eintrag: der Job läuft täglich, und eine Zeile pro Tag
            # nur um zu sagen „ist aus" würde das trade_log fluten.
            return BonusOutcome(outcome="disabled", streak=_streak(history, now))

        settings = self._settings.get_or_default(user.id)
        league = self._leagues.active(user.id)
        assert league is not None  # von state.is_complete garantiert

        if settings.dry_run:
            streak = _streak(history, now)
            self._log_row(user.id, outcome="dry_run", amount=None, streak=streak, response=None)
            return BonusOutcome(outcome="dry_run", streak=streak)

        try:
            before = await self._balance(league.kb_league_id)
            response = await self._kickbase.collect_daily_bonus()
            after = await self._balance(league.kb_league_id)
        except KickbaseError as exc:
            _log.warning("Bonus-Abholung fehlgeschlagen: %s", exc)
            self._log_row(
                user.id, outcome="error", amount=None, streak=0, response=None, error=str(exc)
            )
            return BonusOutcome(outcome="error", error=str(exc))

        amount = None if before is None or after is None else after - before
        streak = _streak(history, now) + 1
        self._log_row(
            user.id, outcome="collected", amount=amount, streak=streak, response=dict(response)
        )
        _log.info(
            "Login-Bonus abgeholt: %s € (Streak %d). Rohantwort-Felder: %s",
            amount if amount is not None else "unbekannt",
            streak,
            sorted(response),
        )
        return BonusOutcome(outcome="collected", amount=amount, streak=streak)

    async def _balance(self, league_id: str) -> Decimal | None:
        return (await self._kickbase.get_league_me(league_id)).budget

    def _log_row(
        self,
        user_id: int,
        *,
        outcome: str,
        amount: Decimal | None,
        streak: int,
        response: dict[str, Any] | None,
        error: str | None = None,
    ) -> None:
        self._trades.add(
            TradeLogRow(
                user_id=user_id,
                action=BONUS_ACTION,
                price=int(amount) if amount is not None else None,
                reason_text=f"Täglicher Login-Bonus ({outcome}), Streak {streak}",
                executed=outcome == "collected",
                context={
                    "outcome": outcome,
                    "streak": streak,
                    "error": error,
                    # Unverändert abgelegt: aus dieser Zeile kommen die
                    # Feldnamen, die §3.4 bisher offenlassen muss.
                    "response": response,
                },
            )
        )


def _collected_on(history: list[TradeLogRow], now: datetime) -> bool:
    """Gab es heute (Berliner Datum) schon einen Versuch?

    Zählt **jeden** Versuch, auch einen fehlgeschlagenen. Ein Endpunkt, der auf
    einen Fehler hin vielleicht doch etwas gebucht hat, darf nicht noch einmal
    angesprochen werden, bevor jemand hingesehen hat.
    """
    today = _as_utc(now).astimezone(_BERLIN).date()
    return any(_as_utc(row.ts).astimezone(_BERLIN).date() == today for row in history)


def _streak(history: list[TradeLogRow], now: datetime) -> int:
    """Wie viele Kalendertage in Folge bis gestern erfolgreich gesammelt wurde.

    Kickbase belohnt die ununterbrochene Serie — ein ausgelassener Tag setzt
    sie zurück, und das ist die einzige Zahl, an der sich ablesen lässt, ob der
    Job zuverlässig läuft. Gezählt werden nur `executed`-Zeilen: ein Dry-Run
    oder ein Fehlversuch hält die Serie nicht am Leben.
    """
    days = {
        _as_utc(row.ts).astimezone(_BERLIN).date()
        for row in history
        if row.executed and row.action == BONUS_ACTION
    }
    if not days:
        return 0
    cursor = _as_utc(now).astimezone(_BERLIN).date() - timedelta(days=1)
    streak = 0
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _as_utc(value: datetime) -> datetime:
    """SQLite gibt naive Datetimes zurück — sie sind per Konvention UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


__all__ = ["BONUS_ACTION", "BonusOutcome", "CollectDailyBonusUseCase"]
