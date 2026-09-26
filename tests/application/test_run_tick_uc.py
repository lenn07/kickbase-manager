from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.application.decision_engine import (
    DecisionContext,
    DecisionEngine,
    HoldOnlyDecisionEngine,
)
from app.application.run_tick_uc import RunTickUseCase, TickOutcome, _max_negative_allowed
from app.application.setup_service import SetupService, SmtpFormInput
from app.application.team_context import TeamContextProvider
from app.domain.exceptions import TransportError
from app.domain.lineup import Lineup
from app.domain.models import (
    Fixture,
    LeagueMe,
    LeagueRanking,
    ManagerStanding,
    MarketPlayer,
    MarketSnapshot,
    Matchday,
    Player,
    PlayerStatus,
    Position,
    Squad,
    SquadPlayer,
    TeamStanding,
)
from app.domain.trade import TradeAction, TradeDecision, TradeIntent
from app.infrastructure.crypto.vault import FernetVault
from app.infrastructure.persistence.models import TradeLogRow
from app.infrastructure.persistence.repositories import (
    MarketMetaRepository,
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


# -- P0-4: Startelf-Guard im Tick -----------------------------------------


def _lineup_player(pid: str, position: Position) -> SquadPlayer:
    return SquadPlayer(
        player=Player(
            id=pid,
            first_name="",
            last_name=pid,
            team_id="2",
            position=position,
            status=PlayerStatus.FIT,
            market_value=Decimal(5_000_000),
            average_points=100.0,
        )
    )


class _GuardKickbase(FakeKickbase):
    """Kader für ein volles 3-5-2, aber nur drei besetzte Slots."""

    def __init__(self, *, placed: int = 3, **kwargs: object) -> None:
        self._squad_players = [
            _lineup_player("gk1", Position.GOALKEEPER),
            *[_lineup_player(f"def{i}", Position.DEFENDER) for i in range(1, 4)],
            *[_lineup_player(f"mid{i}", Position.MIDFIELDER) for i in range(1, 6)],
            *[_lineup_player(f"fwd{i}", Position.FORWARD) for i in range(1, 3)],
        ]
        ids = tuple(sp.player.id for sp in self._squad_players)
        super().__init__(  # type: ignore[arg-type]
            lineup=Lineup(formation="3-5-2", player_ids=ids[:placed]), **kwargs
        )

    async def get_squad(self, league_id: str, manager_id: str) -> Squad:
        return Squad(league_id=league_id, manager_id=manager_id, players=tuple(self._squad_players))


async def test_guard_writes_the_lineup_before_the_model_is_asked(
    db_session: Session, vault: FernetVault
) -> None:
    """Drei leere Slots sind 800 verschenkte Punkte — das ist keine Ermessensfrage."""
    kb = _GuardKickbase(placed=3)
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)
    settings_row = SettingsRepository(db_session).get_or_default(1)
    settings_row.dry_run = False
    SettingsRepository(db_session).upsert(settings_row)

    engine = FixedDecisionEngine(TradeDecision.hold("nichts zu tun"))
    await RunTickUseCase(
        session=db_session,
        vault=vault,
        kickbase=kb,
        engine=engine,
        smtp=smtp,
        lineup_writes_enabled=True,
    ).run()

    assert len(kb.lineups_written) == 1
    assert len(kb.lineups_written[0].player_ids) == 11
    rows = TradeLogRepository(db_session).list_recent(user_id=1, limit=10)
    guard_rows = [r for r in rows if r.action == "SET_LINEUP"]
    assert len(guard_rows) == 1
    assert guard_rows[0].executed
    assert guard_rows[0].context["source"] == "lineup_guard"
    assert guard_rows[0].context["slots_before"] == 3


async def test_guard_stays_out_of_the_way_when_the_lineup_is_complete(
    db_session: Session, vault: FernetVault
) -> None:
    """Ein Guard, der jeden Tick schreibt, ist nur Rauschen und Rate-Limit-Last."""
    kb = _GuardKickbase(placed=11)
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)

    engine = FixedDecisionEngine(TradeDecision.hold("nichts zu tun"))
    await RunTickUseCase(
        session=db_session,
        vault=vault,
        kickbase=kb,
        engine=engine,
        smtp=smtp,
        lineup_writes_enabled=True,
    ).run()

    assert kb.lineups_written == []
    rows = TradeLogRepository(db_session).list_recent(user_id=1, limit=10)
    assert not [r for r in rows if r.action == "SET_LINEUP"]


async def test_guard_respects_the_kill_switch(db_session: Session, vault: FernetVault) -> None:
    """Default-aus: die Aufstellung wird vorgemerkt und geloggt, nicht geschrieben.

    Genau das ist der Shadow-Modus aus dem Plan — man sieht eine Woche lang,
    was der Guard getan hätte, bevor er es tut.
    """
    kb = _GuardKickbase(placed=3)
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)

    engine = FixedDecisionEngine(TradeDecision.hold("nichts zu tun"))
    await RunTickUseCase(
        session=db_session, vault=vault, kickbase=kb, engine=engine, smtp=smtp
    ).run()

    assert kb.lineups_written == []
    rows = TradeLogRepository(db_session).list_recent(user_id=1, limit=10)
    guard_rows = [r for r in rows if r.action == "SET_LINEUP"]
    assert len(guard_rows) == 1
    assert not guard_rows[0].executed
    # Die geplante Aufstellung steht trotzdem im Log — sonst könnte man sie
    # nicht gegen die App vergleichen.
    assert len(guard_rows[0].context["player_ids"]) == 11


async def test_guard_does_not_consume_the_tick(db_session: Session, vault: FernetVault) -> None:
    """Der Guard läuft zusätzlich, nicht anstelle der Modell-Entscheidung."""
    kb = _GuardKickbase(placed=3)
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)

    engine = FixedDecisionEngine(TradeDecision.hold("Markt ruhig"))
    outcome = await RunTickUseCase(
        session=db_session,
        vault=vault,
        kickbase=kb,
        engine=engine,
        smtp=smtp,
        lineup_writes_enabled=True,
    ).run()

    assert len(engine.contexts) == 1, "Das Modell muss trotzdem gefragt werden"
    assert outcome.decision is not None
    assert outcome.decision.action is TradeAction.HOLD


async def test_lineup_reaches_the_decision_context(db_session: Session, vault: FernetVault) -> None:
    """Ohne Formation kann das Modell keine gültige `SET_LINEUP`-Aktion bauen."""
    kb = _GuardKickbase(placed=11)
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)

    engine = FixedDecisionEngine(TradeDecision.hold("ok"))
    await RunTickUseCase(
        session=db_session, vault=vault, kickbase=kb, engine=engine, smtp=smtp
    ).run()

    context = engine.contexts[0]
    assert context.lineup is not None
    assert context.lineup.formation == "3-5-2"


# -- P1-10: die Uhren für den Scheduler ----------------------------------


async def test_tick_persists_the_clocks_for_the_scheduler(
    db_session: Session, vault: FernetVault
) -> None:
    """Der Tick hinterlässt, was der Scheduler beim nächsten Start braucht.

    Der Scheduler legt seine beweglichen Fenster aus `next_matchday_start` —
    hat aber selbst keinen Zugang zur Kickbase-API und beim Containerstart noch
    keinen gelaufenen Tick. Ohne diese Zeile stünde nach einem Neustart bis
    zum ersten Intervall-Tick kein Deadline-Fenster.
    """
    kickoff = datetime.now(UTC) + timedelta(days=3)
    mv_update = datetime.now(UTC) + timedelta(hours=4)
    kb = FakeKickbase(mv_update_at=mv_update, next_matchday_start=kickoff)
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)

    engine = FixedDecisionEngine(TradeDecision.hold("nichts zu tun"))
    outcome = await RunTickUseCase(
        session=db_session, vault=vault, kickbase=kb, engine=engine, smtp=smtp
    ).run()

    row = MarketMetaRepository(db_session).get("L1")
    assert row is not None
    assert row.next_matchday_start.replace(tzinfo=UTC) == kickoff
    assert row.mv_update_at.replace(tzinfo=UTC) == mv_update
    # Und derselbe Wert kommt zurück, damit der Scheduler ohne DB-Zugriff
    # direkt nachziehen kann.
    assert outcome.next_matchday_start == kickoff


# -- P2-11: Spielplan & Gegnerstärke -------------------------------------


class _FixtureKickbase(FakeKickbase):
    """Liefert Tabelle und Spielplan — und zählt, wie oft sie geholt werden."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.table_calls = 0
        self.fixture_calls = 0

    async def get_squad(self, league_id: str, manager_id: str) -> Squad:
        player = Player(
            id="p1",
            first_name="Dayot",
            last_name="Upamecano",
            team_id="2",
            position=Position.DEFENDER,
            status=PlayerStatus.FIT,
            market_value=Decimal(33_000_000),
            average_points=178.0,
            total_points=712,
        )
        return Squad(
            league_id=league_id,
            manager_id=manager_id,
            players=(SquadPlayer(player=player, lineup_order=1),),
        )

    async def get_competition_table(self, competition_id: str = "1") -> list[TeamStanding]:
        self.table_calls += 1
        return [
            TeamStanding(
                team_id="3",
                team_name="Dortmund",
                rank=1,
                points=12,
                matches_played=4,
                goal_difference=7,
            ),
            TeamStanding(
                team_id="2",
                team_name="Bayern",
                rank=2,
                points=10,
                matches_played=4,
                goal_difference=12,
            ),
        ]

    async def list_fixtures(self, competition_id: str = "1") -> list[Fixture]:
        self.fixture_calls += 1
        return [
            Fixture(
                matchday=5,
                kickoff=datetime.now(UTC) + timedelta(days=3),
                home_team_id="3",
                away_team_id="2",
                is_finished=False,
            )
        ]


async def test_fixtures_reach_the_decision_context(db_session: Session, vault: FernetVault) -> None:
    """Der Spielplan muss bis in den Kontext kommen — sonst bleibt §1.2 blind."""
    kb = _FixtureKickbase()
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)

    engine = FixedDecisionEngine(TradeDecision.hold("ok"))
    await RunTickUseCase(
        session=db_session,
        vault=vault,
        kickbase=kb,
        engine=engine,
        smtp=smtp,
        team_context=TeamContextProvider(kb),
    ).run()

    outlook = engine.contexts[0].team_outlook["2"]
    assert outlook.next_opponent_name == "Dortmund"
    assert outlook.is_home is False
    assert outlook.fdr == 5


async def test_tick_runs_without_a_fixture_provider(
    db_session: Session, vault: FernetVault
) -> None:
    """Ohne Provider bleibt `team_outlook` leer — der Tick läuft trotzdem durch.

    Der Zustand vor P2-11, nur jetzt benannt: der Payload setzt dann
    `missing_data:fixtures` und der Prompt entscheidet ohne Gegner.
    """
    kb = _FixtureKickbase()
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)

    engine = FixedDecisionEngine(TradeDecision.hold("ok"))
    outcome = await RunTickUseCase(
        session=db_session, vault=vault, kickbase=kb, engine=engine, smtp=smtp
    ).run()

    assert outcome.decision is not None
    assert engine.contexts[0].team_outlook == {}
    assert kb.table_calls == 0


async def test_fixture_failure_does_not_cost_the_tick(
    db_session: Session, vault: FernetVault
) -> None:
    """Gegnerstärke ist Komfort. Konto und Elf hängen nicht daran."""

    class _BrokenFixtures(_FixtureKickbase):
        async def list_fixtures(self, competition_id: str = "1") -> list[Fixture]:
            raise TransportError("tabelle weg")

        async def get_competition_table(self, competition_id: str = "1") -> list[TeamStanding]:
            raise TransportError("tabelle weg")

    kb = _BrokenFixtures()
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)

    engine = FixedDecisionEngine(TradeDecision.hold("ok"))
    outcome = await RunTickUseCase(
        session=db_session,
        vault=vault,
        kickbase=kb,
        engine=engine,
        smtp=smtp,
        team_context=TeamContextProvider(kb),
    ).run()

    assert outcome.decision is not None
    assert engine.contexts[0].team_outlook == {}


# -- P2-12: Ligakontext ---------------------------------------------------


class _RankingKickbase(FakeKickbase):
    """Liefert eine Ligatabelle — und zählt, wie oft sie geholt wird."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.ranking_calls = 0

    async def get_ranking(self, league_id: str) -> LeagueRanking:
        self.ranking_calls += 1
        return LeagueRanking(
            league_id=league_id,
            league_name="Noob_League",
            matchday=4,
            total_matchdays=34,
            managers=(
                ManagerStanding(
                    manager_id="rival",
                    name="Erster",
                    season_points=4091,
                    matchday_points=1200,
                    rank=1,
                    team_value=Decimal(206_311_718),
                ),
                ManagerStanding(
                    manager_id="u1",
                    name="Ich",
                    season_points=3311,
                    matchday_points=589,
                    rank=2,
                    team_value=Decimal(148_592_138),
                ),
            ),
        )


async def test_ranking_reaches_the_decision_context(
    db_session: Session, vault: FernetVault
) -> None:
    """Ohne Rang und Restspieltage kann das Modell seinen Risikoappetit nicht wählen."""
    kb = _RankingKickbase()
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)

    engine = FixedDecisionEngine(TradeDecision.hold("ok"))
    await RunTickUseCase(
        session=db_session, vault=vault, kickbase=kb, engine=engine, smtp=smtp
    ).run()

    ranking = engine.contexts[0].league_ranking
    assert ranking is not None
    assert ranking.matchdays_left == 30
    me = ranking.standing_of("u1")
    assert me is not None and me.rank == 2
    # Genau ein Call pro Tick — die Tabelle wird bewusst nicht gecacht, weil sie
    # sich während eines laufenden Spieltags bewegt.
    assert kb.ranking_calls == 1


async def test_ranking_failure_does_not_cost_the_tick(
    db_session: Session, vault: FernetVault
) -> None:
    """Der Rang entscheidet über den Risikoappetit, nicht über die Regel-Compliance."""

    class _BrokenRanking(_RankingKickbase):
        async def get_ranking(self, league_id: str) -> LeagueRanking:
            raise TransportError("ranking weg")

    kb = _BrokenRanking()
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)

    engine = FixedDecisionEngine(TradeDecision.hold("ok"))
    outcome = await RunTickUseCase(
        session=db_session, vault=vault, kickbase=kb, engine=engine, smtp=smtp
    ).run()

    assert outcome.decision is not None
    assert engine.contexts[0].league_ranking is None


# -- P2-16: Aktionsketten im Deadline-Fenster ----------------------------


def _chain_decision() -> TradeDecision:
    """Verkaufen und nachkaufen — die Kette, für die P2-16 gebaut wurde."""
    return TradeDecision(
        action=TradeAction.SELL,
        reason="Konto ins Plus",
        player_id="s0",
        player_name="Verkauft",
        intent=TradeIntent.DEBT_RELIEF,
        follow_ups=(
            TradeDecision(
                action=TradeAction.BUY,
                reason="Platz nachbesetzen",
                player_id="m0",
                player_name="Gekauft",
                price=Decimal(5_000_000),
                intent=TradeIntent.POINTS,
            ),
        ),
    )


class _ChainKickbase(FakeKickbase):
    """Führt Verkauf und Kauf aus — wahlweise mit Fehler beim Verkauf."""

    def __init__(self, *, sell_fails: bool = False, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._sell_fails = sell_fails
        self.calls: list[str] = []

    async def sell_to_kickbase(self, league_id: str, player_id: str) -> None:
        self.calls.append(f"sell:{player_id}")
        if self._sell_fails:
            raise TransportError("verkauf abgelehnt")

    async def place_bid(self, league_id: str, player_id: str, price: Decimal) -> str:
        self.calls.append(f"buy:{player_id}")
        return "offer-1"


async def _run_chain(
    db_session: Session, vault: FernetVault, kb: FakeKickbase, *, dry_run: bool = False
) -> tuple[TickOutcome, list[TradeLogRow]]:
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)
    user = UserRepository(db_session).get_singleton()
    assert user is not None and user.id is not None
    settings_repo = SettingsRepository(db_session)
    row = settings_repo.get_or_default(user.id)
    row.dry_run = dry_run
    settings_repo.upsert(row)

    outcome = await RunTickUseCase(
        session=db_session,
        vault=vault,
        kickbase=kb,
        engine=FixedDecisionEngine(_chain_decision()),
        smtp=smtp,
    ).run()
    rows = TradeLogRepository(db_session).list_recent(user_id=user.id, limit=20)
    return outcome, [r for r in reversed(rows) if r.action in {"SELL", "BUY"}]


async def test_chain_executes_every_step_and_logs_each_one(
    db_session: Session, vault: FernetVault
) -> None:
    """Eine Zeile pro Aktion — `buy_history` und die offenen Gebote werden
    daraus rekonstruiert (P1-11).

    Eine gebündelte Zeile würde den Nachkauf für den nächsten Tick unsichtbar
    machen, und der Bot böte erneut: genau Defekt D3.
    """
    kb = _ChainKickbase()
    outcome, rows = await _run_chain(db_session, vault, kb)

    assert kb.calls == ["sell:s0", "buy:m0"]
    assert [r.action for r in rows] == ["SELL", "BUY"]
    assert all(r.executed for r in rows)
    assert [r.context["chain_index"] for r in rows] == [0, 1]
    # Ergebnis und Mail hängen an der Hauptaktion.
    assert outcome.decision is not None
    assert outcome.decision.action is TradeAction.SELL
    assert outcome.executed is True


async def test_chain_stops_at_the_first_failure(db_session: Session, vault: FernetVault) -> None:
    """Der Nachkauf baut auf dem Verkauf auf.

    Scheitert der Verkauf, würde Kickbase den Kauf ohnehin ablehnen — die
    Kette weiterzufahren verbrennt nur einen Tick und erzeugt Log-Rauschen.
    """
    kb = _ChainKickbase(sell_fails=True)
    _, rows = await _run_chain(db_session, vault, kb)

    assert kb.calls == ["sell:s0"]
    assert [r.action for r in rows] == ["SELL"]
    assert rows[0].executed is False
    assert rows[0].context["error"]


async def test_a_single_action_still_logs_without_a_chain_marker(
    db_session: Session, vault: FernetVault
) -> None:
    """Der gewöhnliche Tick darf sich nicht verändern.

    `chain_index` steht nur an echten Ketten — sonst müsste jede Auswertung
    des `trade_log` ein Feld mitlesen, das in 99 % der Zeilen bedeutungslos
    ist.
    """
    kb = _ChainKickbase()
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)
    engine = FixedDecisionEngine(
        TradeDecision(action=TradeAction.SELL, reason="nur eine", player_id="s0", player_name="X")
    )
    await RunTickUseCase(
        session=db_session, vault=vault, kickbase=kb, engine=engine, smtp=smtp
    ).run()

    user = UserRepository(db_session).get_singleton()
    assert user is not None and user.id is not None
    row = TradeLogRepository(db_session).list_recent(user_id=user.id, limit=1)[0]
    assert row.action == "SELL"
    assert "chain_index" not in row.context


async def test_dry_run_logs_the_whole_chain_without_executing(
    db_session: Session, vault: FernetVault
) -> None:
    """Der Shadow-Lauf muss die geplante Kette zeigen, nicht nur ihren Anfang."""
    kb = _ChainKickbase()
    _, rows = await _run_chain(db_session, vault, kb, dry_run=True)

    assert kb.calls == []
    assert [r.action for r in rows] == ["SELL", "BUY"]
    assert not any(r.executed for r in rows)


def _go_live(session: Session) -> None:
    """Schaltet `dry_run` aus.

    Der Guard-Nachlauf setzt eine **ausgeführte** Aktion voraus: im Dry-Run
    wird nichts verkauft, also reisst auch nichts ein Loch, und ein Nachlauf
    wäre eine Reparatur an einem Schaden, den es nicht gibt.
    """
    user = UserRepository(session).get_singleton()
    assert user is not None and user.id is not None
    repo = SettingsRepository(session)
    row = repo.get_or_default(user.id)
    row.dry_run = False
    repo.upsert(row)


class _FullElevenKickbase(FakeKickbase):
    """Zwölf Spieler, elf aufgestellt — und `lineup_order` ist gesetzt.

    Der Guard-Nachlauf entscheidet daran, ob ein Verkauf ein Loch reisst;
    ohne die Slot-Nummern im Kader sähe jeder Verkauf wie ein Bankverkauf aus.
    """

    def __init__(self, **kwargs: object) -> None:
        players = [
            _lineup_player("gk1", Position.GOALKEEPER),
            *[_lineup_player(f"def{i}", Position.DEFENDER) for i in range(1, 4)],
            *[_lineup_player(f"mid{i}", Position.MIDFIELDER) for i in range(1, 6)],
            *[_lineup_player(f"fwd{i}", Position.FORWARD) for i in range(1, 3)],
        ]
        # Elf Slots besetzt, der zwölfte sitzt auf der Bank.
        self._squad_players = [
            SquadPlayer(player=sp.player, lineup_order=i if i < 11 else None)
            for i, sp in enumerate(players)
        ]
        self._squad_players.append(
            SquadPlayer(player=_lineup_player("bench1", Position.DEFENDER).player)
        )
        self.squad_player_ids = tuple(sp.player.id for sp in self._squad_players)
        super().__init__(  # type: ignore[arg-type]
            lineup=Lineup(formation="3-5-2", player_ids=self.squad_player_ids[:11]), **kwargs
        )

    async def get_squad(self, league_id: str, manager_id: str) -> Squad:
        return Squad(league_id=league_id, manager_id=manager_id, players=tuple(self._squad_players))

    async def sell_to_kickbase(self, league_id: str, player_id: str) -> None:
        return None


async def test_the_guard_reruns_after_a_sale_from_the_starting_eleven(
    db_session: Session, vault: FernetVault
) -> None:
    """P2-16: Was die Entscheidung aufreisst, sah der Guard nie.

    Er läuft **vor** der Modell-Abfrage. Ein Verkauf aus der Startelf
    hinterlässt danach einen leeren Slot — 100 Punkte pro Spieltag, und im
    Deadline-Fenster gibt es keinen nächsten Tick, der das heilt.

    Das ist die Absicherung zu `follow_up_actions`: das Modell müsste für ein
    `SET_LINEUP` alle elf IDs aufzählen, und im bezahlten Lauf vom 2026-09-26
    lieferte es den `lineup`-Block prompt gar nicht mit.
    """
    kb = _FullElevenKickbase()
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)
    _go_live(db_session)

    # Verkauft einen Spieler, der in der Startelf steht (lineup_order 0).
    sold = kb.squad_player_ids[1]
    engine = FixedDecisionEngine(
        TradeDecision(
            action=TradeAction.SELL,
            reason="Konto ins Plus",
            player_id=sold,
            player_name="Startelfspieler",
        )
    )
    await RunTickUseCase(
        session=db_session,
        vault=vault,
        kickbase=kb,
        engine=engine,
        smtp=smtp,
        lineup_writes_enabled=True,
    ).run()

    user = UserRepository(db_session).get_singleton()
    assert user is not None and user.id is not None
    rows = TradeLogRepository(db_session).list_recent(user_id=user.id, limit=10)
    guard_rows = [r for r in rows if r.context.get("source") == "lineup_guard"]
    assert guard_rows, "Der Guard hat das Loch nach dem Verkauf nicht geschlossen"
    assert sold not in guard_rows[0].context["player_ids"], (
        "Der verkaufte Spieler steht wieder in der Elf"
    )


async def test_the_guard_stays_quiet_when_the_sale_came_from_the_bench(
    db_session: Session, vault: FernetVault
) -> None:
    """Ein Bankspieler reisst kein Loch — dann gibt es nichts zu reparieren.

    Ein Guard, der nach jeder Aktion schreibt, erzeugt Rauschen im `trade_log`
    und Last gegen das Rate-Limit, ohne einen Punkt zu bringen.
    """
    kb = _FullElevenKickbase()
    smtp = FakeSmtp()
    await _complete_setup(db_session, vault, kb, smtp)
    _go_live(db_session)

    bench = kb.squad_player_ids[-1]
    engine = FixedDecisionEngine(
        TradeDecision(
            action=TradeAction.SELL, reason="Bank weg", player_id=bench, player_name="Bank"
        )
    )
    await RunTickUseCase(
        session=db_session,
        vault=vault,
        kickbase=kb,
        engine=engine,
        smtp=smtp,
        lineup_writes_enabled=True,
    ).run()

    user = UserRepository(db_session).get_singleton()
    assert user is not None and user.id is not None
    rows = TradeLogRepository(db_session).list_recent(user_id=user.id, limit=10)
    assert not [r for r in rows if r.context.get("source") == "lineup_guard"]
