"""RunTickUseCase — orchestriert einen einzelnen Scheduler-Tick (F-4/F-5).

Verantwortlichkeiten:
- Setup-State prüfen (kein Tick ohne vollständige Konfiguration).
- Aktive Liga + Squad + Markt vom Kickbase-Gateway laden.
- Entscheidungs-Engine (Phase 3: HOLD-Stub) befragen.
- TradeExecutor anwenden (respektiert Dry-Run).
- Ergebnis in `trade_log` persistieren.
- Ausgeführte Aktionen und Fehler per SMTP melden; HOLD-Ticks bleiben stumm
  und werden vom separaten Digest-Job (`SendHoldDigestUseCase`, F-9) gebündelt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlmodel import Session

from app.application.decision_engine import (
    BuyRecord,
    DecisionContext,
    DecisionEngine,
    ListingRecord,
    OpenBid,
    RecentAction,
)
from app.application.lineup_guard import propose_lineup_fix, to_decision
from app.application.player_enrichment import PlayerEnricher, PlayerEnrichment
from app.application.setup_state import read_setup_state
from app.application.team_context import TeamContextProvider
from app.application.trade_executor import ExecutionResult, TradeExecutor
from app.domain.exceptions import KickbaseError
from app.domain.fixtures import TeamOutlook
from app.domain.gateways import KickbaseGateway
from app.domain.lineup import Lineup
from app.domain.models import (
    LeagueConstraints,
    LeagueRanking,
    MarketPlayer,
    MarketSnapshot,
    Squad,
)
from app.domain.trade import TradeAction, TradeDecision, TradeIntent
from app.infrastructure.crypto.vault import CryptoError, FernetVault
from app.infrastructure.metrics import get_metrics
from app.infrastructure.notifications.smtp_client import SmtpConfig, SmtpError, SmtpGateway
from app.infrastructure.persistence.models import SmtpConfigRow, TradeLogRow
from app.infrastructure.persistence.repositories import (
    LeagueRepository,
    MarketMetaRepository,
    SettingsRepository,
    SmtpRepository,
    TradeLogRepository,
    UserRepository,
)

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TickOutcome:
    executed: bool
    decision: TradeDecision | None
    log_id: int | None
    skipped_reason: str | None = None
    # Der Anpfiff, den dieser Tick gesehen hat — der Scheduler zieht seine
    # beweglichen Fenster daraus nach (P1-10). `None`, wenn der Tick gar nicht
    # so weit kam (Setup unvollständig, Kickbase-Fehler).
    next_matchday_start: datetime | None = None


_MAX_RECENT_ACTIONS = 20
_KICKBASE_DEBT_ALLOWANCE_PCT = Decimal("0.33")


class RunTickUseCase:
    def __init__(
        self,
        *,
        session: Session,
        vault: FernetVault,
        kickbase: KickbaseGateway,
        engine: DecisionEngine,
        smtp: SmtpGateway,
        enricher: PlayerEnricher | None = None,
        team_context: TeamContextProvider | None = None,
        lineup_writes_enabled: bool = False,
        club_limit: int | None = None,
        club_limit_is_unlimited: bool = False,
        underpay_blocked: bool | None = None,
        scoring_mode: str | None = None,
    ) -> None:
        # Die drei Liga-Einstellungen, die die Kickbase-API nicht herausgibt
        # (P1-9/§8/F6). Ohne Wert bleiben sie unbekannt und der Payload sagt
        # das — geraten wird nichts.
        self._club_limit = club_limit
        self._club_limit_is_unlimited = club_limit_is_unlimited
        self._underpay_blocked = underpay_blocked
        self._scoring_mode = scoring_mode
        # Kill-Switch aus der Konfiguration (`KB_LINEUP_WRITES_ENABLED`, Default
        # aus). Aufstellungs-Writes bewegen direkt Punkte — sie gehen erst raus,
        # wenn der Shadow-Lauf sie bestätigt hat (Plan §9).
        self._lineup_writes_enabled = lineup_writes_enabled
        self._session = session
        self._vault = vault
        self._kickbase = kickbase
        self._engine = engine
        self._smtp = smtp
        self._enricher = enricher
        # Spielplan-Kontext (P2-11). Ohne Provider bleibt `team_outlook` leer und
        # der Payload sagt `missing_data:fixtures` — derselbe Zustand wie vor
        # P2-11, nur jetzt benannt.
        self._team_context = team_context
        self._users = UserRepository(session)
        self._leagues = LeagueRepository(session)
        self._settings = SettingsRepository(session)
        self._smtp_repo = SmtpRepository(session)
        self._trades = TradeLogRepository(session)
        self._market_meta = MarketMetaRepository(session)

    async def run(self) -> TickOutcome:
        metrics = get_metrics()
        metrics.last_tick_ts.set_to_current_time()
        _log.info("Tick gestartet.")

        state = read_setup_state(self._session)
        if not state.is_complete:
            _log.info("Tick übersprungen: Setup nicht vollständig (%s)", state.next_step)
            metrics.record_tick("skipped_setup")
            return TickOutcome(
                executed=False, decision=None, log_id=None, skipped_reason="setup-incomplete"
            )

        user = self._users.get_singleton()
        assert user is not None and user.id is not None  # von state.is_complete garantiert

        league_row = self._leagues.active(user.id)
        assert league_row is not None

        settings = self._settings.get_or_default(user.id)

        try:
            league_me = await self._kickbase.get_league_me(league_row.kb_league_id)
            squad = await self._kickbase.get_squad(league_row.kb_league_id, user.kb_user_id)
            snapshot = await self._kickbase.get_market(league_row.kb_league_id)
            market = list(snapshot.players)
            next_matchday_start = await self._next_matchday_start(snapshot)
            lineup = await self._kickbase.get_lineup(league_row.kb_league_id)
        except KickbaseError as exc:
            _log.warning("Kickbase-Fehler im Tick: %s", exc)
            row = self._trades.add(
                TradeLogRow(
                    user_id=user.id,
                    action="ERROR",
                    reason_text=f"Kickbase-Fehler: {exc}",
                    executed=False,
                    context={"stage": "load"},
                )
            )
            await self._maybe_notify_error(user.id, "Kickbase-Fehler", str(exc))
            metrics.record_tick("error")
            return TickOutcome(executed=False, decision=None, log_id=row.id)

        now = datetime.now(UTC)
        # Die beiden Uhren festhalten, bevor irgendetwas schiefgehen kann: der
        # Scheduler legt seine Fenster daraus, und nach einem Neustart ist das
        # hier die einzige Quelle, bis der erste Tick durch ist (P1-10).
        self._market_meta.upsert(
            league_row.kb_league_id,
            next_matchday_start=next_matchday_start,
            mv_update_at=snapshot.mv_update_at,
        )

        squad_ids = {sp.player.id for sp in squad.players}
        buy_history = _load_buy_history(self._trades, user.id, squad_ids)
        own_listings = _load_own_listings(
            trades=self._trades,
            user_id=user.id,
            market=market,
            manager_id=user.kb_user_id,
            now=now,
        )
        recent_actions = _load_recent_actions(self._trades, user.id)
        enrichment, team_outlook, ranking = await self._load_prompt_signals(
            league_row.kb_league_id,
            squad,
            market,
            snapshot=snapshot,
            next_matchday_start=next_matchday_start,
            now=now,
        )

        open_bids = self._collect_open_bids(
            market=market, manager_id=user.kb_user_id, user_id=user.id, squad_ids=squad_ids
        )
        open_bids_total = sum((bid.price for bid in open_bids.values()), Decimal(0))
        max_negative = _max_negative_allowed(team_value=snapshot.team_value, cash=league_me.budget)
        current_balance_after_open_bids = league_me.budget - open_bids_total

        # Startelf-Guard **vor** der LLM-Abfrage: ob elf Positionen besetzt sind,
        # ist keine Ermessensfrage (Plan §6/P0-4). Er läuft als eigene Aktion und
        # verbraucht den Tick nicht — das Modell entscheidet danach normal weiter.
        await self._run_lineup_guard(
            user_id=user.id,
            league_id=league_row.kb_league_id,
            squad=squad,
            lineup=lineup,
            enrichment=enrichment,
            dry_run=settings.dry_run,
        )

        context = DecisionContext(
            league_id=league_row.kb_league_id,
            league_me=league_me,
            squad=squad,
            market=tuple(market),
            budget=league_me.budget,
            min_action_score=settings.min_action_score,
            max_trade_pct=settings.max_trade_pct,
            min_cash_reserve=settings.min_cash_reserve,
            blacklist=tuple(settings.blacklist),
            team_value=snapshot.team_value,
            open_bids_total=open_bids_total,
            open_bids=open_bids,
            now=now,
            next_matchday_start=next_matchday_start,
            mv_update_at=snapshot.mv_update_at,
            interval_min=settings.interval_min,
            buy_history=buy_history,
            own_listings=own_listings,
            enrichment=enrichment,
            recent_actions=recent_actions,
            max_negative_allowed=max_negative,
            current_balance_after_open_bids=current_balance_after_open_bids,
            lineup=lineup,
            lineup_deadline=next_matchday_start,
            constraints=LeagueConstraints(
                squad_limit=league_me.squad_limit,
                # Kickbase liefert diese drei nicht — sie stehen in den
                # Admin-Einstellungen der Liga und kommen daher aus der
                # Konfiguration. Nicht gesetzt bleibt `None` (Plan §9): lieber
                # unbekannt als erfunden.
                club_limit=self._club_limit,
                club_limit_is_unlimited=self._club_limit_is_unlimited,
                players_per_club=league_me.players_per_club,
                underpay_blocked=self._underpay_blocked,
                scoring_mode=self._scoring_mode,
            ),
            team_outlook=team_outlook,
            league_ranking=ranking,
        )

        decision = await self._engine.decide(context)
        executor = self._executor(squad=squad, dry_run=settings.dry_run)
        result = await executor.execute(league_row.kb_league_id, decision)

        row = self._trades.add(
            TradeLogRow(
                user_id=user.id,
                action=decision.action.value,
                player_id=decision.player_id,
                player_name=decision.player_name,
                price=int(decision.price) if decision.price is not None else None,
                reason_text=decision.reason,
                executed=result.executed,
                context={
                    "dry_run": settings.dry_run,
                    "executor_note": result.reason,
                    "response_ref": result.response_ref,
                    "error": result.error,
                    "intent": decision.intent.value if decision.intent is not None else None,
                },
            )
        )

        await self._notify_outcome(user.id, decision, result)

        _log.info(
            "Tick abgeschlossen: %s (%s) — %s",
            decision.action.value,
            _tick_status(decision, result, settings.dry_run),
            _tick_summary(decision, result),
        )

        if decision.is_hold:
            metrics.record_tick("hold")
        elif result.executed:
            metrics.record_tick("executed")
            metrics.record_trade(decision.action.value)
        else:
            # Nicht-HOLD-Entscheidung, aber nicht ausgeführt → Dry-Run oder Executor-Fehler.
            metrics.record_tick("blocked")

        return TickOutcome(
            executed=result.executed,
            decision=decision,
            log_id=row.id,
            next_matchday_start=next_matchday_start,
        )

    def _executor(self, *, squad: Squad, dry_run: bool) -> TradeExecutor:
        return TradeExecutor(
            self._kickbase,
            dry_run=dry_run,
            squad=squad.players,
            lineup_writes_enabled=self._lineup_writes_enabled,
        )

    def _collect_open_bids(
        self,
        *,
        market: list[MarketPlayer],
        manager_id: str,
        user_id: int,
        squad_ids: set[str],
    ) -> dict[str, OpenBid]:
        """Eigene laufende Gebote sammeln und protokollieren.

        Die Log-Zeile ist wichtig: der Bot hat am 2026-09-24 siebenmal auf
        denselben Spieler geboten, ohne dass es irgendwo sichtbar war.
        """
        open_bids = _open_bids(
            market=market,
            manager_id=manager_id,
            trades=self._trades,
            user_id=user_id,
            squad_ids=squad_ids,
        )
        if open_bids:
            total = sum((bid.price for bid in open_bids.values()), Decimal(0))
            _log.info(
                "Offene eigene Gebote: %d über %s € — Spieler %s",
                len(open_bids),
                f"{int(total):,}".replace(",", "."),
                ", ".join(sorted(open_bids)),
            )
        return open_bids

    async def _run_lineup_guard(
        self,
        *,
        user_id: int,
        league_id: str,
        squad: Squad,
        lineup: Lineup,
        enrichment: dict[str, PlayerEnrichment],
        dry_run: bool,
    ) -> None:
        """Füllt leere Startelf-Slots, bevor das Modell überhaupt gefragt wird.

        Schweigt, wenn nichts zu verbessern ist — ein Guard, der jeden Tick
        schreibt, erzeugt Rauschen im `trade_log` und Last gegen das
        Rate-Limit, ohne einen einzigen Punkt zu bringen.
        """
        proposal = propose_lineup_fix(squad=squad.players, current=lineup, enrichment=enrichment)
        if proposal is None:
            return

        decision = to_decision(proposal)
        executor = self._executor(squad=squad, dry_run=dry_run)
        result = await executor.execute(league_id, decision)

        _log.info(
            "Startelf-Guard: %s (%d -> %d Slots, Formation %s)",
            "geschrieben" if result.executed else result.reason,
            proposal.current_count,
            len(proposal.lineup.player_ids),
            proposal.lineup.formation,
        )
        self._trades.add(
            TradeLogRow(
                user_id=user_id,
                action=decision.action.value,
                reason_text=decision.reason,
                executed=result.executed,
                context={
                    "dry_run": dry_run,
                    "executor_note": result.reason,
                    "error": result.error,
                    "intent": decision.intent.value if decision.intent is not None else None,
                    "source": "lineup_guard",
                    "formation": proposal.lineup.formation,
                    "player_ids": list(proposal.lineup.player_ids),
                    "slots_before": proposal.current_count,
                },
            )
        )

    async def _load_prompt_signals(
        self,
        league_id: str,
        squad: Squad,
        market: list[MarketPlayer],
        *,
        snapshot: MarketSnapshot,
        next_matchday_start: datetime | None,
        now: datetime,
    ) -> tuple[dict[str, PlayerEnrichment], dict[str, TeamOutlook], LeagueRanking | None]:
        """Die drei Zusatzsignal-Quellen des Prompts: Spieler, Spielplan, Ligatabelle.

        Gemeinsam gehalten, weil sie dieselbe Eigenschaft teilen — jede darf
        ausfallen, ohne den Tick zu verbrauchen. Was nicht ausfallen darf, steht
        im Block darüber (`get_league_me`, `get_squad`, `get_market`,
        `get_lineup`) und bricht den Tick mit einer ERROR-Zeile ab.
        """
        enrichment = await self._enrich_players(
            league_id,
            squad,
            market,
            snapshot=snapshot,
            next_matchday_start=next_matchday_start,
            now=now,
        )
        team_outlook = await self._load_team_outlook(
            next_matchday_start=next_matchday_start, now=now
        )
        ranking = await self._load_ranking(league_id)
        return enrichment, team_outlook, ranking

    async def _load_ranking(self, league_id: str) -> LeagueRanking | None:
        """Ligatabelle (P2-12) — ein Call pro Tick, bewusst ohne Cache.

        Anders als Bundesliga-Tabelle und Spielplan (P2-11) bewegt sich diese
        Tabelle **während** eines Spieltags: `mdp` und `spl` ändern sich mit
        jedem Tor. Ein Tages-Cache würde sie genau dann einfrieren, wenn sie
        etwas zu sagen hat.

        `None` bei jedem Kickbase-Fehler: der Rang entscheidet über den
        Risikoappetit, nicht über die Regel-Compliance.
        """
        try:
            return await self._kickbase.get_ranking(league_id)
        except KickbaseError as exc:
            _log.info("Ligatabelle nicht verfügbar (%s) — Prompt läuft ohne Rang.", exc)
            return None

    async def _enrich_players(
        self,
        league_id: str,
        squad: Squad,
        market: list[MarketPlayer],
        *,
        snapshot: MarketSnapshot,
        next_matchday_start: datetime | None,
        now: datetime,
    ) -> dict[str, PlayerEnrichment]:
        if self._enricher is None:
            return {}
        try:
            # `mvud` aus dem Market-Root ist die Haltbarkeit des
            # Historien-Caches: bis dahin ändert Kickbase keinen Marktwert.
            return await self._enricher.enrich(
                league_id,
                squad,
                market,
                mv_update_at=snapshot.mv_update_at,
                next_matchday_start=next_matchday_start,
                now=now,
            )
        except KickbaseError as exc:
            _log.info("Enrichment fehlgeschlagen (%s) — Prompt läuft ohne Zusatzsignale.", exc)
            return {}

    async def _load_team_outlook(
        self, *, next_matchday_start: datetime | None, now: datetime
    ) -> dict[str, TeamOutlook]:
        """Gegnerstärke je Verein (P2-11) — ein Ausfall darf den Tick nicht kosten.

        Der Provider fängt Kickbase-Fehler schon selbst ab und liefert dann ein
        leeres Mapping. Das `except` hier ist die zweite Sicherung: der
        Spielplan ist ein Komfort-Signal, die Regel-Compliance (Konto, Elf)
        hängt nicht an ihm.
        """
        if self._team_context is None:
            return {}
        try:
            return await self._team_context.load(next_matchday_start=next_matchday_start, now=now)
        except KickbaseError as exc:
            _log.info("Spielplan-Kontext fehlgeschlagen (%s) — Prompt läuft ohne Gegner.", exc)
            return {}

    async def _next_matchday_start(self, snapshot: MarketSnapshot) -> datetime | None:
        """Start des nächsten Spieltags — für die Deadline-Regel.

        Primärquelle ist `dt` aus dem Market-Root: es steht in einer Response,
        die wir ohnehin holen, und spart damit einen HTTP-Call pro Tick.
        `list_matchdays()` bleibt Fallback für den Fall, dass Kickbase das Feld
        weglässt oder es in der Vergangenheit liegt (zwischen Anpfiff und dem
        nächsten Payload-Update).

        Gibt None zurück, wenn beide Quellen nichts liefern; der
        Deadline-Modifikator ist dann inaktiv, die restlichen Regeln greifen
        weiter.
        """
        now = datetime.now(UTC)
        from_snapshot = snapshot.next_matchday_start
        if from_snapshot is not None and from_snapshot > now:
            return from_snapshot

        try:
            matchdays = await self._kickbase.list_matchdays()
        except KickbaseError as exc:
            _log.info("Matchday-Liste nicht verfügbar (%s) — Deadline-Regel inaktiv.", exc)
            return from_snapshot
        future = [md.starts_at for md in matchdays if md.starts_at > now]
        return min(future) if future else from_snapshot

    # -- Notification --------------------------------------------------

    async def _notify_outcome(
        self, user_id: int, decision: TradeDecision, result: ExecutionResult
    ) -> None:
        # HOLD-Ticks bleiben stumm; die Sammelmail übernimmt der Digest-Job (F-9).
        if decision.is_hold:
            return

        subject_prefix = "Aktion" if result.executed else "Aktion vorgemerkt"
        subject = f"[Kickbase] {subject_prefix}: {decision.action}"
        body = (
            f"Aktion: {decision.action}\n"
            f"Spieler: {decision.player_name or decision.player_id or '—'}\n"
            f"Preis: {decision.price if decision.price is not None else '—'}\n"
            f"Begründung: {decision.reason}\n"
            f"Ausgeführt: {result.executed}\n"
            f"Hinweis: {result.reason}\n"
        )
        if result.error:
            body += f"Fehler: {result.error}\n"
            subject = f"[Kickbase] Fehler bei {decision.action}"

        delivered = await self._send_mail(user_id, subject, body)

        # notified_at nur setzen, wenn die Mail tatsächlich rausging — sonst
        # könnte ein späterer Retry-Mechanismus (oder Nutzer-Diagnose) den
        # nicht-benachrichtigten Trade nicht mehr erkennen.
        if result.executed and delivered:
            row = self._trades.latest(user_id)
            if row is not None:
                row.notified_at = datetime.now(UTC)
                self._session.commit()

    async def _maybe_notify_error(self, user_id: int, subject: str, detail: str) -> None:
        await self._send_mail(user_id, f"[Kickbase] {subject}", detail)

    async def _send_mail(self, user_id: int, subject: str, body: str) -> bool:
        config = self._load_smtp(user_id)
        if config is None:
            _log.info("SMTP nicht konfiguriert — Mail übersprungen.")
            return False
        try:
            await self._smtp.send(config, subject, body)
        except SmtpError as exc:
            _log.warning("Mail-Versand fehlgeschlagen: %s", exc)
            return False
        return True

    def _load_smtp(self, user_id: int) -> SmtpConfig | None:
        row: SmtpConfigRow | None = self._smtp_repo.get(user_id)
        if row is None:
            return None
        try:
            password = self._vault.decrypt(row.encrypted_password)
        except CryptoError:
            _log.warning("SMTP-Passwort konnte nicht entschlüsselt werden.")
            return None
        return SmtpConfig(
            host=row.host,
            port=row.port,
            username=row.username,
            password=password,
            from_addr=row.from_addr,
            to_addr=row.to_addr,
            use_tls=row.use_tls,
            use_starttls=row.use_starttls,
        )


def _tick_status(decision: TradeDecision, result: ExecutionResult, dry_run: bool) -> str:
    if decision.is_hold:
        return "HOLD"
    if result.executed:
        return "ausgeführt"
    if result.error:
        return "Fehler"
    if dry_run:
        return "Dry-Run"
    return "nicht ausgeführt"


def _tick_summary(decision: TradeDecision, result: ExecutionResult) -> str:
    parts: list[str] = []
    if decision.player_name:
        parts.append(decision.player_name)
    if decision.price is not None:
        parts.append(f"{int(decision.price):,}".replace(",", ".") + " €")
    if result.error:
        parts.append(f"Fehler: {result.error}")
    else:
        parts.append(decision.reason)
    return " | ".join(parts)


def _load_own_listings(
    *,
    trades: TradeLogRepository,
    user_id: int,
    market: list[MarketPlayer],
    manager_id: str,
    now: datetime,
) -> dict[str, ListingRecord]:
    """Baut das ListingRecord-Mapping aus Market-Response + Trade-Log.

    Nur Spieler, die aktuell tatsächlich als eigenes Listing im Markt liegen
    (`seller_id == manager_id`), landen im Mapping — abgelaufene oder
    zurückgezogene Listings bleiben draußen. `listed_at` kommt bevorzugt aus
    dem letzten LIST_ON_MARKET-Log-Eintrag; ist keiner vorhanden (z. B. weil
    das Listing über die Kickbase-App angelegt wurde), bleibt es None und
    der Stale-Fallback stützt sich allein auf `expires_at`.

    `has_offers` kommt seit P0-2 aus `ofc` und nicht mehr aus dem `offers`-Tupel
    — das ist bis zur Klärung von F1 immer leer und hätte jedes eingegangene
    Gebot als „keins" gemeldet. Genau daran hing der Stale-Fallback: Ein
    Listing mit Bietern wäre in den Sofortverkauf gelaufen und hätte den
    Bieterwettbewerb verschenkt.
    """
    own = [mp for mp in market if mp.seller_id == manager_id]
    if not own:
        return {}
    ts_by_player = trades.last_listing_ts_by_player(user_id)
    out: dict[str, ListingRecord] = {}
    for mp in own:
        pid = mp.player.id
        out[pid] = ListingRecord(
            player_id=pid,
            listing_price=mp.price,
            listed_at=ts_by_player.get(pid),
            expires_at=mp.expires_at(now),
            has_offers=mp.has_offers,
            offer_count=mp.offer_count,
        )
    return out


def _load_recent_actions(trades: TradeLogRepository, user_id: int) -> tuple[RecentAction, ...]:
    """Letzte N trade_log-Zeilen (aufsteigend nach ts) für den Master-Prompt.

    `list_recent` liefert absteigend — wir drehen um, damit der Prompt die
    Historie chronologisch (älteste zuerst) sieht, was leichter zu lesen ist.
    Fehlende Intents/Actions werden übersprungen (Konsistenz > Vollständigkeit).
    """
    rows = trades.list_recent(user_id=user_id, limit=_MAX_RECENT_ACTIONS)
    out: list[RecentAction] = []
    for row in reversed(rows):
        try:
            action = TradeAction(row.action)
        except ValueError:
            continue
        intent_raw = row.context.get("intent") if isinstance(row.context, dict) else None
        try:
            intent = TradeIntent(intent_raw) if intent_raw else None
        except ValueError:
            intent = None
        out.append(
            RecentAction(
                ts=row.ts,
                action=action,
                player_id=row.player_id,
                price=Decimal(row.price) if row.price is not None else None,
                intent=intent,
                executed=row.executed,
            )
        )
    return tuple(out)


def _open_bids(
    *,
    market: list[MarketPlayer],
    manager_id: str,
    trades: TradeLogRepository,
    user_id: int,
    squad_ids: set[str],
) -> dict[str, OpenBid]:
    """Die eigenen Gebote, die gerade noch laufen — je Spieler eines.

    **Warum das aus dem `trade_log` rekonstruiert wird.** Kickbase liefert die
    abgegebenen Gebote nicht: das Gebots-Array im Market-Payload ist bis heute
    unbenannt (Plan §8/F1), `mp.offers` deshalb immer leer. Vorher ergab diese
    Funktion darum konstant 0 — der Bot sah seine eigenen laufenden Gebote
    nicht und bot jeden Tick erneut auf dieselben Spieler. Im Betrieb am
    2026-09-24 stand ein Spieler siebenmal als ausgeführter BUY im Log und lag
    immer noch im Markt (Defekt D3).

    Ein Gebot gilt als **offen**, wenn alle vier Bedingungen halten:

    1. Es steht als ausgeführter `BUY` im `trade_log` — wir haben es abgegeben.
    2. Der Spieler liegt **noch** im Markt. Ist das Listing weg, ist das Gebot
       entschieden, so oder so.
    3. Er steht **nicht** im eigenen Kader. Sonst haben wir ihn bekommen.
    4. Das Gebot wurde **nach** dem Beginn des aktuellen Listings abgegeben.
       Ohne diese Bedingung zählte ein Gebot auf ein früheres Listing desselben
       Spielers mit — der Spieler war zwischendurch verkauft und neu gelistet,
       und unser altes Gebot ist längst erloschen.

    Das ist eine Untergrenze, keine Gewissheit: ob wir Höchstbietende sind,
    sagt uns niemand. Genau deshalb steht `offer_count` daneben.
    """
    by_player = trades.last_executed_buys(user_id)
    open_bids: dict[str, OpenBid] = {}
    for mp in market:
        pid = mp.player.id
        if mp.seller_id == manager_id or pid in squad_ids:
            continue
        row = by_player.get(pid)
        if row is None or row.price is None:
            continue
        if mp.listed_at is not None and _as_utc(row.ts) < _as_utc(mp.listed_at):
            continue  # Gebot galt einem früheren Listing desselben Spielers
        open_bids[pid] = OpenBid(
            player_id=pid,
            price=Decimal(row.price),
            placed_at=_as_utc(row.ts),
        )
    return open_bids


def _as_utc(value: datetime) -> datetime:
    """SQLite gibt naive Datetimes zurück — sie sind per Konvention UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _max_negative_allowed(*, team_value: Decimal, cash: Decimal) -> Decimal:
    """Kickbase-33 %-Regel: `max_negative = -0.33 * (team_value + min(0, cash))`.

    Bei positivem Cash-Bestand zählt nur der Mannschaftswert; ein bereits
    bestehendes Kontominus reduziert die Basis (der Bot darf nicht endlos
    Schulden anhäufen). Rückgabe ist bereits negativ, damit der Vergleich
    `balance >= max_negative_allowed` intuitiv bleibt.
    """
    negative_component = min(cash, Decimal(0))
    basis = team_value + negative_component
    if basis <= 0:
        return Decimal(0)
    return -(basis * _KICKBASE_DEBT_ALLOWANCE_PCT).quantize(Decimal(1))


def _load_buy_history(
    trades: TradeLogRepository, user_id: int, squad_ids: set[str]
) -> dict[str, BuyRecord]:
    """Baut ein `BuyRecord`-Mapping für alle Spieler, die noch im Squad stehen.

    Nur ausgeführte BUY-Ticks zählen (Dry-Run-Vormerkungen bleiben ohne Effekt).
    Fehlt der Intent oder ist er ungültig, wird der Datensatz übersprungen —
    ohne Intent kann die PROFIT-Exit-Logik nichts entscheiden.
    """
    if not squad_ids:
        return {}
    raw = trades.last_executed_buys(user_id)
    out: dict[str, BuyRecord] = {}
    for player_id, row in raw.items():
        if player_id not in squad_ids or row.price is None:
            continue
        intent_raw = row.context.get("intent") if isinstance(row.context, dict) else None
        try:
            intent = TradeIntent(intent_raw) if intent_raw else None
        except ValueError:
            intent = None
        if intent is None:
            continue
        out[player_id] = BuyRecord(
            intent=intent, buy_price=Decimal(row.price), bought_at=_as_utc(row.ts)
        )
    return out
