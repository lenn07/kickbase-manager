from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.application.decision_engine import (
    DecisionContext,
    DecisionEngine,
    HoldOnlyDecisionEngine,
)
from app.application.run_tick_uc import RunTickUseCase, _max_negative_allowed
from app.application.setup_service import SetupService, SmtpFormInput
from app.domain.exceptions import TransportError
from app.domain.models import (
    LeagueMe,
    MarketPlayer,
    MarketSnapshot,
    Matchday,
    Player,
    PlayerStatus,
    Position,
    Squad,
    SquadPlayer,
)
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


# -- P0-1: Teamwert & Markt-Metadaten ------------------------------------


def test_max_negative_allowed_with_a_real_team_value() -> None:
    """Die 33 %-Regel gegen den echten Teamwert aus der Cassette.

    Vor P0-1 lief diese Rechnung jeden Tick gegen `team_value=0` und gab damit
    0 zurück — das LLM hat daraus wörtlich geschlossen, es dürfe nicht kaufen
    (Plan §6/P0-1). Bei 148,77 Mio Teamwert und 380k Minus sind rund 49 Mio
    Spielraum erlaubt.
    """
    limit = _max_negative_allowed(team_value=Decimal(148_767_974), cash=Decimal(-380_069))
    assert limit == Decimal(-48_968_009)


def test_max_negative_allowed_ignores_positive_cash_in_the_basis() -> None:
    """Nur ein *negativer* Kontostand verkleinert die Basis, Guthaben nicht."""
    with_cash = _max_negative_allowed(team_value=Decimal(100_000_000), cash=Decimal(5_000_000))
    no_cash = _max_negative_allowed(team_value=Decimal(100_000_000), cash=Decimal(0))
    assert with_cash == no_cash == Decimal(-33_000_000)


def test_max_negative_allowed_stays_zero_without_a_team_value() -> None:
    """Fehlt der Teamwert, ist 0 die konservative Antwort — kein Minus."""
    assert _max_negative_allowed(team_value=Decimal(0), cash=Decimal(1_000_000)) == Decimal(0)


class _MatchdayCountingKickbase(FakeKickbase):
    """Zählt `list_matchdays()`-Aufrufe — das ist der eingesparte HTTP-Call."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.matchday_calls = 0

    async def list_matchdays(self, competition_id: str = "1") -> list[Matchday]:
        self.matchday_calls += 1
        return [
            Matchday(
                number=9,
                starts_at=datetime.now(UTC) + timedelta(days=30),
                ends_at=datetime.now(UTC) + timedelta(days=30, hours=2),
                is_current=False,
            )
        ]


async def test_tick_takes_the_matchday_start_from_the_market_root(
    db_session: Session, vault: FernetVault
) -> None:
    """`dt` steht im Market-Payload — der Extra-Call entfällt (ein Request weniger).

    Das ist kein kosmetischer Gewinn: der Tick läuft gegen ein Rate-Limit, und
    jeder gesparte Call ist Budget für die Anreicherung.
    """
    expected = datetime.now(UTC) + timedelta(days=3)
    kb = _MatchdayCountingKickbase(next_matchday_start=expected)
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)

    engine = FixedDecisionEngine(TradeDecision.hold("nichts zu tun"))
    await RunTickUseCase(
        session=db_session, vault=vault, kickbase=kb, engine=engine, smtp=smtp
    ).run()

    assert kb.matchday_calls == 0, "list_matchdays() lief trotz `dt` im Market-Root"
    assert engine.contexts[0].next_matchday_start == expected


async def test_tick_falls_back_to_list_matchdays_when_dt_is_stale(
    db_session: Session, vault: FernetVault
) -> None:
    """Zwischen Anpfiff und Payload-Update zeigt `dt` in die Vergangenheit.

    Dann ist die Liste die verlässlichere Quelle — sonst hielte der Bot einen
    längst angepfiffenen Spieltag für „gleich" und triebe Deadline-Panik.
    """
    kb = _MatchdayCountingKickbase(next_matchday_start=datetime.now(UTC) - timedelta(hours=2))
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)

    engine = FixedDecisionEngine(TradeDecision.hold("nichts zu tun"))
    await RunTickUseCase(
        session=db_session, vault=vault, kickbase=kb, engine=engine, smtp=smtp
    ).run()

    assert kb.matchday_calls == 1
    assert engine.contexts[0].next_matchday_start > datetime.now(UTC)


async def test_tick_passes_team_value_and_mv_update_into_the_context(
    db_session: Session, vault: FernetVault
) -> None:
    """Beide Root-Felder müssen bis in den Entscheidungs-Kontext durchkommen."""
    mv_update = datetime.now(UTC) + timedelta(hours=4)
    kb = FakeKickbase(team_value=Decimal(148_767_974), mv_update_at=mv_update)
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)

    engine = FixedDecisionEngine(TradeDecision.hold("nichts zu tun"))
    await RunTickUseCase(
        session=db_session, vault=vault, kickbase=kb, engine=engine, smtp=smtp
    ).run()

    context = engine.contexts[0]
    assert context.team_value == Decimal(148_767_974)
    assert context.mv_update_at == mv_update
    assert context.max_negative_allowed < 0


# -- P0-2: Gebote auf eigenen Listings -----------------------------------


class _ListingKickbase(FakeKickbase):
    """Liefert ein eigenes Listing mit `ofc` — wie der echte Market-Payload."""

    def __init__(self, *, offer_count: int, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._offer_count = offer_count

    async def get_squad(self, league_id: str, manager_id: str) -> Squad:
        player = Player(
            id="1809",
            first_name="Marius",
            last_name="Wolf",
            team_id="13",
            position=Position.MIDFIELDER,
            status=PlayerStatus.FIT,
            market_value=Decimal(8_811_078),
            average_points=111.0,
            total_points=444,
        )
        return Squad(
            league_id=league_id,
            manager_id=manager_id,
            players=(SquadPlayer(player=player, lineup_order=8),),
        )

    async def get_market(self, league_id: str) -> MarketSnapshot:
        squad = await self.get_squad(league_id, "u1")
        listing = MarketPlayer(
            player=squad.players[0].player,
            price=Decimal(9_200_000),
            expires_in_s=None,  # eigene Listings tragen kein `exs` (F5)
            seller_id="u1",
            offer_count=self._offer_count,
        )
        return MarketSnapshot(players=(listing,), team_value=self.team_value)


async def test_own_listing_reports_incoming_bids_through_the_counter(
    db_session: Session, vault: FernetVault
) -> None:
    """`has_offers` muss aus `ofc` kommen, nicht aus dem leeren `offers`-Tupel.

    Am Tupel hing der Stale-Fallback: ein Listing mit vier Bietern hätte als
    „keine Gebote" gegolten und wäre in den Sofortverkauf gelaufen — der
    Bieterwettbewerb wäre verschenkt gewesen.
    """
    kb = _ListingKickbase(offer_count=4)
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)

    engine = FixedDecisionEngine(TradeDecision.hold("beobachten"))
    await RunTickUseCase(
        session=db_session, vault=vault, kickbase=kb, engine=engine, smtp=smtp
    ).run()

    listing = engine.contexts[0].own_listings["1809"]
    assert listing.has_offers is True
    assert listing.offer_count == 4


async def test_own_listing_without_bids_stays_marked_as_quiet(
    db_session: Session, vault: FernetVault
) -> None:
    kb = _ListingKickbase(offer_count=0)
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)

    engine = FixedDecisionEngine(TradeDecision.hold("beobachten"))
    await RunTickUseCase(
        session=db_session, vault=vault, kickbase=kb, engine=engine, smtp=smtp
    ).run()

    listing = engine.contexts[0].own_listings["1809"]
    assert listing.has_offers is False
    assert listing.offer_count == 0
