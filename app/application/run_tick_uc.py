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

from sqlmodel import Session

from app.application.decision_engine import DecisionContext, DecisionEngine
from app.application.setup_state import read_setup_state
from app.application.trade_executor import ExecutionResult, TradeExecutor
from app.domain.exceptions import KickbaseError
from app.domain.gateways import KickbaseGateway
from app.domain.trade import TradeDecision
from app.infrastructure.crypto.vault import CryptoError, FernetVault
from app.infrastructure.metrics import get_metrics
from app.infrastructure.notifications.smtp_client import SmtpConfig, SmtpError, SmtpGateway
from app.infrastructure.persistence.models import SmtpConfigRow, TradeLogRow
from app.infrastructure.persistence.repositories import (
    LeagueRepository,
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


class RunTickUseCase:
    def __init__(
        self,
        *,
        session: Session,
        vault: FernetVault,
        kickbase: KickbaseGateway,
        engine: DecisionEngine,
        smtp: SmtpGateway,
    ) -> None:
        self._session = session
        self._vault = vault
        self._kickbase = kickbase
        self._engine = engine
        self._smtp = smtp
        self._users = UserRepository(session)
        self._leagues = LeagueRepository(session)
        self._settings = SettingsRepository(session)
        self._smtp_repo = SmtpRepository(session)
        self._trades = TradeLogRepository(session)

    async def run(self) -> TickOutcome:
        metrics = get_metrics()
        metrics.last_tick_ts.set_to_current_time()

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
            market = await self._kickbase.get_market(league_row.kb_league_id)
            next_matchday_start = await self._next_matchday_start()
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
            team_value=squad.team_value,
            now=datetime.now(UTC),
            next_matchday_start=next_matchday_start,
            interval_min=settings.interval_min,
        )

        decision = await self._engine.decide(context)
        executor = TradeExecutor(self._kickbase, dry_run=settings.dry_run)
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
                },
            )
        )

        await self._notify_outcome(user.id, decision, result)

        if decision.is_hold:
            metrics.record_tick("hold")
        elif result.executed:
            metrics.record_tick("executed")
            metrics.record_trade(decision.action.value)
        else:
            # Nicht-HOLD-Entscheidung, aber nicht ausgeführt → Dry-Run oder Executor-Fehler.
            metrics.record_tick("blocked")

        return TickOutcome(executed=result.executed, decision=decision, log_id=row.id)

    async def _next_matchday_start(self) -> datetime | None:
        """Frühester zukünftiger Spieltagsstart — für die Deadline-Regel.

        Gibt None zurück, wenn Kickbase keinen kommenden Spieltag liefert oder
        der Endpoint fehlschlägt; der Deadline-Modifikator ist dann inaktiv,
        die restlichen Regeln greifen weiter.
        """
        try:
            matchdays = await self._kickbase.list_matchdays()
        except KickbaseError as exc:
            _log.info("Matchday-Liste nicht verfügbar (%s) — Deadline-Regel inaktiv.", exc)
            return None
        now = datetime.now(UTC)
        future = [md.starts_at for md in matchdays if md.starts_at > now]
        return min(future) if future else None

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
