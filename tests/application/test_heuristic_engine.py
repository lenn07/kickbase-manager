"""Unit-Tests für die HeuristicDecisionEngine (Phase 4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.application.decision_engine import BuyRecord, DecisionContext, ListingRecord
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
from app.domain.trade import TradeAction, TradeIntent

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
    position: Position = Position.MIDFIELDER,
) -> Player:
    return Player(
        id=pid,
        first_name="V",
        last_name=name,
        team_id="t1",
        position=position,
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
    """Ergänzt neutrale Reserve-Spieler, damit die Startelf-Regel nicht greift.

    Die Filler sind bewusst stark (hoher Ø-Punktwert) UND auf mehrere Positionen
    verteilt (GK/DEF/MID/ATT im Rotationsmuster). Dadurch entsteht auf keiner
    Position ein extremer Überschuss, sodass die Filler nicht als SELL/LIST-
    Kandidaten die Test-Aussagen der individuellen Spieler verfälschen.
    """
    extras: list[SquadPlayer] = []
    needed = max(0, target_size - len(players))
    rotation = (
        Position.DEFENDER,
        Position.MIDFIELDER,
        Position.FORWARD,
        Position.DEFENDER,
        Position.MIDFIELDER,
        Position.FORWARD,
        Position.GOALKEEPER,
    )
    for i in range(needed):
        extras.append(
            SquadPlayer(
                player=_player(
                    f"filler-{i}",
                    avg=150.0,
                    market_value=Decimal("1000000"),
                    name=f"Filler{i}",
                    position=rotation[i % len(rotation)],
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
    buy_history: dict[str, BuyRecord] | None = None,
    own_listings: dict[str, ListingRecord] | None = None,
    now: datetime | None = None,
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
        buy_history=buy_history or {},
        own_listings=own_listings or {},
        now=now,
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
    # Overbid-Cap: höchstens +15 % über Marktwert (bei hoher Utility).
    assert Decimal("2000000") <= decision.price <= Decimal("2300000")
    # Bei leerem Squad ist "Kader füllen" der dominante Grund.
    assert decision.intent is not None
    assert decision.intent.value in {"SQUAD_FILL", "PROFIT", "POINTS"}


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


async def test_list_worst_squad_player_by_default() -> None:
    # Ohne Debt-Relief oder Deadline gewinnt das Listing (+10 % Wunschpreis)
    # gegenüber dem Direktverkauf, weil der Erwartungswert-Bonus positiv ist.
    weak = _player("s1", avg=1.0, market_value=Decimal("500000"), name="Weak")
    squad = _padded_squad([SquadPlayer(player=weak)])
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    decision = await engine.decide(_context(squad=squad, min_action_score=0.3))
    assert decision.action is TradeAction.LIST_ON_MARKET
    assert decision.player_id == "s1"
    # Wunschpreis = Marktwert * 1.10 (auf ganze Euro gerundet).
    assert decision.price == Decimal("550000")


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
    # 33 %-Regel drückt die BUY-Utility unter die Aktions-Schwelle. Der
    # Bot darf in diesem Tick keinen weiteren BUY ausführen (SELL-Optionen
    # bleiben erlaubt, weil sie Cash freimachen und die Situation entschärfen).
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
    assert decision.action is not TradeAction.BUY


async def test_dynamic_threshold_lets_marginal_action_through_near_deadline() -> None:
    # Mittelmäßiger Kandidat: Utility knapp unter der Basis-Schwelle 0.65
    # (avg=50 → form≈0.38, PE geclippt auf 1.0 → Score ~0.58).
    ok_player = _player("m1", avg=50.0, market_value=Decimal("2000000"), name="Ok")
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
    # Deadline-Skalierung senkt die Schwelle → mindestens EINE Nicht-HOLD-Aktion
    # kommt durch. Welche konkret (BUY oder LIST_ON_MARKET) hängt von der
    # Konkurrenz im Sell-Pfad ab; Kernaussage des Tests ist die Urgency-Skala.
    assert decision_near.action is not TradeAction.HOLD


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


async def test_buy_intent_is_squad_fill_when_kader_klein() -> None:
    strong = _player("m1", avg=12.0, market_value=Decimal("2000000"), name="Strong")
    market = (_market(strong, price=Decimal("2000000")),)
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    # Squad leer → definitiv SQUAD_FILL
    decision = await engine.decide(_context(market=market, min_action_score=0.3))
    assert decision.action is TradeAction.BUY
    assert decision.intent is TradeIntent.SQUAD_FILL


async def test_buy_intent_is_profit_on_strong_uptrend_in_full_squad() -> None:
    # Voller Kader (>= 13 Spieler) + starker Uptrend → PROFIT-Intent.
    riser = _player("m1", avg=6.0, market_value=Decimal("2000000"), name="Riser")
    market = (_market(riser, price=Decimal("2000000")),)
    engine = HeuristicDecisionEngine(
        FakeHistoryGateway(histories={"m1": _rising_history(2_000_000)})
    )
    squad = _padded_squad([], target_size=14)
    decision = await engine.decide(_context(squad=squad, market=market, min_action_score=0.3))
    assert decision.action is TradeAction.BUY
    assert decision.intent is TradeIntent.PROFIT


async def test_overbid_caps_at_15_percent_and_respects_spendable() -> None:
    strong = _player("m1", avg=12.0, market_value=Decimal("2000000"), name="Strong")
    market = (_market(strong, price=Decimal("2000000")),)
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    decision = await engine.decide(
        _context(
            market=market,
            budget=Decimal("10000000"),
            max_trade_pct=1.0,
            min_action_score=0.3,
        )
    )
    assert decision.action is TradeAction.BUY
    # Marktpreis 2M, max Overbid +15 % → höchstens 2,3M.
    assert decision.price <= Decimal("2300000")
    assert decision.price >= Decimal("2000000")


async def test_overbid_never_exceeds_spendable_budget() -> None:
    strong = _player("m1", avg=12.0, market_value=Decimal("2000000"), name="Strong")
    market = (_market(strong, price=Decimal("2000000")),)
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    # spendable = 2,1M — Overbid würde eigentlich 2,3M sein, muss aber gecappt werden.
    decision = await engine.decide(
        _context(
            market=market,
            budget=Decimal("2100000"),
            max_trade_pct=1.0,
            min_action_score=0.3,
        )
    )
    assert decision.action is TradeAction.BUY
    assert decision.price <= Decimal("2100000")


async def test_profit_exit_wins_over_hold_when_gain_material() -> None:
    # Squad-Spieler mit ordentlichem Score, gekauft für 4M, jetzt 6M wert → +50 %.
    # PROFIT-Exit realisiert der Bot standardmäßig via Listing zum Wunschpreis
    # (bringt weitere +10 % vs. Direktverkauf).
    keeper = _player("k1", avg=8.0, market_value=Decimal("6000000"), name="Riser")
    squad = _padded_squad([SquadPlayer(player=keeper)], target_size=13)
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    buy_history = {
        "k1": BuyRecord(intent=TradeIntent.PROFIT, buy_price=Decimal("4000000")),
    }
    decision = await engine.decide(
        _context(
            squad=squad,
            min_action_score=0.55,
            buy_history=buy_history,
        )
    )
    assert decision.action is TradeAction.LIST_ON_MARKET
    assert decision.player_id == "k1"
    assert decision.intent is TradeIntent.PROFIT
    assert "PROFIT-Exit" in decision.reason
    # Wunschpreis = 6M * 1.10 = 6,6M.
    assert decision.price == Decimal("6600000")


async def test_profit_exit_ignored_when_gain_too_small() -> None:
    keeper = _player("k1", avg=8.0, market_value=Decimal("4200000"), name="Slow")
    squad = _padded_squad([SquadPlayer(player=keeper)], target_size=13)
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    buy_history = {
        "k1": BuyRecord(intent=TradeIntent.PROFIT, buy_price=Decimal("4000000")),
    }
    # +5 % Gewinn ist unterhalb des 10 %-Trigger → kein PROFIT-Bonus, weniger als
    # der ohnehin nötige Aktions-Score → HOLD.
    decision = await engine.decide(
        _context(
            squad=squad,
            min_action_score=0.6,
            buy_history=buy_history,
        )
    )
    # Kein PROFIT-Exit — Reason enthält "PROFIT-Exit" nicht.
    assert "PROFIT-Exit" not in (decision.reason or "")


async def test_debt_relief_sell_gets_debt_intent() -> None:
    # Konto im Minus, Verkauf greift → Intent muss DEBT_RELIEF sein.
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
    assert decision.intent is TradeIntent.DEBT_RELIEF


async def test_debt_relief_prefers_direct_sell_over_listing() -> None:
    # Bei negativem Konto ist der Listing-Debt-Relief-Bonus halbiert (Cash
    # kommt erst in 24 h), während der Direktverkauf sofort Liquidität bringt
    # → SELL muss die LIST-Option schlagen.
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
    # Reason muss den Cash-Delay-Malus für den nicht gewählten Listing-Weg
    # nicht enthalten, aber Debt-Relief muss den Ausschlag geben.
    assert "Debt-Relief" in decision.reason


async def test_already_listed_player_gets_no_duplicate_sell_candidate() -> None:
    # Ein Spieler, der bereits gelistet ist, darf nicht erneut als
    # LIST/SELL-Kandidat auftauchen — der Bot würde sonst zwei Listings anlegen.
    keeper = _player("k1", avg=1.0, market_value=Decimal("500000"), name="Weak")
    squad = _padded_squad([SquadPlayer(player=keeper)])
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    now = datetime(2026, 3, 5, 12, 0, tzinfo=UTC)
    listings = {
        "k1": ListingRecord(
            player_id="k1",
            listing_price=Decimal("550000"),
            listed_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=23),
            has_offers=False,
        )
    }
    context = _context(
        squad=squad,
        min_action_score=0.3,
        own_listings=listings,
        now=now,
    )
    candidates = await engine.propose(context)
    for cand in candidates:
        assert cand.decision.player_id != "k1", (
            f"Spieler k1 ist bereits gelistet — es darf kein neuer "
            f"{cand.decision.action.value}-Kandidat entstehen (Kandidat-ID {cand.id})."
        )


async def test_stale_listing_triggers_direct_sell_fallback() -> None:
    # Listing seit ≥24 h offen und ohne Angebote → Priority-SELL an Kickbase.
    keeper = _player("k1", avg=6.0, market_value=Decimal("500000"), name="Slow")
    squad = _padded_squad([SquadPlayer(player=keeper)])
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    now = datetime(2026, 3, 5, 12, 0, tzinfo=UTC)
    listings = {
        "k1": ListingRecord(
            player_id="k1",
            listing_price=Decimal("550000"),
            listed_at=now - timedelta(hours=25),
            expires_at=None,
            has_offers=False,
        )
    }
    decision = await engine.decide(
        _context(
            squad=squad,
            min_action_score=0.5,
            own_listings=listings,
            now=now,
        )
    )
    assert decision.action is TradeAction.SELL
    assert decision.player_id == "k1"
    assert "Stale-Listing" in decision.reason


async def test_stale_listing_survives_naive_listed_at_from_sqlite() -> None:
    # SQLite gibt gespeicherte Timestamps ohne TZ-Info zurück (naive).
    # `_stale_reason` muss das gegen den aware `context.now` normalisieren,
    # sonst crasht die Subtraktion mit TypeError.
    keeper = _player("k1", avg=6.0, market_value=Decimal("500000"), name="Slow")
    squad = _padded_squad([SquadPlayer(player=keeper)])
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    now = datetime(2026, 3, 5, 12, 0, tzinfo=UTC)
    naive_listed_at = (now - timedelta(hours=25)).replace(tzinfo=None)
    listings = {
        "k1": ListingRecord(
            player_id="k1",
            listing_price=Decimal("550000"),
            listed_at=naive_listed_at,
            expires_at=None,
            has_offers=False,
        )
    }
    decision = await engine.decide(
        _context(
            squad=squad,
            min_action_score=0.5,
            own_listings=listings,
            now=now,
        )
    )
    assert decision.action is TradeAction.SELL


async def test_stale_listing_with_open_offer_does_not_fallback() -> None:
    # Solange ein Manager-Angebot offen ist, entscheidet ACCEPT/DECLINE —
    # der Stale-Fallback darf nicht drüberbügeln.
    keeper = _player("k1", avg=6.0, market_value=Decimal("4000000"), name="Slow")
    squad = _padded_squad([SquadPlayer(player=keeper)])
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    now = datetime(2026, 3, 5, 12, 0, tzinfo=UTC)
    listings = {
        "k1": ListingRecord(
            player_id="k1",
            listing_price=Decimal("4400000"),
            listed_at=now - timedelta(hours=25),
            expires_at=now + timedelta(minutes=30),
            has_offers=True,
        )
    }
    high_offer = MarketOffer(
        id="o1", user_id="u2", user_name="Rival", price=Decimal("5000000"), valid_until=None
    )
    market = (
        _market(keeper, price=Decimal("4400000"), seller_id=MANAGER_ID, offers=(high_offer,)),
    )
    decision = await engine.decide(
        _context(
            squad=squad,
            market=market,
            min_action_score=0.4,
            own_listings=listings,
            now=now,
        )
    )
    assert decision.action is TradeAction.ACCEPT_OFFER


# -- Positions- und Torwart-Regeln ---------------------------------------


def _balanced_squad(*extras: SquadPlayer) -> Squad:
    """Baut einen Squad mit Mindestbesetzung (1 GK + 3 DEF + 3 MID + 1 ATT + extras)."""

    base: list[SquadPlayer] = []
    base.append(
        SquadPlayer(
            player=_player(
                "gk1",
                avg=6.0,
                market_value=Decimal("2000000"),
                name="Keeper",
                position=Position.GOALKEEPER,
            )
        )
    )
    for i in range(3):
        base.append(
            SquadPlayer(
                player=_player(
                    f"def{i}",
                    avg=5.0,
                    market_value=Decimal("1500000"),
                    name=f"Def{i}",
                    position=Position.DEFENDER,
                )
            )
        )
    for i in range(3):
        base.append(
            SquadPlayer(
                player=_player(
                    f"mid{i}",
                    avg=5.0,
                    market_value=Decimal("1500000"),
                    name=f"Mid{i}",
                    position=Position.MIDFIELDER,
                )
            )
        )
    base.append(
        SquadPlayer(
            player=_player(
                "att1",
                avg=6.0,
                market_value=Decimal("2000000"),
                name="Att",
                position=Position.FORWARD,
            )
        )
    )
    return _squad(base + list(extras))


async def test_last_goalkeeper_is_not_sold_even_when_score_is_low() -> None:
    # Mindestbesetzung mit einem einzigen (schwachen) Torwart. Der Bot darf
    # ihn nicht verkaufen — Kickbase würde die GK-Position leer lassen (-100).
    # Der Kandidat kann noch entstehen, muss aber durch den Positions-Loch-
    # Malus auf Utility 0 gedrückt werden und darf nicht als beste Aktion
    # gewählt werden.
    weak_gk = SquadPlayer(
        player=_player(
            "gk1",
            avg=1.0,
            market_value=Decimal("500000"),
            name="WeakKeeper",
            position=Position.GOALKEEPER,
        )
    )
    extras = [
        SquadPlayer(
            player=_player(
                f"pad{i}",
                avg=6.0,
                market_value=Decimal("1000000"),
                name=f"Pad{i}",
                position=Position.MIDFIELDER,
            )
        )
        for i in range(4)
    ]
    base_squad = _balanced_squad(*extras)
    players = (weak_gk, *(sp for sp in base_squad.players if sp.player.id != "gk1"))
    squad = Squad(league_id=LEAGUE_ID, manager_id=MANAGER_ID, players=players)
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    context = _context(squad=squad, min_action_score=0.3)
    decision = await engine.decide(context)
    assert decision.player_id != "gk1", (
        f"Der einzige GK darf nicht verkauft werden — Engine wählte gk1 als "
        f"{decision.action.value}."
    )
    # Zusätzlich: der gk1-SELL/LIST-Kandidat muss auf Utility 0 gedrückt sein
    # (Positions-Loch-Malus greift) und einen entsprechenden Reason tragen.
    candidates = await engine.propose(context)
    gk_candidates = [
        c
        for c in candidates
        if c.decision.player_id == "gk1"
        and c.decision.action in {TradeAction.SELL, TradeAction.LIST_ON_MARKET}
    ]
    assert gk_candidates, "GK-SELL/LIST-Kandidat sollte existieren, aber mit Utility 0."
    for cand in gk_candidates:
        assert cand.utility == 0.0
        assert "Positions-Loch" in cand.decision.reason


async def test_second_goalkeeper_buy_is_dampened_without_profit_signal() -> None:
    # Squad hat schon 1 GK — ein zweiter GK ohne PROFIT-Signal bekommt Overstock-Malus.
    strong_gk_2 = _player(
        "gk2",
        avg=8.0,
        market_value=Decimal("3000000"),
        name="Second",
        position=Position.GOALKEEPER,
    )
    strong_mid = _player(
        "m1",
        avg=8.0,
        market_value=Decimal("3000000"),
        name="Mid",
        position=Position.MIDFIELDER,
    )
    market = (
        _market(strong_gk_2, price=Decimal("3000000")),
        _market(strong_mid, price=Decimal("3000000")),
    )
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    decision = await engine.decide(
        _context(squad=_balanced_squad(), market=market, min_action_score=0.3)
    )
    if decision.action is TradeAction.BUY:
        assert decision.player_id != "gk2", (
            "2. GK sollte durch Overstock-Malus hinter dem MID-Kandidaten landen."
        )


async def test_second_goalkeeper_gets_position_surplus_bonus() -> None:
    # 2 GK im Kader — beide GK-Verkaufskandidaten müssen den
    # Positions-Überschuss-Bonus tragen (ohne Positions-Loch-Malus), damit die
    # Engine sie überhaupt gegen andere Kandidaten in Konkurrenz stellt.
    # (Welcher der beiden am Ende gewinnt, entscheidet der reine Score — das
    # testet der Domain-Test `test_sell_surplus_position_gets_small_bonus`.)
    strong_gk = SquadPlayer(
        player=_player(
            "gk1",
            avg=8.0,
            market_value=Decimal("3000000"),
            name="Strong",
            position=Position.GOALKEEPER,
        )
    )
    weak_gk = SquadPlayer(
        player=_player(
            "gk2",
            avg=2.0,
            market_value=Decimal("500000"),
            name="Weak",
            position=Position.GOALKEEPER,
        )
    )
    # Vier zusätzliche MID-Spieler bringen die Squad-Größe auf 13 — damit
    # der Startelf-Malus (unter 11) nicht die GK-Reasons überschreibt.
    extras = tuple(
        SquadPlayer(
            player=_player(
                f"pad{i}",
                avg=6.0,
                market_value=Decimal("1500000"),
                name=f"Pad{i}",
                position=Position.MIDFIELDER,
            )
        )
        for i in range(4)
    )
    base_squad = _balanced_squad(weak_gk, *extras)
    players = (strong_gk, *(sp for sp in base_squad.players if sp.player.id != "gk1"))
    squad = Squad(league_id=LEAGUE_ID, manager_id=MANAGER_ID, players=players)
    engine = HeuristicDecisionEngine(FakeHistoryGateway())
    candidates = await engine.propose(_context(squad=squad, min_action_score=0.3))
    for pid in ("gk1", "gk2"):
        gk_candidates = [
            c
            for c in candidates
            if c.decision.player_id == pid
            and c.decision.action in {TradeAction.SELL, TradeAction.LIST_ON_MARKET}
        ]
        assert gk_candidates, f"SELL/LIST-Kandidat für {pid} muss existieren."
        for cand in gk_candidates:
            assert "Positions-Überschuss" in cand.decision.reason, (
                f"GK-Verkauf-Kandidat {cand.id} sollte den Positions-Überschuss-Bonus "
                f"tragen (2. GK ist entbehrlich)."
            )
            assert "Positions-Loch" not in cand.decision.reason
