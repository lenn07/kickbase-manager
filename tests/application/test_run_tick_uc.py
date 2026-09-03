from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.application.decision_engine import (
    DecisionContext,
    DecisionEngine,
    HoldOnlyDecisionEngine,
)
from app.application.run_tick_uc import RunTickUseCase
from app.application.setup_service import SetupService, SmtpFormInput
from app.domain.exceptions import TransportError
from app.domain.models import LeagueMe, Squad
from app.domain.trade import TradeAction, TradeDecision, TradeIntent
from app.infrastructure.crypto.vault import FernetVault
from app.infrastructure.persistence.models import TradeLogRow
from app.infrastructure.persistence.repositories import (
    SettingsRepository,
    TradeLogRepository,
    UserRepository,
)
from sqlmodel import Session

from tests.application.conftest import FakeKickbase, FakeLlm, FakeSmtp


async def _complete_setup(
    session: Session, vault: FernetVault, kb: FakeKickbase, smtp: FakeSmtp
) -> None:
    service = SetupService(session=session, vault=vault, kickbase=kb, llm=FakeLlm(), smtp=smtp)
    await service.verify_kickbase("user@example.com", "secret")
    service.select_league("L1")
    await service.verify_anthropic("sk-ant-good")
    await service.verify_smtp(
        SmtpFormInput(
            host="smtp.example.com",
            port=465,
            username="u",
            password="pw",
            from_addr="from@example.com",
            to_addr="to@example.com",
            use_tls=True,
            use_starttls=False,
        )
    )


class FixedDecisionEngine:
    def __init__(self, decision: TradeDecision) -> None:
        self._decision = decision
        self.contexts: list[DecisionContext] = []

    async def decide(self, context: DecisionContext) -> TradeDecision:
        self.contexts.append(context)
        return self._decision


class FailingKickbase(FakeKickbase):
    async def get_league_me(self, league_id: str) -> LeagueMe:
        raise TransportError("api hakelt")

    async def get_squad(self, league_id: str, manager_id: str) -> Squad:
        raise AssertionError("squad-call sollte nicht mehr passieren")


async def test_tick_skipped_when_setup_incomplete(db_session: Session, vault: FernetVault) -> None:
    uc = RunTickUseCase(
        session=db_session,
        vault=vault,
        kickbase=FakeKickbase(),
        engine=HoldOnlyDecisionEngine(),
        smtp=FakeSmtp(),
    )
    outcome = await uc.run()
    assert outcome.executed is False
    assert outcome.skipped_reason == "setup-incomplete"
    assert outcome.log_id is None


async def test_tick_hold_logs_but_sends_no_mail(db_session: Session, vault: FernetVault) -> None:
    kb = FakeKickbase()
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)
    smtp.sent.clear()  # Setup-Test-Mail nicht zählen

    uc = RunTickUseCase(
        session=db_session,
        vault=vault,
        kickbase=kb,
        engine=HoldOnlyDecisionEngine(),
        smtp=smtp,
    )
    outcome = await uc.run()

    assert outcome.executed is False
    assert outcome.decision is not None
    assert outcome.decision.action is TradeAction.HOLD
    assert outcome.log_id is not None

    logs = TradeLogRepository(db_session).list_recent(
        user_id=UserRepository(db_session).get_singleton().id  # type: ignore[union-attr,arg-type]
    )
    assert len(logs) == 1
    assert logs[0].action == "HOLD"
    assert smtp.sent == []


async def test_tick_dry_run_action_is_logged_and_mailed_but_not_executed(
    db_session: Session, vault: FernetVault
) -> None:
    kb = FakeKickbase()
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)
    smtp.sent.clear()

    user = UserRepository(db_session).get_singleton()
    assert user is not None and user.id is not None
    # dry_run bleibt Default (True)
    SettingsRepository(db_session).get_or_default(user.id)

    engine = FixedDecisionEngine(
        TradeDecision(
            action=TradeAction.BUY,
            reason="stub-decision",
            player_id="p1",
            player_name="Musterspieler",
            price=Decimal(1_000_000),
        )
    )
    uc = RunTickUseCase(session=db_session, vault=vault, kickbase=kb, engine=engine, smtp=smtp)
    outcome = await uc.run()

    assert outcome.decision is not None
    assert outcome.decision.action is TradeAction.BUY
    assert outcome.executed is False  # Dry-Run
    assert len(smtp.sent) == 1
    _config, subject, body = smtp.sent[0]
    assert "vorgemerkt" in subject or "BUY" in subject
    assert "Musterspieler" in body


async def test_tick_live_action_sets_notified_at(db_session: Session, vault: FernetVault) -> None:
    kb = FakeKickbase()
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)
    smtp.sent.clear()

    user = UserRepository(db_session).get_singleton()
    assert user is not None and user.id is not None
    settings = SettingsRepository(db_session).get_or_default(user.id)
    settings.dry_run = False
    SettingsRepository(db_session).upsert(settings)

    engine = FixedDecisionEngine(
        TradeDecision(
            action=TradeAction.BUY,
            reason="live-decision",
            player_id="p1",
            player_name="Live",
            price=Decimal(500_000),
        )
    )
    uc = RunTickUseCase(session=db_session, vault=vault, kickbase=kb, engine=engine, smtp=smtp)
    outcome = await uc.run()

    assert outcome.executed is True
    latest = TradeLogRepository(db_session).latest(user.id)
    assert latest is not None
    assert latest.executed is True
    assert latest.notified_at is not None
    assert datetime.now(UTC) - _as_utc(latest.notified_at) < timedelta(seconds=10)


async def test_tick_live_action_smtp_failure_leaves_notified_at_null(
    db_session: Session, vault: FernetVault
) -> None:
    kb = FakeKickbase()
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)
    smtp.sent.clear()

    user = UserRepository(db_session).get_singleton()
    assert user is not None and user.id is not None
    settings = SettingsRepository(db_session).get_or_default(user.id)
    settings.dry_run = False
    SettingsRepository(db_session).upsert(settings)

    # Ab jetzt schlägt SMTP fehl — Trade muss geloggt werden, notified_at aber leer bleiben.
    failing_smtp = FakeSmtp(fail=True)

    engine = FixedDecisionEngine(
        TradeDecision(
            action=TradeAction.BUY,
            reason="live-decision",
            player_id="p1",
            player_name="Live",
            price=Decimal(500_000),
        )
    )
    uc = RunTickUseCase(
        session=db_session, vault=vault, kickbase=kb, engine=engine, smtp=failing_smtp
    )
    outcome = await uc.run()

    assert outcome.executed is True
    latest = TradeLogRepository(db_session).latest(user.id)
    assert latest is not None
    assert latest.executed is True
    assert latest.notified_at is None


async def test_tick_kickbase_error_is_logged_and_mailed(
    db_session: Session, vault: FernetVault
) -> None:
    kb = FailingKickbase()
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)
    smtp.sent.clear()

    uc = RunTickUseCase(
        session=db_session,
        vault=vault,
        kickbase=kb,
        engine=HoldOnlyDecisionEngine(),
        smtp=smtp,
    )
    outcome = await uc.run()

    assert outcome.executed is False
    assert outcome.log_id is not None
    latest = TradeLogRepository(db_session).latest(
        UserRepository(db_session).get_singleton().id  # type: ignore[union-attr,arg-type]
    )
    assert latest is not None
    assert latest.action == "ERROR"
    assert "api hakelt" in latest.reason_text
    assert len(smtp.sent) == 1


async def test_tick_persists_intent_in_trade_log_context(
    db_session: Session, vault: FernetVault
) -> None:
    kb = FakeKickbase()
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)
    smtp.sent.clear()

    engine = FixedDecisionEngine(
        TradeDecision(
            action=TradeAction.BUY,
            reason="stub",
            player_id="p1",
            player_name="Test",
            price=Decimal(1_000_000),
            intent=TradeIntent.PROFIT,
        )
    )
    uc = RunTickUseCase(session=db_session, vault=vault, kickbase=kb, engine=engine, smtp=smtp)
    outcome = await uc.run()

    assert outcome.log_id is not None
    latest = TradeLogRepository(db_session).latest(
        UserRepository(db_session).get_singleton().id  # type: ignore[union-attr,arg-type]
    )
    assert latest is not None
    assert latest.context.get("intent") == "PROFIT"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# Import-Reference, damit ruff „DecisionEngine ungenutzt" bei zukünftigen Reviews
# nicht meckert — Protokoll wird über Fake-Impls implizit erfüllt.
_ = DecisionEngine
_ = TradeLogRow
