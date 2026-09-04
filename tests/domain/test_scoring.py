"""Unit-Tests für das pure Scoring-Modul (Phase 4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.domain.models import MarketValuePoint, Player, PlayerStatus, Position
from app.domain.scoring import (
    ScoreWeights,
    compose_score,
    compute_features,
    form_score,
    injury_multiplier,
    market_trend_score,
    price_efficiency_score,
)


def _player(
    *,
    avg: float = 6.0,
    status: PlayerStatus = PlayerStatus.FIT,
    mv: Decimal = Decimal("2000000"),
) -> Player:
    return Player(
        id="p1",
        first_name="F",
        last_name="L",
        team_id="t1",
        position=Position.MIDFIELDER,
        status=status,
        market_value=mv,
        average_points=avg,
        total_points=0,
    )


def _history(values: list[int]) -> list[MarketValuePoint]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        MarketValuePoint(day=start + timedelta(days=i), value=Decimal(v))
        for i, v in enumerate(values)
    ]


class TestFormScore:
    def test_zero_average_maps_to_zero(self) -> None:
        assert form_score(_player(avg=0.0)) == 0.0

    def test_half_saturation_point_hits_half(self) -> None:
        # k = 80 → bei 80 Ø-Punkten liegt der Score bei 0.5.
        assert form_score(_player(avg=80.0)) == pytest.approx(0.5)

    def test_high_average_differentiates_no_clip(self) -> None:
        # Kurve sättigt weich — 120 muss deutlich über 12 liegen.
        low = form_score(_player(avg=12.0))
        high = form_score(_player(avg=120.0))
        assert high > low + 0.4
        assert high < 1.0

    def test_score_monotonic_in_average(self) -> None:
        values = [form_score(_player(avg=x)) for x in (5, 30, 80, 150, 250)]
        assert values == sorted(values)
        assert len(set(values)) == len(values)

    def test_negative_average_treated_as_zero(self) -> None:
        assert form_score(_player(avg=-5.0)) == 0.0


class TestPriceEfficiency:
    def test_tiny_price_returns_zero(self) -> None:
        # unter 100 k € nicht auswertbar.
        assert price_efficiency_score(average_points=8.0, price=Decimal("50000")) == 0.0

    def test_four_points_per_million_is_one(self) -> None:
        assert price_efficiency_score(
            average_points=8.0, price=Decimal("2000000")
        ) == pytest.approx(1.0)

    def test_low_efficiency_is_low_score(self) -> None:
        # 1 Punkt für 5 Mio € → 0.2 / 4 = 0.05
        s = price_efficiency_score(average_points=1.0, price=Decimal("5000000"))
        assert 0.0 < s < 0.1


class TestMarketTrendScore:
    def test_no_history_returns_neutral(self) -> None:
        assert market_trend_score([]) == 0.5

    def test_single_point_returns_neutral(self) -> None:
        assert market_trend_score(_history([1_000_000])) == 0.5

    def test_positive_trend_above_neutral(self) -> None:
        s = market_trend_score(_history([1_000_000, 1_050_000]))
        assert s > 0.5

    def test_negative_trend_below_neutral(self) -> None:
        s = market_trend_score(_history([1_000_000, 950_000]))
        assert s < 0.5

    def test_ten_percent_gain_saturates_high(self) -> None:
        s = market_trend_score(_history([1_000_000, 1_100_000]))
        assert s == pytest.approx(1.0)

    def test_ten_percent_loss_saturates_low(self) -> None:
        s = market_trend_score(_history([1_000_000, 900_000]))
        assert s == pytest.approx(0.0)


class TestInjuryMultiplier:
    def test_fit_is_full(self) -> None:
        assert injury_multiplier(PlayerStatus.FIT) == 1.0

    def test_injured_is_heavy_penalty(self) -> None:
        assert injury_multiplier(PlayerStatus.INJURED) < 0.5

    def test_not_in_team_is_near_zero(self) -> None:
        assert injury_multiplier(PlayerStatus.NOT_IN_TEAM) <= 0.1


class TestExternalSignal:
    def test_defaults_to_neutral(self) -> None:
        features = compute_features(_player(avg=10.0), price=Decimal("2000000"), history=[])
        assert features.external_signal == pytest.approx(0.5)

    def test_explicit_signal_is_passed_through(self) -> None:
        features = compute_features(
            _player(avg=10.0),
            price=Decimal("2000000"),
            history=[],
            external_signal=0.9,
        )
        assert features.external_signal == pytest.approx(0.9)

    def test_out_of_range_signal_is_clipped(self) -> None:
        features = compute_features(
            _player(avg=10.0),
            price=Decimal("2000000"),
            history=[],
            external_signal=1.7,
        )
        assert features.external_signal == pytest.approx(1.0)

    def test_high_external_signal_increases_composed_score(self) -> None:
        player = _player(avg=40.0, mv=Decimal("2000000"))
        low = compose_score(
            compute_features(player, Decimal("2000000"), [], external_signal=0.0),
            ScoreWeights(),
        )
        high = compose_score(
            compute_features(player, Decimal("2000000"), [], external_signal=1.0),
            ScoreWeights(),
        )
        assert high > low


class TestComposeScore:
    def test_all_zero_features_gives_zero(self) -> None:
        features = compute_features(
            _player(avg=0.0, mv=Decimal("50000")),
            price=Decimal("50000"),
            history=_history([1_000_000, 500_000]),
            external_signal=0.0,
        )
        assert compose_score(features, ScoreWeights()) == 0.0

    def test_top_features_yield_near_one(self) -> None:
        player = _player(avg=400.0, status=PlayerStatus.FIT, mv=Decimal("2000000"))
        features = compute_features(
            player,
            price=Decimal("2000000"),
            history=_history([1_000_000, 1_100_000]),
            external_signal=1.0,
        )
        assert compose_score(features, ScoreWeights()) > 0.9

    def test_injury_multiplier_reduces_score(self) -> None:
        player_fit = _player(avg=10.0, status=PlayerStatus.FIT, mv=Decimal("2000000"))
        player_injured = _player(avg=10.0, status=PlayerStatus.INJURED, mv=Decimal("2000000"))
        w = ScoreWeights()
        s_fit = compose_score(compute_features(player_fit, Decimal("2000000"), []), w)
        s_injured = compose_score(compute_features(player_injured, Decimal("2000000"), []), w)
        assert s_injured < s_fit
        assert s_injured == pytest.approx(s_fit * 0.3, rel=1e-6)

    def test_weights_all_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            ScoreWeights(form=0.0, price_efficiency=0.0, market_trend=0.0, external_signal=0.0)
