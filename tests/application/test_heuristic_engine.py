"""Unit-Tests für die HeuristicDecisionEngine (Phase 4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.application.decision_engine import DecisionContext
from app.application.heuristic_engine import HeuristicDecisionEngine
from app.domain.exceptions import TransportError
from app.domain.models import (
    LeagueMe,
    MarketOffer,
    MarketPlayer,
    MarketValuePoint,
    Player,
    PlayerStatus,
    Position,
    Squad,
    SquadPlayer,
)
from app.domain.trade import TradeAction

LEAGUE_ID = "L1"
MANAGER_ID = "M1"


class FakeHistoryGateway:
    """Minimaler Fake — die Engine liest ausschließlich `get_market_value_history`."""

    def __init__(
        self,
        histories: dict[str, list[MarketValuePoint]] | None = None,
        *,
        fail_ids: set[str] | None = None,
    ) -> None:
        self._histories = histories or {}
        self._fail = fail_ids or set()
        self.calls: list[str] = []

    async def get_market_value_history(
        self, league_id: str, player_id: str, days: int = 7
    ) -> list[MarketValuePoint]:
        del league_id, days
        self.calls.append(player_id)
        if player_id in self._fail:
            raise TransportError("history down")
        return list(self._histories.get(player_id, []))


def _player(
    pid: str,
    *,
    avg: float = 6.0,
    status: PlayerStatus = PlayerStatus.FIT,
    market_value: Decimal = Decimal("2000000"),
    name: str = "Spieler",
) -> Player:
    return Player(
        id=pid,
        first_name="V",
        last_name=name,
        team_id="t1",
        position=Position.MIDFIELDER,
        status=status,
        market_value=market_value,
        average_points=avg,
        total_points=0,
    )


def _market(
    player: Player,
    *,
    price: Decimal,
    seller_id: str | None = None,
    offers: tuple[MarketOffer, ...] = (),
) -> MarketPlayer:
    return MarketPlayer(
        player=player, price=price, expires_at=None, seller_id=seller_id, offers=offers
    )


def _squad(players: list[SquadPlayer]) -> Squad:
    return Squad(league_id=LEAGUE_ID, manager_id=MANAGER_ID, players=tuple(players))


def _padded_squad(players: list[SquadPlayer], *, target_size: int = 13) -> Squad:
    """Ergänzt neutrale Reserve-Spieler, damit die Startelf-Regel nicht greift."""
    extras: list[SquadPlayer] = []
    needed = max(0, target_size - len(players))
    for i in range(needed):
        extras.append(
            SquadPlayer(
                player=_player(
                    f"filler-{i}", avg=5.0, market_value=Decimal("1000000"), name=f"Filler{i}"
                )
            )
        )
    return _squad(players + extras)


def _context(
    *,
    squad: Squad | None = None,
    market: tuple[MarketPlayer, ...] = (),
    budget: Decimal = Decimal("10000000"),
    min_action_score: float = 0.5,
    max_trade_pct: float = 0.5,
    min_cash_reserve: int = 0,
    blacklist: tuple[str, ...] = (),
    team_value: Decimal = Decimal("50000000"),
) -> DecisionContext:
    squad = squad or _squad([])
    return DecisionContext(
        league_id=LEAGUE_ID,
        league_me=LeagueMe(league_id=LEAGUE_ID, budget=budget),
        squad=squad,
        market=market,
        budget=budget,
        min_action_score=min_action_score,
        max_trade_pct=max_trade_pct,
        min_cash_reserve=min_cash_reserve,
        blacklist=blacklist,
        team_value=team_value,
    )


def _rising_history(pid_price: int) -> list[MarketValuePoint]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        MarketValuePoint(day=start, value=Decimal(pid_price)),
        MarketValuePoint(day=start + timedelta(days=6), value=Decimal(int(pid_price * 1.1))),
    ]


async def test_hold_when_no_squad_and_no_market() -> None:
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    decision = await engine.decide(_context())
    assert decision.action is TradeAction.HOLD
    assert "keine Aktion" in decision.reason


async def test_hold_when_best_utility_below_threshold() -> None:
    # Nur ein mittelmäßiger Marktspieler, min_action_score sehr hoch.
    market_player = _player("m1", avg=4.0, market_value=Decimal("3000000"))
    market = (_market(market_player, price=Decimal("3000000")),)
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    decision = await engine.decide(_context(market=market, min_action_score=0.95))
    assert decision.action is TradeAction.HOLD
    assert "Utility" in decision.reason


async def test_buy_best_market_candidate() -> None:
    strong = _player("m1", avg=12.0, market_value=Decimal("2000000"), name="Strong")
    weak = _player("m2", avg=2.0, market_value=Decimal("5000000"), name="Weak")
    market = (
        _market(strong, price=Decimal("2000000")),
        _market(weak, price=Decimal("5000000")),
    )
    engine = HeuristicDecisionEngine(
        FakeHistoryGateway(histories={"m1": _rising_history(2_000_000)})
    )
    decision = await engine.decide(_context(market=market, min_action_score=0.3))
    assert decision.action is TradeAction.BUY
    assert decision.player_id == "m1"
    assert decision.price == Decimal("2000000")


async def test_blacklist_blocks_buy() -> None:
    strong = _player("m1", avg=12.0, market_value=Decimal("2000000"), name="Strong")
    market = (_market(strong, price=Decimal("2000000")),)
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    decision = await engine.decide(_context(market=market, min_action_score=0.3, blacklist=("m1",)))
    # Ohne Squad und blockierten Kauf → HOLD, weil keine Aktion.
    assert decision.action is TradeAction.HOLD


async def test_max_trade_pct_blocks_buy() -> None:
    strong = _player("m1", avg=12.0, market_value=Decimal("5000000"), name="Strong")
    market = (_market(strong, price=Decimal("5000000")),)
    # Budget 10 Mio, max_trade_pct 0.1 → 1 Mio spendable → Preis 5 Mio zu teuer.
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    decision = await engine.decide(
        _context(
            market=market,
            budget=Decimal("10000000"),
            max_trade_pct=0.1,
            min_action_score=0.3,
        )
    )
    assert decision.action is TradeAction.HOLD


async def test_min_cash_reserve_blocks_buy() -> None:
    strong = _player("m1", avg=12.0, market_value=Decimal("5000000"), name="Strong")
    market = (_market(strong, price=Decimal("5000000")),)
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    decision = await engine.decide(
        _context(
            market=market,
            budget=Decimal("6000000"),
            max_trade_pct=1.0,
            min_cash_reserve=2_000_000,
            min_action_score=0.3,
        )
    )
    # spendable = min(6M, 6M-2M)=4M < Preis 5M → kein Kauf möglich.
    assert decision.action is TradeAction.HOLD


async def test_injured_market_player_is_skipped() -> None:
    injured = _player(
        "m1",
        avg=12.0,
        status=PlayerStatus.INJURED,
        market_value=Decimal("2000000"),
        name="Hurt",
    )
    market = (_market(injured, price=Decimal("2000000")),)
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    decision = await engine.decide(_context(market=market, min_action_score=0.3))
    assert decision.action is TradeAction.HOLD


async def test_player_already_in_squad_not_bought_again() -> None:
    dup = _player("m1", avg=12.0, market_value=Decimal("2000000"))
    market = (_market(dup, price=Decimal("2000000")),)
    squad = _squad([SquadPlayer(player=dup)])
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    decision = await engine.decide(_context(squad=squad, market=market, min_action_score=0.3))
    # Der eigene Spieler taucht nicht als Kauf-Kandidat auf; SELL wäre hier
    # unattraktiv (guter Spieler) → HOLD.
    assert decision.action is not TradeAction.BUY


async def test_sell_worst_squad_player() -> None:
    weak = _player("s1", avg=1.0, market_value=Decimal("500000"), name="Weak")
    squad = _padded_squad([SquadPlayer(player=weak)])
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    decision = await engine.decide(_context(squad=squad, min_action_score=0.3))
    assert decision.action is TradeAction.SELL
    assert decision.player_id == "s1"


async def test_accept_offer_when_price_above_market_value() -> None:
    keeper = _player("k1", avg=8.0, market_value=Decimal("4000000"), name="Keeper")
    squad = _padded_squad([SquadPlayer(player=keeper)])
    high_offer = MarketOffer(
        id="o1", user_id="u2", user_name="Rival", price=Decimal("6000000"), valid_until=None
    )
    market = (
        _market(keeper, price=Decimal("4000000"), seller_id=MANAGER_ID, offers=(high_offer,)),
    )
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    decision = await engine.decide(_context(squad=squad, market=market, min_action_score=0.4))
    assert decision.action is TradeAction.ACCEPT_OFFER
    assert decision.offer_id == "o1"
    assert decision.player_id == "k1"


async def test_decline_offer_when_price_below_market_and_squad_score_high() -> None:
    keeper = _player("k1", avg=12.0, market_value=Decimal("4000000"), name="Keeper")
    squad = _padded_squad([SquadPlayer(player=keeper)])
    low_offer = MarketOffer(
        id="o1", user_id="u2", user_name="Rival", price=Decimal("2000000"), valid_until=None
    )
    market = (_market(keeper, price=Decimal("4000000"), seller_id=MANAGER_ID, offers=(low_offer,)),)
    engine = HeuristicDecisionEngine(
        FakeHistoryGateway(histories={"k1": _rising_history(4_000_000)})
    )
    decision = await engine.decide(_context(squad=squad, market=market, min_action_score=0.6))
    assert decision.action is TradeAction.DECLINE_OFFER
    assert decision.offer_id == "o1"


async def test_history_error_falls_back_to_neutral_trend() -> None:
    strong = _player("m1", avg=12.0, market_value=Decimal("2000000"), name="Strong")
    market = (_market(strong, price=Decimal("2000000")),)
    gateway = FakeHistoryGateway(fail_ids={"m1"})
    engine = HeuristicDecisionEngine(gateway)
    decision = await engine.decide(_context(market=market, min_action_score=0.3))
    # Trend fällt auf 0.5 zurück — Buy bleibt trotzdem attraktiv.
    assert decision.action is TradeAction.BUY
    assert "m1" in gateway.calls


async def test_top_n_prefilter_bounds_history_calls() -> None:
    gateway = FakeHistoryGateway()
    engine = HeuristicDecisionEngine(gateway, max_market_candidates=3)
    market = tuple(
        _market(
            _player(f"m{i}", avg=8.0, market_value=Decimal("1000000")),
            price=Decimal("1000000"),
        )
        for i in range(20)
    )
    await engine.decide(_context(market=market, min_action_score=0.3))
    assert len(gateway.calls) <= 3


async def test_buy_over_33_percent_limit_falls_below_threshold() -> None:
    # Offene Gebote schieben das effektive Konto weit ins Minus — die
    # 33 %-Regel drückt die BUY-Utility unter die Aktions-Schwelle, obwohl
    # der Prefilter (max_trade_pct) den Kauf noch durchlässt.
    strong = _player("m1", avg=12.0, market_value=Decimal("3000000"), name="Strong")
    market = (_market(strong, price=Decimal("3000000")),)
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    context = DecisionContext(
        league_id=LEAGUE_ID,
        league_me=LeagueMe(league_id=LEAGUE_ID, budget=Decimal("10000000")),
        squad=_padded_squad([]),
        market=market,
        budget=Decimal("10000000"),
        min_action_score=0.5,
        max_trade_pct=1.0,
        min_cash_reserve=0,
        blacklist=(),
        team_value=Decimal("10000000"),
        open_bids_total=Decimal("50000000"),
    )
    decision = await engine.decide(context)
    assert decision.action is TradeAction.HOLD
    assert "Utility" in decision.reason


async def test_dynamic_threshold_lets_marginal_action_through_near_deadline() -> None:
    # Mittelmäßiger Kandidat: Utility knapp unter der Basis-Schwelle.
    ok_player = _player("m1", avg=6.0, market_value=Decimal("2000000"), name="Ok")
    market = (_market(ok_player, price=Decimal("2000000")),)
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    now = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)

    # Weit vor Anpfiff → volle Schwelle greift → HOLD.
    far_deadline = now + timedelta(days=3)
    context_far = DecisionContext(
        league_id=LEAGUE_ID,
        league_me=LeagueMe(league_id=LEAGUE_ID, budget=Decimal("10000000")),
        squad=_padded_squad([]),
        market=market,
        budget=Decimal("10000000"),
        min_action_score=0.65,
        max_trade_pct=1.0,
        min_cash_reserve=0,
        blacklist=(),
        team_value=Decimal("50000000"),
        now=now,
        next_matchday_start=far_deadline,
        interval_min=120,
    )
    decision_far = await engine.decide(context_far)
    assert decision_far.action is TradeAction.HOLD

    # Gleicher Kandidat, nur noch 30 min bis Anpfiff → Schwelle sinkt Richtung
    # Urgency-Floor → BUY geht durch.
    near_deadline = now + timedelta(minutes=30)
    context_near = DecisionContext(
        league_id=LEAGUE_ID,
        league_me=LeagueMe(league_id=LEAGUE_ID, budget=Decimal("10000000")),
        squad=_padded_squad([]),
        market=market,
        budget=Decimal("10000000"),
        min_action_score=0.65,
        max_trade_pct=1.0,
        min_cash_reserve=0,
        blacklist=(),
        team_value=Decimal("50000000"),
        now=now,
        next_matchday_start=near_deadline,
        interval_min=120,
    )
    decision_near = await engine.decide(context_near)
    assert decision_near.action is TradeAction.BUY


async def test_sell_debt_relief_wins_over_neutral_hold() -> None:
    # Konto im Minus, ein mittelmäßiger Squad-Spieler mit ordentlichem Preis
    # bekommt Debt-Relief-Bonus und schlägt HOLD.
    weak = _player("s1", avg=4.0, market_value=Decimal("8000000"), name="Weak")
    squad = _padded_squad([SquadPlayer(player=weak)])
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    decision = await engine.decide(
        _context(
            squad=squad,
            budget=Decimal("-5000000"),
            team_value=Decimal("50000000"),
            min_action_score=0.5,
        )
    )
    assert decision.action is TradeAction.SELL
    assert decision.player_id == "s1"
    assert "Debt-Relief" in decision.reason


@pytest.mark.parametrize("status", [PlayerStatus.RED_CARD, PlayerStatus.OUT_OF_SQUAD])
async def test_various_unfit_statuses_are_filtered(status: PlayerStatus) -> None:
    unfit = _player("m1", avg=12.0, status=status, market_value=Decimal("2000000"))
    market = (_market(unfit, price=Decimal("2000000")),)
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    decision = await engine.decide(_context(market=market, min_action_score=0.3))
    assert decision.action is TradeAction.HOLD
