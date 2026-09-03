from __future__ import annotations

from decimal import Decimal

import pytest
from app.application.trade_executor import TradeExecutor
from app.domain.exceptions import ConflictError
from app.domain.trade import TradeAction, TradeDecision

from tests.application.conftest import FakeKickbase


class RecordingKickbase(FakeKickbase):
    def __init__(self) -> None:
        super().__init__()
        self.bids: list[tuple[str, str, Decimal]] = []
        self.listings: list[tuple[str, str, Decimal]] = []
        self.direct_sells: list[tuple[str, str]] = []
        self.accepts: list[tuple[str, str, str]] = []
        self.declines: list[tuple[str, str, str]] = []
        self.raise_on_bid = False

    async def place_bid(self, league_id: str, player_id: str, price: Decimal) -> str:
        if self.raise_on_bid:
            raise ConflictError("Gebot zu niedrig")
        self.bids.append((league_id, player_id, price))
        return "offer-42"

    async def list_on_market(self, league_id: str, player_id: str, price: Decimal) -> str:
        self.listings.append((league_id, player_id, price))
        return "listing-99"

    async def sell_to_kickbase(self, league_id: str, player_id: str) -> None:
        self.direct_sells.append((league_id, player_id))

    async def accept_offer(self, league_id: str, player_id: str, offer_id: str) -> None:
        self.accepts.append((league_id, player_id, offer_id))

    async def decline_offer(self, league_id: str, player_id: str, offer_id: str) -> None:
        self.declines.append((league_id, player_id, offer_id))


async def test_hold_never_touches_gateway() -> None:
    kb = RecordingKickbase()
    executor = TradeExecutor(kb, dry_run=False)
    result = await executor.execute("L1", TradeDecision.hold("nichts zu tun"))
    assert result.executed is False
    assert kb.bids == []


async def test_dry_run_skips_bid() -> None:
    kb = RecordingKickbase()
    executor = TradeExecutor(kb, dry_run=True)
    decision = TradeDecision(
        action=TradeAction.BUY, reason="test", player_id="p1", price=Decimal(1_000_000)
    )
    result = await executor.execute("L1", decision)
    assert result.executed is False
    assert "Dry-Run" in result.reason
    assert kb.bids == []


async def test_live_buy_dispatches_place_bid() -> None:
    kb = RecordingKickbase()
    executor = TradeExecutor(kb, dry_run=False)
    decision = TradeDecision(
        action=TradeAction.BUY, reason="top pick", player_id="p1", price=Decimal(1_000_000)
    )
    result = await executor.execute("L1", decision)
    assert result.executed is True
    assert result.response_ref == "offer-42"
    assert kb.bids == [("L1", "p1", Decimal(1_000_000))]


async def test_accept_offer_dispatches() -> None:
    kb = RecordingKickbase()
    executor = TradeExecutor(kb, dry_run=False)
    decision = TradeDecision(
        action=TradeAction.ACCEPT_OFFER,
        reason="nice price",
        player_id="p1",
        offer_id="off-9",
    )
    result = await executor.execute("L1", decision)
    assert result.executed is True
    assert kb.accepts == [("L1", "p1", "off-9")]


async def test_kickbase_error_becomes_non_executed_result() -> None:
    kb = RecordingKickbase()
    kb.raise_on_bid = True
    executor = TradeExecutor(kb, dry_run=False)
    decision = TradeDecision(
        action=TradeAction.BUY, reason="test", player_id="p1", price=Decimal(1_000)
    )
    result = await executor.execute("L1", decision)
    assert result.executed is False
    assert result.error is not None
    assert "zu niedrig" in result.error


async def test_live_list_on_market_dispatches_listing() -> None:
    kb = RecordingKickbase()
    executor = TradeExecutor(kb, dry_run=False)
    decision = TradeDecision(
        action=TradeAction.LIST_ON_MARKET,
        reason="listing test",
        player_id="p9",
        price=Decimal(750_000),
    )
    result = await executor.execute("L1", decision)
    assert result.executed is True
    assert result.response_ref == "listing-99"
    assert kb.listings == [("L1", "p9", Decimal(750_000))]


async def test_live_sell_dispatches_direct_sell_to_kickbase() -> None:
    kb = RecordingKickbase()
    executor = TradeExecutor(kb, dry_run=False)
    decision = TradeDecision(
        action=TradeAction.SELL,
        reason="drop underperformer",
        player_id="p9",
        price=Decimal(750_000),
    )
    result = await executor.execute("L1", decision)
    assert result.executed is True
    assert kb.direct_sells == [("L1", "p9")]
    assert kb.listings == []


async def test_dry_run_skips_direct_sell() -> None:
    kb = RecordingKickbase()
    executor = TradeExecutor(kb, dry_run=True)
    decision = TradeDecision(
        action=TradeAction.SELL, reason="x", player_id="p9", price=Decimal(500_000)
    )
    result = await executor.execute("L1", decision)
    assert result.executed is False
    assert "Dry-Run" in result.reason
    assert kb.direct_sells == []


async def test_sell_without_player_raises() -> None:
    kb = RecordingKickbase()
    executor = TradeExecutor(kb, dry_run=False)
    with pytest.raises(ValueError, match="SELL"):
        await executor.execute("L1", TradeDecision(action=TradeAction.SELL, reason="s"))


async def test_list_without_price_raises() -> None:
    kb = RecordingKickbase()
    executor = TradeExecutor(kb, dry_run=False)
    with pytest.raises(ValueError, match="LIST_ON_MARKET"):
        await executor.execute(
            "L1",
            TradeDecision(action=TradeAction.LIST_ON_MARKET, reason="s", player_id="p1"),
        )


async def test_buy_without_price_raises() -> None:
    kb = RecordingKickbase()
    executor = TradeExecutor(kb, dry_run=False)
    with pytest.raises(ValueError, match="BUY"):
        await executor.execute(
            "L1", TradeDecision(action=TradeAction.BUY, reason="x", player_id="p1")
        )
