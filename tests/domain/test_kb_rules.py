"""Unit-Tests für die Kickbase-Regel-Modifikatoren."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.kb_rules import (
    action_threshold_scale,
    apply_delta,
    evaluate_accept_offer,
    evaluate_buy,
    evaluate_sell,
    negative_budget_limit,
)
from app.domain.models import Position


def test_negative_budget_limit_matches_official_example() -> None:
    # Kickbase-Hilfe: TV 100 M, Kto -10 M → (100 - 10) * 33 % = 29.7 M erlaubtes Minus.
    limit = negative_budget_limit(team_value=Decimal("100000000"), budget=Decimal("-10000000"))
    assert limit == Decimal("29700000.00")


def test_negative_budget_limit_uses_full_team_value_when_positive_balance() -> None:
    limit = negative_budget_limit(team_value=Decimal("100000000"), budget=Decimal("5000000"))
    assert limit == Decimal("33000000.00")


def test_buy_within_limit_gets_no_penalty() -> None:
    adj = evaluate_buy(
        budget=Decimal("5000000"),
        team_value=Decimal("50000000"),
        open_bids_total=Decimal(0),
        buy_price=Decimal("2000000"),
        squad_size=13,
    )
    assert adj.delta == 0.0
    assert adj.reasons == ()


def test_buy_breaking_33_percent_gets_penalty() -> None:
    # TV 30 M, Kto 0 → Limit 9.9 M. Kauf 20 M → neuer Stand -20 M, Overshoot 10.1 M.
    adj = evaluate_buy(
        budget=Decimal(0),
        team_value=Decimal("30000000"),
        open_bids_total=Decimal(0),
        buy_price=Decimal("20000000"),
        squad_size=13,
    )
    assert adj.delta < 0
    assert any("33" in r for r in adj.reasons)


def test_buy_open_bids_count_toward_limit() -> None:
    # TV 30 M, Kto 5 M, offene Gebote 5 M, Kauf 20 M → projected = -20; Limit = 9.9 M.
    adj_with_bids = evaluate_buy(
        budget=Decimal("5000000"),
        team_value=Decimal("30000000"),
        open_bids_total=Decimal("5000000"),
        buy_price=Decimal("20000000"),
        squad_size=13,
    )
    adj_without_bids = evaluate_buy(
        budget=Decimal("5000000"),
        team_value=Decimal("30000000"),
        open_bids_total=Decimal(0),
        buy_price=Decimal("20000000"),
        squad_size=13,
    )
    # Mit offenen Geboten muss der Malus mindestens so groß sein wie ohne.
    assert adj_with_bids.delta <= adj_without_bids.delta


def test_buy_squad_cap_blocks_further_buy() -> None:
    adj = evaluate_buy(
        budget=Decimal("50000000"),
        team_value=Decimal("100000000"),
        open_bids_total=Decimal(0),
        buy_price=Decimal("1000000"),
        squad_size=15,
    )
    assert adj.delta == -1.0
    assert any("Squad-Cap" in r for r in adj.reasons)


def test_buy_deadline_window_adds_small_penalty() -> None:
    now = datetime(2026, 8, 28, 19, 0, tzinfo=UTC)
    deadline = now + timedelta(minutes=90)
    adj = evaluate_buy(
        budget=Decimal("10000000"),
        team_value=Decimal("50000000"),
        open_bids_total=Decimal(0),
        buy_price=Decimal("2000000"),
        squad_size=13,
        now=now,
        next_matchday_start=deadline,
    )
    assert adj.delta < 0
    assert any("Deadline" in r for r in adj.reasons)


def test_sell_debt_relief_to_positive_gets_big_bonus() -> None:
    adj = evaluate_sell(
        budget=Decimal("-5000000"),
        sell_price=Decimal("6000000"),
        squad_size=13,
    )
    assert adj.delta > 0
    assert any("Debt-Relief" in r and "Plus" in r for r in adj.reasons)


def test_sell_partial_debt_relief_gets_smaller_bonus() -> None:
    adj_partial = evaluate_sell(
        budget=Decimal("-10000000"),
        sell_price=Decimal("2000000"),
        squad_size=13,
    )
    adj_full = evaluate_sell(
        budget=Decimal("-10000000"),
        sell_price=Decimal("11000000"),
        squad_size=13,
    )
    assert 0 < adj_partial.delta < adj_full.delta


def test_sell_below_starting_eleven_gets_hard_penalty() -> None:
    adj = evaluate_sell(budget=Decimal("5000000"), sell_price=Decimal("1000000"), squad_size=11)
    assert adj.delta == -1.0
    assert any("Startelf" in r for r in adj.reasons)


def test_sell_leaving_exactly_eleven_reserves_penalty() -> None:
    adj = evaluate_sell(budget=Decimal("5000000"), sell_price=Decimal("1000000"), squad_size=12)
    assert adj.delta < 0
    assert adj.delta > -1.0


def test_sell_deadline_bonus_only_when_negative_budget() -> None:
    now = datetime(2026, 8, 28, 19, 0, tzinfo=UTC)
    deadline = now + timedelta(minutes=45)
    adj_negative = evaluate_sell(
        budget=Decimal("-5000000"),
        sell_price=Decimal("6000000"),
        squad_size=13,
        now=now,
        next_matchday_start=deadline,
    )
    adj_positive = evaluate_sell(
        budget=Decimal("5000000"),
        sell_price=Decimal("1000000"),
        squad_size=13,
        now=now,
        next_matchday_start=deadline,
    )
    assert any("Deadline" in r for r in adj_negative.reasons)
    assert not any("Deadline" in r for r in adj_positive.reasons)


def test_accept_offer_reuses_sell_logic() -> None:
    adj_offer = evaluate_accept_offer(
        budget=Decimal("-5000000"),
        offer_price=Decimal("6000000"),
        squad_size=13,
    )
    adj_sell = evaluate_sell(
        budget=Decimal("-5000000"),
        sell_price=Decimal("6000000"),
        squad_size=13,
    )
    assert adj_offer.delta == adj_sell.delta


def test_threshold_scale_neutral_without_deadline_context() -> None:
    assert action_threshold_scale(now=None, next_matchday_start=None, interval_min=120) == 1.0


def test_threshold_scale_neutral_when_many_ticks_remaining() -> None:
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    # 3 Tage * 24 h = 72 h → bei 120-min-Intervall = 36 Ticks (weit über calm_tick_count=10).
    deadline = now + timedelta(days=3)
    assert action_threshold_scale(now=now, next_matchday_start=deadline, interval_min=120) == 1.0


def test_threshold_scale_hits_urgency_floor_at_last_tick() -> None:
    now = datetime(2026, 8, 28, 20, 29, tzinfo=UTC)
    # Nur noch 1 Minute bis Anpfiff → deutlich unter 1 Tick, Faktor nahe urgency_floor (0.5).
    deadline = now + timedelta(minutes=1)
    scale = action_threshold_scale(now=now, next_matchday_start=deadline, interval_min=120)
    assert 0.5 <= scale < 0.55


def test_threshold_scale_interpolates_linearly() -> None:
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    # 5 Ticks bis Deadline (bei 120-min-Intervall = 10 h) → Faktor 0.5 + 0.5 * (5/10) = 0.75.
    deadline = now + timedelta(hours=10)
    scale = action_threshold_scale(now=now, next_matchday_start=deadline, interval_min=120)
    assert abs(scale - 0.75) < 1e-9


def test_threshold_scale_stays_neutral_after_kickoff() -> None:
    now = datetime(2026, 8, 28, 21, 0, tzinfo=UTC)
    deadline = now - timedelta(hours=1)  # Spieltag läuft schon
    assert action_threshold_scale(now=now, next_matchday_start=deadline, interval_min=120) == 1.0


def test_apply_delta_clips_to_unit_interval() -> None:
    assert apply_delta(0.9, 0.3) == 1.0
    assert apply_delta(0.2, -0.5) == 0.0
    assert apply_delta(0.5, 0.1) == 0.6


# -- Positions-Regeln ------------------------------------------------------


_HEALTHY_SQUAD_POSITIONS = {
    Position.GOALKEEPER: 2,
    Position.DEFENDER: 5,
    Position.MIDFIELDER: 5,
    Position.FORWARD: 3,
}


def test_sell_last_goalkeeper_is_blocked_hard() -> None:
    # Nur 1 GK im Kader — Verkauf würde eine unbesetzte GK-Position hinterlassen.
    positions = {**_HEALTHY_SQUAD_POSITIONS, Position.GOALKEEPER: 1}
    adj = evaluate_sell(
        budget=Decimal("5000000"),
        sell_price=Decimal("2000000"),
        squad_size=14,
        squad_positions=positions,
        sold_position=Position.GOALKEEPER,
    )
    assert adj.delta == -1.0
    assert any("Positions-Loch" in r and "GK" in r for r in adj.reasons)


def test_sell_surplus_position_gets_small_bonus() -> None:
    # 5 DEF — Verkauf lässt 4 DEF, weit über Mindest 3.
    adj = evaluate_sell(
        budget=Decimal("5000000"),
        sell_price=Decimal("2000000"),
        squad_size=14,
        squad_positions=_HEALTHY_SQUAD_POSITIONS,
        sold_position=Position.DEFENDER,
    )
    assert adj.delta > 0
    assert any("Positions-Überschuss" in r for r in adj.reasons)


def test_buy_second_goalkeeper_gets_overstock_malus() -> None:
    # Schon 1 GK vorhanden — der zweite ist nur als PROFIT-Karte sinnvoll.
    positions = {**_HEALTHY_SQUAD_POSITIONS, Position.GOALKEEPER: 1}
    adj = evaluate_buy(
        budget=Decimal("10000000"),
        team_value=Decimal("50000000"),
        open_bids_total=Decimal(0),
        buy_price=Decimal("2000000"),
        squad_size=14,
        squad_positions=positions,
        bought_position=Position.GOALKEEPER,
    )
    assert adj.delta < 0
    assert any("GK-Overstock" in r for r in adj.reasons)


def test_buy_filling_missing_position_gets_bedarf_bonus() -> None:
    # Kader ohne einzigen GK — Kauf füllt Startelf-Pflicht.
    positions = {**_HEALTHY_SQUAD_POSITIONS, Position.GOALKEEPER: 0}
    adj = evaluate_buy(
        budget=Decimal("10000000"),
        team_value=Decimal("50000000"),
        open_bids_total=Decimal(0),
        buy_price=Decimal("2000000"),
        squad_size=14,
        squad_positions=positions,
        bought_position=Position.GOALKEEPER,
    )
    assert adj.delta > 0
    assert any("Positions-Bedarf" in r and "GK" in r for r in adj.reasons)


def test_position_check_stays_neutral_without_context() -> None:
    # Ältere Aufrufer (Tests, Legacy-Code) ohne Positions-Kontext dürfen keinen
    # Effekt sehen — die Position-Regel greift nur mit vollständigen Params.
    adj_buy = evaluate_buy(
        budget=Decimal("5000000"),
        team_value=Decimal("50000000"),
        open_bids_total=Decimal(0),
        buy_price=Decimal("2000000"),
        squad_size=13,
    )
    adj_sell = evaluate_sell(
        budget=Decimal("5000000"),
        sell_price=Decimal("2000000"),
        squad_size=13,
    )
    assert not any("Positions" in r or "GK-Overstock" in r for r in adj_buy.reasons)
    assert not any("Positions" in r for r in adj_sell.reasons)
