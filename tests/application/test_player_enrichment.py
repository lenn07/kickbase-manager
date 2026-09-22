"""Unit-Tests für den PlayerEnricher (AI-Only-Modus, Master-Prompt-Signale)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.application.player_enrichment import PlayerEnricher
from app.domain.exceptions import TransportError
from app.domain.models import (
    MarketPlayer,
    MarketValuePoint,
    Player,
    PlayerStatus,
    Position,
    Squad,
    SquadPlayer,
)

LEAGUE_ID = "L1"


class FakeKickbase:
    """Kickbase-Gateway-Fake — der Enricher ruft nur `get_market_value_history`."""

    def __init__(
        self,
        *,
        history: dict[str, list[MarketValuePoint]] | None = None,
        raise_for: set[str] | None = None,
    ) -> None:
        self.history = history or {}
        self.raise_for = raise_for or set()
        self.calls: list[str] = []

    async def get_market_value_history(
        self, league_id: str, player_id: str, days: int = 7
    ) -> list[MarketValuePoint]:
        del league_id, days
        self.calls.append(player_id)
        if player_id in self.raise_for:
            raise TransportError("no history")
        return self.history.get(player_id, [])


def _player(
    pid: str,
    *,
    status: PlayerStatus = PlayerStatus.FIT,
    avg: float = 130.0,
    market_value: int = 5_000_000,
) -> Player:
    return Player(
        id=pid,
        first_name="Vor",
        last_name=f"Name{pid}",
        team_id="T1",
        position=Position.MIDFIELDER,
        status=status,
        market_value=Decimal(market_value),
        average_points=avg,
        total_points=int(avg) * 20,
    )


def _squad(*players: Player) -> Squad:
    return Squad(
        league_id=LEAGUE_ID,
        manager_id="M1",
        players=tuple(SquadPlayer(player=p) for p in players),
    )


def _market_player(pid: str, *, market_value: int = 10_000_000) -> MarketPlayer:
    return MarketPlayer(
        player=_player(pid, market_value=market_value),
        price=Decimal(market_value),
        expires_at=datetime.now(UTC) + timedelta(hours=6),
        seller_id="other",
        offers=(),
    )


def _history(*values: int) -> list[MarketValuePoint]:
    base = datetime.now(UTC) - timedelta(days=len(values))
    return [
        MarketValuePoint(day=base + timedelta(days=i), value=Decimal(v))
        for i, v in enumerate(values)
    ]


async def test_enrichment_covers_squad_and_top_market() -> None:
    squad_players = [_player("s1"), _player("s2")]
    market = [_market_player(f"m{i}", market_value=10_000_000 - i * 100_000) for i in range(15)]
    kb = FakeKickbase(history={"s1": _history(1_000_000, 1_050_000)})
    enricher = PlayerEnricher(kb, max_market_history=3)

    result = await enricher.enrich(LEAGUE_ID, _squad(*squad_players), market)

    # Squad + Top-3 Markt → 5 Historien-Calls, aber alle Spieler enthalten.
    assert set(result.keys()) == {"s1", "s2", "m0", "m1", "m2", *(f"m{i}" for i in range(3, 15))}
    assert kb.calls == ["s1", "s2", "m0", "m1", "m2"]


async def test_trend_pct_computed_from_history() -> None:
    # 8 Punkte → 7d-Trend deckt genau [-8] vs [-1] ab.
    series = _history(
        1_000_000,
        1_010_000,
        1_020_000,
        1_030_000,
        1_050_000,
        1_070_000,
        1_090_000,
        1_100_000,
    )
    kb = FakeKickbase(history={"s1": series})
    enricher = PlayerEnricher(kb)

    result = await enricher.enrich(LEAGUE_ID, _squad(_player("s1")), [])

    assert result["s1"].market_trend_7d_pct == pytest.approx(10.0)
    # 1d-Trend: 1_090_000 → 1_100_000 = ~0.92 %
    assert result["s1"].market_trend_1d_pct == pytest.approx(0.92, abs=0.01)
    # 3d-Trend: 1_050_000 → 1_100_000 = ~4.76 %
    assert result["s1"].market_trend_3d_pct == pytest.approx(4.76, abs=0.01)
    # 30d-Fenster nicht ausreichend gefüllt.
    assert result["s1"].market_trend_30d_pct is None
    assert result["s1"].mv_max_30d == 1_100_000
    assert "missing_data:market_trend_7d_pct" not in result["s1"].missing_data_flags


async def test_trend_none_when_history_transport_fails() -> None:
    kb = FakeKickbase(raise_for={"s1"})
    enricher = PlayerEnricher(kb)

    result = await enricher.enrich(LEAGUE_ID, _squad(_player("s1")), [])

    assert result["s1"].market_trend_7d_pct is None
    assert result["s1"].market_trend_1d_pct is None
    assert result["s1"].mv_max_30d is None
    assert "missing_data:market_trend_7d_pct" in result["s1"].missing_data_flags


async def test_short_history_leaves_longer_windows_none() -> None:
    # 2 Punkte reichen fuer 1d-Trend, nicht fuer 7d/30d.
    kb = FakeKickbase(history={"s1": _history(1_000_000, 1_020_000)})
    enricher = PlayerEnricher(kb)

    result = await enricher.enrich(LEAGUE_ID, _squad(_player("s1")), [])

    assert result["s1"].market_trend_1d_pct == pytest.approx(2.0)
    assert result["s1"].market_trend_3d_pct is None
    assert result["s1"].market_trend_7d_pct is None
    # 7d-Trend fehlt → Master-Prompt-Flag gesetzt.
    assert "missing_data:market_trend_7d_pct" in result["s1"].missing_data_flags


async def test_avg_points_fallback_flag_always_set() -> None:
    kb = FakeKickbase()
    enricher = PlayerEnricher(kb)

    result = await enricher.enrich(LEAGUE_ID, _squad(_player("s1", avg=110)), [])

    assert result["s1"].avg_points_last5 == pytest.approx(110.0)
    assert "missing_data:avg_points_last5_using_season_avg" in result["s1"].missing_data_flags


async def test_start_probability_reflects_status() -> None:
    kb = FakeKickbase()
    enricher = PlayerEnricher(kb)

    result = await enricher.enrich(
        LEAGUE_ID,
        _squad(
            _player("fit", status=PlayerStatus.FIT),
            _player("hurt", status=PlayerStatus.INJURED),
            _player("red", status=PlayerStatus.RED_CARD),
        ),
        [],
    )

    assert result["fit"].start_probability_next > 0.5
    assert result["hurt"].start_probability_next < 0.2
    assert result["red"].start_probability_next == 0.0
    assert result["fit"].injury_status == "fit"
    assert result["hurt"].injury_status == "injured"
    assert result["red"].injury_status == "suspended_red"


async def test_single_history_point_leaves_trend_none() -> None:
    kb = FakeKickbase(history={"s1": _history(1_000_000)})
    enricher = PlayerEnricher(kb)

    result = await enricher.enrich(LEAGUE_ID, _squad(_player("s1")), [])

    assert result["s1"].market_trend_7d_pct is None
