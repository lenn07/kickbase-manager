"""Die Startelf-Quellen-Kette aus P0-3 (Defekt D5), Stufe für Stufe.

Warum ein eigener Test statt einer Assertion im Payload-Snapshot: der Snapshot
läuft gegen die Cassette vom 23.09., und die enthält **kein** `prob` — Kickbase
liefert das Feld außerhalb der Spieltagswoche nicht (Plan §8/F2, 0 von 21
Items am 23.09. gegen 22 von 22 am 31.08.). Die `prob`-Stufe wäre dort also
nicht messbar, egal wie gut sie umgesetzt ist.

Quelle für die `prob`-Fälle ist deshalb die Archiv-Stichprobe aus P0-0.7
(`docs/samples/market_prob_sample_2026-08-31.json`) — dieselbe, die schon F2
und F3 belegt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from app.application.player_enrichment import PlayerEnricher
from app.domain.exceptions import TransportError
from app.domain.models import (
    MarketPlayer,
    MarketValuePoint,
    Player,
    PlayerDetail,
    PlayerStatus,
    Position,
    Squad,
    SquadPlayer,
)
from app.infrastructure.kickbase.dto import MarketResponseDTO

SAMPLE_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "samples" / "market_prob_sample_2026-08-31.json"
)
LEAGUE_ID = "1111111"


class _CountingGateway:
    """Zählt Detail-Calls — jeder ist ein zusätzlicher Request pro Tick."""

    def __init__(self, *, predicted_starter: bool | None = True) -> None:
        self.detail_calls: list[str] = []
        self._predicted_starter = predicted_starter

    async def get_market_value_history(
        self, league_id: str, player_id: str, days: int = 7
    ) -> list[MarketValuePoint]:
        del league_id, player_id, days
        return []

    async def get_player_detail(self, league_id: str, player_id: str) -> PlayerDetail:
        del league_id
        self.detail_calls.append(player_id)
        return PlayerDetail(
            player_id=player_id,
            is_predicted_starter=self._predicted_starter,
            prediction_source="Ligainsider",
        )


def _sample_market() -> tuple[MarketPlayer, ...]:
    payload: dict[str, Any] = json.loads(SAMPLE_PATH.read_text())
    return MarketResponseDTO.model_validate(payload).to_domain().players


def _empty_squad() -> Squad:
    return Squad(league_id=LEAGUE_ID, manager_id="9999999", players=())


def _player(pid: str, *, status: PlayerStatus = PlayerStatus.FIT) -> Player:
    return Player(
        id=pid,
        first_name="",
        last_name=pid,
        team_id="2",
        position=Position.MIDFIELDER,
        status=status,
        market_value=10_000_000,  # type: ignore[arg-type]
    )


def _market_player(player: Player, *, prob: int | None = None) -> MarketPlayer:
    return MarketPlayer(
        player=player,
        price=player.market_value,
        expires_in_s=3600,
        seller_id=None,
        start_probability_raw=prob,
    )


# -- Stufe 1: `prob` ------------------------------------------------------


async def test_prob_scale_spreads_the_start_probability() -> None:
    """Das eigentliche Maß für D5: Ersatzkeeper und Kapitän dürfen nicht gleich aussehen.

    Vor P0-3 bekam jeder fitte Spieler 0.85 aus `_START_PROBABILITY_BY_STATUS`
    — ein Wert, der nur die Verfügbarkeit kennt, nicht die Rotation. Mit `prob`
    entstehen echte Abstufungen.
    """
    market = _sample_market()
    assert market, "Archiv-Stichprobe leer"
    gateway = _CountingGateway()

    enrichment = await PlayerEnricher(gateway).enrich(  # type: ignore[arg-type]
        LEAGUE_ID, _empty_squad(), market
    )

    fit = [
        enrichment[mp.player.id]
        for mp in market
        if mp.player.status is PlayerStatus.FIT and mp.start_probability_raw is not None
    ]
    assert len(fit) > 1

    distinct = {e.start_probability_next for e in fit}
    assert len(distinct) > 1, (
        f"Alle {len(fit)} fitten Spieler haben denselben Wert {distinct} — "
        "das wäre wieder der Verletzungsstatus statt einer Startelf-Prognose."
    )
    assert all(e.start_probability_source == "kickbase_prob" for e in fit)
    assert not any(
        "missing_data:start_probability_next_heuristic" in e.missing_data_flags for e in fit
    )


async def test_prob_one_is_the_safest_starter() -> None:
    """Die Richtung ist das Hauptrisiko des Pakets (Plan §9, Risiko 1).

    Invertiert gelesen hielte der Bot Ersatzspieler für Stammkräfte. Belegt ist
    `1` = sicherste Startelf über den Median-Marktwert je Stufe (§8/F2).
    """
    market = (
        _market_player(_player("sicher"), prob=1),
        _market_player(_player("bank"), prob=5),
    )
    enrichment = await PlayerEnricher(_CountingGateway()).enrich(  # type: ignore[arg-type]
        LEAGUE_ID, _empty_squad(), market
    )

    assert enrichment["sicher"].start_probability_next is not None
    assert enrichment["bank"].start_probability_next is not None
    assert enrichment["sicher"].start_probability_next > enrichment["bank"].start_probability_next


async def test_prob_costs_no_extra_requests() -> None:
    """In der Spieltagswoche ist die Kette gratis.

    `prob` steht im Market-Payload, den der Tick ohnehin holt. Nur wenn es
    fehlt, kostet Stufe 2 einen Request je Spieler — §9 des Plans führt die
    Call-Zahl als Ban-Risiko.
    """
    gateway = _CountingGateway()
    await PlayerEnricher(gateway).enrich(  # type: ignore[arg-type]
        LEAGUE_ID, _empty_squad(), _sample_market()
    )
    assert gateway.detail_calls == []


async def test_unknown_prob_level_falls_through_instead_of_guessing() -> None:
    """Eine 6. Stufe wäre neu — dann fällt die Kette weiter, statt zu raten."""
    market = (_market_player(_player("neu"), prob=9),)
    gateway = _CountingGateway(predicted_starter=True)

    enrichment = await PlayerEnricher(gateway).enrich(  # type: ignore[arg-type]
        LEAGUE_ID, _empty_squad(), market
    )

    assert enrichment["neu"].start_probability_source == "lineup_prediction"


# -- Stufe 2: `sl` --------------------------------------------------------


async def test_lineup_prediction_fills_in_outside_the_matchday_week() -> None:
    """Ohne `sl` wäre D5 an 11 von 14 Tagen unbehoben (Plan §8/F2)."""
    market = (_market_player(_player("m1")),)
    gateway = _CountingGateway(predicted_starter=True)

    enrichment = await PlayerEnricher(gateway).enrich(  # type: ignore[arg-type]
        LEAGUE_ID, _empty_squad(), market
    )

    assert gateway.detail_calls == ["m1"]
    assert enrichment["m1"].start_probability_source == "lineup_prediction"
    assert enrichment["m1"].start_probability_next == pytest.approx(0.80)


async def test_lineup_prediction_is_capped_to_protect_the_rate_limit() -> None:
    """Der Deckel ist der Grund, warum die Kette überhaupt tragbar ist.

    Ohne ihn holte der Enricher ein Detail für *jeden* Marktspieler — knapp 30
    zusätzliche Requests pro Tick, obendrauf auf die Historien-Calls.
    """
    market = tuple(_market_player(_player(f"m{i}")) for i in range(20))
    gateway = _CountingGateway()

    await PlayerEnricher(gateway, max_market_history=3).enrich(  # type: ignore[arg-type]
        LEAGUE_ID, _empty_squad(), market
    )

    assert len(gateway.detail_calls) == 3, (
        "Details dürfen nur für die ohnehin beobachtete Auswahl geholt werden"
    )


async def test_squad_players_always_get_a_prediction() -> None:
    """Der eigene Kader ist immer in der Auswahl — dort zählt jeder Slot 100 Punkte."""
    squad = Squad(
        league_id=LEAGUE_ID,
        manager_id="9999999",
        players=(SquadPlayer(player=_player("s1"), lineup_order=0),),
    )
    gateway = _CountingGateway(predicted_starter=False)

    enrichment = await PlayerEnricher(gateway).enrich(gateway and LEAGUE_ID, squad, ())  # type: ignore[arg-type]

    assert gateway.detail_calls == ["s1"]
    assert enrichment["s1"].start_probability_source == "lineup_prediction"
    assert enrichment["s1"].start_probability_next == pytest.approx(0.20)


# -- Stufen 3 und 4: Heuristik und Aufgabe --------------------------------


async def test_injury_status_is_the_last_numeric_fallback() -> None:
    market = tuple(_market_player(_player(f"m{i}")) for i in range(5))
    gateway = _CountingGateway(predicted_starter=None)

    enrichment = await PlayerEnricher(gateway, max_market_history=2).enrich(  # type: ignore[arg-type]
        LEAGUE_ID, _empty_squad(), market
    )

    outside = enrichment["m4"]  # ausserhalb der Shortlist, also gar kein Detail-Call
    assert outside.start_probability_source == "injury_status_heuristic"
    assert "missing_data:start_probability_next_heuristic" in outside.missing_data_flags


async def test_unknown_status_yields_no_probability_at_all() -> None:
    """Defekt D6: ein unbekanntes `st` darf keine Zahl rechtfertigen.

    Vorher landete es auf `FIT` und damit auf 0.85 — ein gesperrter Spieler sah
    spielbereit aus. Jetzt: `null` plus zwei Flags, die genau sagen, was fehlt.
    """
    market = (_market_player(_player("raetsel", status=PlayerStatus.UNKNOWN)),)
    gateway = _CountingGateway(predicted_starter=None)

    enrichment = await PlayerEnricher(gateway).enrich(  # type: ignore[arg-type]
        LEAGUE_ID, _empty_squad(), market
    )

    entry = enrichment["raetsel"]
    assert entry.start_probability_next is None
    assert entry.start_probability_source == "none"
    assert entry.injury_status == "unknown"
    assert "missing_data:start_probability_next" in entry.missing_data_flags
    assert "missing_data:injury_status" in entry.missing_data_flags


async def test_detail_call_failure_does_not_break_the_tick() -> None:
    """Ein fehlgeschlagener Zusatz-Call darf den Tick nicht kosten."""

    class _FailingGateway(_CountingGateway):
        async def get_player_detail(self, league_id: str, player_id: str) -> PlayerDetail:
            self.detail_calls.append(player_id)
            raise TransportError("detail down")

    market = (_market_player(_player("m1")),)
    enrichment = await PlayerEnricher(_FailingGateway()).enrich(  # type: ignore[arg-type]
        LEAGUE_ID, _empty_squad(), market
    )

    assert enrichment["m1"].start_probability_source == "injury_status_heuristic"
