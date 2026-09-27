"""Mehrere Aktionen pro Tick im Deadline-Fenster (P2-16).

Am Freitagabend um 20:00 ist „verkaufen, aufstellen, nachkaufen" **eine**
Handlung: um 20:30 friert die Aufstellung ein und das Konto muss im Plus sein.
Mit einer Aktion pro Tick war das strukturell unmöglich — der nächste Tick kam
zu spät.

Die Kette ist deshalb eng begrenzt: nur im Fenster, höchstens drei Aktionen,
jede für sich gültig auf dem Zustand nach den vorherigen, Abbruch beim ersten
Fehler. Diese vier Grenzen prüfen die Tests hier.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from app.application.ai_decision_engine import (
    _INPUT_SCHEMA,
    MAX_ACTIONS_PER_TICK,
    AiDecisionEngine,
    _build_user_payload,
    _input_schema_for,
)
from app.application.decision_engine import DecisionContext
from app.domain.lineup import DEFAULT_FORMATION
from app.domain.models import (
    LeagueConstraints,
    LeagueMe,
    MarketPlayer,
    Player,
    PlayerStatus,
    Position,
    Squad,
    SquadPlayer,
)
from app.domain.trade import TradeAction

from tests.application.test_ai_decision_engine import FakeLlm

LEAGUE_ID = "L1"
MANAGER_ID = "M1"
NOW = datetime(2026, 10, 9, 17, 30, tzinfo=UTC)
# Anpfiff in 60 Minuten → `trading.phase == "deadline"`.
KICKOFF = NOW + timedelta(minutes=60)


def _player(pid: str, *, mv: int = 5_000_000, pos: Position = Position.MIDFIELDER) -> Player:
    return Player(
        id=pid,
        first_name="",
        last_name=f"Spieler {pid}",
        team_id="2",
        position=pos,
        status=PlayerStatus.FIT,
        market_value=Decimal(mv),
        average_points=100.0,
        total_points=400,
    )


def _context(
    *,
    squad_size: int = 3,
    squad_limit: int | None = 3,
    minutes_until_kickoff: int = 60,
) -> DecisionContext:
    squad = tuple(SquadPlayer(player=_player(f"s{i}"), lineup_order=i) for i in range(squad_size))
    market = tuple(
        MarketPlayer(
            player=_player(f"m{i}", mv=6_000_000),
            price=Decimal(6_000_000),
            expires_in_s=7200,
            seller_id="fremd",
        )
        for i in range(2)
    )
    return DecisionContext(
        league_id=LEAGUE_ID,
        league_me=LeagueMe(league_id=LEAGUE_ID, budget=Decimal(-2_000_000)),
        squad=Squad(league_id=LEAGUE_ID, manager_id=MANAGER_ID, players=squad),
        market=market,
        budget=Decimal(-2_000_000),
        max_trade_pct=0.5,
        min_cash_reserve=0,
        now=NOW,
        next_matchday_start=NOW + timedelta(minutes=minutes_until_kickoff),
        interval_min=120,
        max_negative_allowed=Decimal(-40_000_000),
        current_balance_after_open_bids=Decimal(-2_000_000),
        constraints=LeagueConstraints(squad_limit=squad_limit),
    )


def _main_action(action: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": action,
        "player_id": "s0",
        "price": 0,
        "intent": "DEBT_RELIEF",
        "confidence": 0.9,
        "reason_short": "kurz",
        "reason_long": "lang",
        "expected_outcome": {
            "points_delta_next_matchday": 0,
            "profit_estimate": 0,
            "balance_after_action": 0,
            "balance_after_open_bids": 0,
        },
        "risk_flags": [],
    }
    payload.update(extra)
    return payload


async def _decide(
    monkeypatch: pytest.MonkeyPatch, tool_input: dict[str, Any], context: DecisionContext
):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt", lambda: "SYS"
    )
    engine = AiDecisionEngine(llm=FakeLlm(response=tool_input), api_key="k")
    return await engine.decide(context)


async def test_sell_then_buy_is_one_chain_in_the_deadline_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der Fall, für den P2-16 gebaut wurde.

    Kader voll (3/3), Konto im Minus, Anpfiff in einer Stunde. Verkaufen
    schafft Geld **und** den Platz; der Nachkauf braucht beides. Mit einer
    Aktion pro Tick wäre der Kauf erst nach dem Anpfiff möglich.
    """
    decision = await _decide(
        monkeypatch,
        _main_action(
            "SELL_INSTANT",
            player_id="s0",
            follow_up_actions=[
                {
                    "action": "BUY",
                    "player_id": "m0",
                    "price": 6_000_000,
                    "intent": "POINTS",
                    "reason_short": "Platz aus Schritt 1 nutzen",
                }
            ],
        ),
        _context(),
    )

    assert decision.action is TradeAction.SELL
    assert [step.action for step in decision.chain] == [TradeAction.SELL, TradeAction.BUY]
    assert decision.follow_ups[0].player_id == "m0"
    assert decision.follow_ups[0].price == Decimal(6_000_000)


async def test_the_freed_slot_is_visible_to_the_next_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ohne Fortschreibung würde die Slot-Sperre genau diese Kette blocken.

    Der Kader ist mit 3/3 voll — ein `BUY` allein wäre unzulässig und würde
    zu `HOLD`. Nach dem Verkauf ist der Platz frei, und die Prüfung der
    Folgeaktion muss auf diesem Zustand rechnen.
    """
    without_sell = await _decide(
        monkeypatch,
        _main_action("BUY", player_id="m0", price=6_000_000, intent="POINTS"),
        _context(),
    )
    assert without_sell.action is TradeAction.HOLD
    assert "Kader ist voll" in without_sell.reason

    with_sell = await _decide(
        monkeypatch,
        _main_action(
            "SELL_INSTANT",
            player_id="s0",
            follow_up_actions=[
                {"action": "BUY", "player_id": "m0", "price": 6_000_000, "reason_short": "ok"}
            ],
        ),
        _context(),
    )
    assert len(with_sell.follow_ups) == 1


async def test_a_second_buy_without_a_slot_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein Verkauf schafft **einen** Platz, nicht zwei.

    Die Fortschreibung darf die Prüfung nicht aushebeln — sonst wäre die
    Kette ein Weg, die Slot-Sperre zu umgehen. Der ungedeckte zweite Kauf
    fällt weg, der Rest der Kette läuft: eine unbrauchbare Folgeaktion kostet
    seit dem Lauf vom 2026-09-26 nicht mehr die ganze Antwort.
    """
    decision = await _decide(
        monkeypatch,
        _main_action(
            "SELL_INSTANT",
            player_id="s0",
            follow_up_actions=[
                {"action": "BUY", "player_id": "m0", "price": 6_000_000, "reason_short": "eins"},
                {"action": "BUY", "player_id": "m1", "price": 6_000_000, "reason_short": "zwei"},
            ],
        ),
        _context(),
    )
    assert decision.action is TradeAction.SELL
    assert [step.action for step in decision.chain] == [TradeAction.SELL, TradeAction.BUY]
    assert decision.follow_ups[0].player_id == "m0", "der gedeckte Kauf bleibt"
    assert all(step.player_id != "m1" for step in decision.chain), "der ungedeckte fällt weg"


async def test_follow_ups_are_dropped_outside_the_deadline_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drei Stunden vor Anpfiff kommt ein nächster Tick — eine Aktion reicht.

    Verworfen, nicht abgelehnt: die Hauptaktion ist meist richtig, und sie
    mitzureissen wäre teurer als das Ignorieren des Extras.
    """
    decision = await _decide(
        monkeypatch,
        _main_action(
            "SELL_INSTANT",
            player_id="s0",
            follow_up_actions=[
                {"action": "BUY", "player_id": "m0", "price": 6_000_000, "reason_short": "x"}
            ],
        ),
        _context(minutes_until_kickoff=180),
    )
    assert decision.action is TradeAction.SELL
    assert decision.follow_ups == ()


async def test_the_chain_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Höchstens drei Aktionen — der Rest fällt weg statt zu laufen."""
    decision = await _decide(
        monkeypatch,
        _main_action(
            "SELL_INSTANT",
            player_id="s0",
            follow_up_actions=[
                {"action": "SELL_INSTANT", "player_id": "s1", "reason_short": "zwei"},
                {"action": "SELL_INSTANT", "player_id": "s2", "reason_short": "drei"},
                {"action": "SELL_LIST", "player_id": "s2", "price": 1, "reason_short": "vier"},
            ],
        ),
        _context(squad_size=4, squad_limit=4),
    )
    assert len(decision.chain) == MAX_ACTIONS_PER_TICK


async def test_hold_never_carries_follow_ups(monkeypatch: pytest.MonkeyPatch) -> None:
    """„Dieser Tick tut nichts" und „danach noch etwas" widersprechen sich."""
    decision = await _decide(
        monkeypatch,
        _main_action(
            "HOLD",
            player_id="",
            follow_up_actions=[
                {"action": "BUY", "player_id": "m0", "price": 6_000_000, "reason_short": "x"}
            ],
        ),
        _context(),
    )
    assert decision.action is TradeAction.HOLD
    assert decision.follow_ups == ()


async def test_set_lineup_works_as_a_follow_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der eigentliche Zweck: verkaufen **und** die Lücke in der Elf schliessen."""
    decision = await _decide(
        monkeypatch,
        _main_action(
            "SELL_INSTANT",
            player_id="s0",
            follow_up_actions=[
                {
                    "action": "SET_LINEUP",
                    "reason_short": "Elf ohne den Verkauften",
                    "lineup": {"formation": DEFAULT_FORMATION, "player_ids": ["s1", "s2"]},
                }
            ],
        ),
        _context(),
    )
    follow_up = decision.follow_ups[0]
    assert follow_up.action is TradeAction.SET_LINEUP
    assert follow_up.lineup is not None
    assert follow_up.lineup.player_ids == ("s1", "s2")


async def test_selling_the_same_player_twice_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nach dem ersten Verkauf steht er nicht mehr im Kader.

    Fällt ohne Sonderregel auf: die Fortschreibung entfernt ihn, und die
    bestehende Kaderprüfung schlägt an. Der erste Verkauf bleibt gültig —
    er war ja richtig.
    """
    decision = await _decide(
        monkeypatch,
        _main_action(
            "SELL_INSTANT",
            player_id="s0",
            follow_up_actions=[
                {"action": "SELL_INSTANT", "player_id": "s0", "reason_short": "nochmal"}
            ],
        ),
        _context(),
    )
    assert decision.action is TradeAction.SELL
    assert decision.follow_ups == ()
    assert len(decision.chain) == 1


def test_the_payload_states_how_many_actions_are_possible() -> None:
    """Die Zahl steht in den Daten, nicht nur als Bedingung im Prompt.

    Im ersten bezahlten Lauf (2026-09-26) beschrieb das Modell die Folgeaktion
    dreimal in Prosa („Dann SET_LINEUP …") und lieferte trotzdem eine einzelne.
    Dieselbe Lehre wie bei `trading.phase` und `season_phase`: ein Wert in den
    Daten wird gelesen, eine Bedingung im Fliesstext überlesen.
    """
    deadline = _build_user_payload(_context(minutes_until_kickoff=45))["trading"]
    assert deadline["phase"] == "deadline"
    assert deadline["max_actions_this_tick"] == MAX_ACTIONS_PER_TICK

    normal = _build_user_payload(_context(minutes_until_kickoff=600))["trading"]
    assert normal["phase"] != "deadline"
    assert normal["max_actions_this_tick"] == 1


def test_follow_up_actions_is_a_required_field() -> None:
    """Ein optionales Feld wird ignoriert, ein verlangtes beantwortet.

    Auch `[]` ist eine Antwort — und zwar eine, die belegt, dass das Modell
    über die Kette nachgedacht und sich dagegen entschieden hat.
    """
    assert "follow_up_actions" in _INPUT_SCHEMA["required"]
    assert "follow_up_actions" in _INPUT_SCHEMA["properties"]


async def test_an_empty_follow_up_list_is_a_normal_single_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Das Pflichtfeld darf den gewöhnlichen Tick nicht verändern."""
    decision = await _decide(
        monkeypatch,
        _main_action("SELL_INSTANT", player_id="s0", follow_up_actions=[]),
        _context(),
    )
    assert decision.action is TradeAction.SELL
    assert decision.follow_ups == ()
    assert len(decision.chain) == 1


# -- Die beiden Code-Sperren aus dem Schlusslauf (P2-16) -----------------


def test_the_chain_field_only_exists_in_the_deadline_window() -> None:
    """Ein Feld, das es nicht gibt, kann keine falsche Idee auslösen.

    Im Schlusslauf vom 2026-09-26 kippte `squad_is_full` von `HOLD` auf
    dreimal `BUY`: seit §2 „erst verkaufen, dann kaufen" erlaubt, versuchte
    das Modell diese Reihenfolge auch **ausserhalb** des Fensters, wo die
    Folgeaktionen verworfen werden — übrig blieb ein Kauf ohne Kaderplatz.
    """
    inside = _input_schema_for(_context(minutes_until_kickoff=45))
    assert "follow_up_actions" in inside["properties"]
    assert "follow_up_actions" in inside["required"]

    outside = _input_schema_for(_context(minutes_until_kickoff=600))
    assert "follow_up_actions" not in outside["properties"]
    assert "follow_up_actions" not in outside["required"]
    # Das gemeinsame Schema darf dabei nicht beschädigt werden.
    assert "follow_up_actions" in _INPUT_SCHEMA["properties"]


def _squad_with_prices(*prices: int) -> tuple[SquadPlayer, ...]:
    """Kader mit gegebenen Marktwerten; die ersten elf stehen in der Elf."""
    return tuple(
        SquadPlayer(
            player=Player(
                id=f"s{i}",
                first_name="",
                last_name=f"Spieler {i}",
                team_id="2",
                position=Position.MIDFIELDER,
                status=PlayerStatus.FIT,
                market_value=Decimal(price),
                average_points=100.0,
            ),
            lineup_order=i if i < 11 else None,
        )
        for i, price in enumerate(prices)
    )


def _debt_context(*, prices: tuple[int, ...], cash: int, minutes: int = 45) -> DecisionContext:
    return replace(
        _context(minutes_until_kickoff=minutes),
        squad=Squad(
            league_id=LEAGUE_ID, manager_id=MANAGER_ID, players=_squad_with_prices(*prices)
        ),
        budget=Decimal(cash),
        league_me=LeagueMe(league_id=LEAGUE_ID, budget=Decimal(cash)),
    )


async def test_an_insufficient_emergency_sale_is_redirected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der Befund aus acht Läufen: das Modell verkauft zu klein, um kein Loch zu reissen.

    Es vermeidet 100 Punkte Strafe und kauft sich den Totalausfall des
    Spieltags ein. Umgelenkt wird auf den **billigsten** Spieler, dessen
    Marktwert das Minus deckt.
    """
    decision = await _decide(
        monkeypatch,
        _main_action("SELL_INSTANT", player_id="s11"),  # der 1-Mio-Bankspieler
        _debt_context(prices=(*(12_000_000,) * 11, 1_000_000), cash=-10_000_000),
    )
    assert decision.action is TradeAction.SELL
    assert decision.player_id != "s11"
    assert "[Code-Korrektur]" in decision.reason


async def test_a_sufficient_sale_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deckt die Wahl des Modells das Minus, bleibt sie unangetastet."""
    decision = await _decide(
        monkeypatch,
        _main_action("SELL_INSTANT", player_id="s0"),
        _debt_context(prices=(*(12_000_000,) * 11, 1_000_000), cash=-10_000_000),
    )
    assert decision.player_id == "s0"
    assert "[Code-Korrektur]" not in decision.reason


async def test_a_chain_that_covers_the_deficit_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zwei Verkäufe, die zusammen reichen, sind die gewollte Lösung.

    Die Umlenkung darf sie nicht zerschlagen — sie rechnet über die ganze
    Kette, nicht über die erste Aktion.
    """
    decision = await _decide(
        monkeypatch,
        _main_action(
            "SELL_INSTANT",
            player_id="s11",
            follow_up_actions=[
                {"action": "SELL_INSTANT", "player_id": "s0", "reason_short": "zweiter"}
            ],
        ),
        _debt_context(prices=(*(12_000_000,) * 11, 1_000_000), cash=-10_000_000),
    )
    assert decision.player_id == "s11", "die Kette deckt das Minus, also kein Eingriff"
    assert [step.player_id for step in decision.chain] == ["s11", "s0"]


async def test_no_redirect_when_no_single_player_is_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reicht keiner allein, ist jede Wahl eine Teillösung — dann entscheidet das Modell."""
    decision = await _decide(
        monkeypatch,
        _main_action("SELL_INSTANT", player_id="s11"),
        _debt_context(prices=(*(2_000_000,) * 11, 1_000_000), cash=-30_000_000),
    )
    assert decision.player_id == "s11"
    assert "[Code-Korrektur]" not in decision.reason


async def test_no_redirect_outside_the_deadline_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Davor kommt ein nächster Tick — dann ist die Wahl Sache des Modells."""
    decision = await _decide(
        monkeypatch,
        _main_action("SELL_INSTANT", player_id="s11"),
        _debt_context(prices=(*(12_000_000,) * 11, 1_000_000), cash=-10_000_000, minutes=600),
    )
    assert decision.player_id == "s11"


async def test_the_redirect_prefers_the_bench_at_equal_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bei gleichem Marktwert der Bankspieler — kein Loch, wo keines nötig ist.

    Der Kader trägt hier einen 1-Mio-Spieler, den das Modell wählt, und zwei
    gleich teure Kandidaten mit 12 Mio: einer in der Elf, einer auf der Bank.
    Beide würden das Minus decken; genommen wird der, dessen Verkauf keinen
    Slot aufreisst.
    """
    decision = await _decide(
        monkeypatch,
        _main_action("SELL_INSTANT", player_id="s12"),
        # s0..s10 in der Elf (12 Mio), s11 Bank (12 Mio), s12 Bank (1 Mio).
        _debt_context(prices=(*(12_000_000,) * 11, 12_000_000, 1_000_000), cash=-10_000_000),
    )
    assert decision.player_id == "s11"
    assert "[Code-Korrektur]" in decision.reason
