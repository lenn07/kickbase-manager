"""Snapshot des USER-JSON, das die AI-Only-Engine an Claude schickt.

Das Review-Artefakt jedes Optimizing-Pakets ist der **Payload-Diff**, nicht der
Code-Diff: *Was sieht das LLM jetzt zusätzlich?* Dieser Test macht den Diff
sichtbar und verhindert, dass ein Umbau still Felder verliert.

Der Kontext wird aus den echten Cassettes gebaut (`squad`, `market`,
`league_me`, `market_value`) — nicht aus Handfutter, damit der Snapshot zeigt,
was der Bot in der echten Liga sieht.

**Snapshot neu schreiben:**
`UPDATE_SNAPSHOTS=1 pytest tests/application/test_user_payload_snapshot.py`
Danach den Diff lesen, *bevor* er committet wird.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from app.application.ai_decision_engine import _build_user_payload
from app.application.decision_engine import BuyRecord, DecisionContext, ListingRecord, RecentAction
from app.application.player_enrichment import PlayerEnricher
from app.application.run_tick_uc import _max_negative_allowed, _open_bids_total
from app.domain.models import MarketPlayer, MarketValuePoint, Squad
from app.domain.trade import TradeAction, TradeIntent
from app.infrastructure.kickbase.dto import (
    LeagueMeDTO,
    MarketResponseDTO,
    MarketValueResponseDTO,
    SquadResponseDTO,
)

from tests.infrastructure.kickbase.vcr_config import (
    FAKE_LEAGUE_ID,
    FAKE_USER_ID,
    load_cassette_payload,
)

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "user_payload.json"

# Feste Uhrzeit für den gesamten Kontext. Ohne sie wäre der Payload nicht
# reproduzierbar: `MarketPlayerDTO.to_market_player()` rechnet `exs` (Sekunden
# bis Ablauf) gegen `datetime.now(UTC)` in eine absolute Zeit um, und
# `_offer_entry()` greift ebenfalls auf die Wall-Clock zu. Beide Stellen werden
# hier deterministisch gemacht — siehe `_market_with_fixed_expiry` und
# `_normalise`.
NOW = datetime(2026, 9, 23, 16, 0, 0, tzinfo=UTC)
# `dt` aus dem Market-Root der Cassette = Start des nächsten Spieltags.
NEXT_MATCHDAY_START = datetime(2026, 10, 9, 18, 30, 0, tzinfo=UTC)


def _build_context() -> DecisionContext:
    squad_raw = load_cassette_payload("squad")
    market_raw = load_cassette_payload("market")
    league_me_raw = load_cassette_payload("league_me")
    history_raw = load_cassette_payload("market_value")

    squad = SquadResponseDTO.model_validate(squad_raw).to_domain(FAKE_LEAGUE_ID, FAKE_USER_ID)
    league_me = LeagueMeDTO.model_validate(league_me_raw).to_domain(FAKE_LEAGUE_ID)
    market = _market_with_fixed_expiry(market_raw)
    history = [p.to_domain() for p in MarketValueResponseDTO.model_validate(history_raw).it]

    enrichment = _enrich(squad, market, history)

    open_bids_total = _open_bids_total(market=list(market), manager_id=FAKE_USER_ID)
    return DecisionContext(
        league_id=FAKE_LEAGUE_ID,
        league_me=league_me,
        squad=squad,
        market=market,
        budget=league_me.budget,
        min_action_score=0.6,
        max_trade_pct=0.25,
        min_cash_reserve=1_000_000,
        blacklist=(),
        team_value=squad.team_value,
        open_bids_total=open_bids_total,
        now=NOW,
        next_matchday_start=NEXT_MATCHDAY_START,
        interval_min=120,
        buy_history=_buy_history(squad),
        own_listings=_own_listings(market),
        enrichment=enrichment,
        recent_actions=_recent_actions(),
        max_negative_allowed=_max_negative_allowed(
            team_value=squad.team_value, cash=league_me.budget
        ),
        current_balance_after_open_bids=league_me.budget - open_bids_total,
    )


def _market_with_fixed_expiry(market_raw: dict[str, Any]) -> tuple[MarketPlayer, ...]:
    """Ersetzt die Wall-Clock-Ablaufzeit durch `NOW + exs`.

    `to_market_player()` kennt nur die absolute Zeit, nicht mehr die Sekunden —
    deshalb wird `exs` hier aus dem Roh-Payload nachgereicht.
    """
    items = MarketResponseDTO.model_validate(market_raw).it
    raw_items = market_raw.get("it", [])
    out: list[MarketPlayer] = []
    for dto, raw in zip(items, raw_items, strict=True):
        expires_in_s = raw.get("exs")
        expires_at = (
            NOW + timedelta(seconds=int(expires_in_s)) if expires_in_s is not None else None
        )
        out.append(replace(dto.to_market_player(), expires_at=expires_at))
    return tuple(out)


class _StaticHistoryGateway:
    """Liefert jedem Spieler dieselbe echte Marktwert-Serie.

    Der Enricher soll im Snapshot seinen echten Pfad laufen (Trendfenster,
    `mv_max_30d`, `missing_data`-Flags) — nur die Datenquelle ist fixiert,
    damit das Ergebnis reproduzierbar bleibt.
    """

    def __init__(self, history: list[MarketValuePoint]) -> None:
        self._history = history

    async def get_market_value_history(
        self, league_id: str, player_id: str, days: int = 7
    ) -> list[MarketValuePoint]:
        del league_id, player_id, days
        return list(self._history)


def _enrich(
    squad: Squad, market: tuple[MarketPlayer, ...], history: list[MarketValuePoint]
) -> dict[str, Any]:
    enricher = PlayerEnricher(_StaticHistoryGateway(history))  # type: ignore[arg-type]
    return asyncio.run(enricher.enrich(FAKE_LEAGUE_ID, squad, market))


def _buy_history(squad: Squad) -> dict[str, BuyRecord]:
    """Ein historischer Kauf, damit `bought_at_price`/`bought_intent` im Snapshot stehen."""
    first = squad.players[0].player
    return {first.id: BuyRecord(intent=TradeIntent.PROFIT, buy_price=Decimal(12_000_000))}


def _own_listings(market: tuple[MarketPlayer, ...]) -> dict[str, ListingRecord]:
    """Das echte eigene Listing aus der Cassette (Verkäufer == eigener Manager)."""
    listings: dict[str, ListingRecord] = {}
    for mp in market:
        if mp.seller_id != FAKE_USER_ID:
            continue
        listings[mp.player.id] = ListingRecord(
            player_id=mp.player.id,
            listing_price=mp.price,
            listed_at=NOW - timedelta(hours=2, minutes=28),
            expires_at=mp.expires_at,
            has_offers=bool(mp.offers),
        )
    return listings


def _recent_actions() -> tuple[RecentAction, ...]:
    return (
        RecentAction(
            ts=NOW - timedelta(hours=4),
            action=TradeAction.LIST_ON_MARKET,
            player_id="1809",
            price=Decimal(9_200_000),
            intent=TradeIntent.PROFIT,
            executed=True,
        ),
        RecentAction(
            ts=NOW - timedelta(hours=2),
            action=TradeAction.HOLD,
            player_id=None,
            price=None,
            intent=None,
            executed=False,
        ),
    )


def _normalise(payload: dict[str, Any]) -> dict[str, Any]:
    """Nimmt die letzte Wall-Clock-Abhängigkeit aus dem Payload.

    `_offer_entry()` rechnet `expires_in_min` gegen `datetime.now(UTC)` statt
    gegen `context.now`. Solange `offers` leer ist (bis P0-2), greift das nicht;
    danach würde der Snapshot ohne diese Rundung im Minutentakt flackern.
    """
    normalised = json.loads(json.dumps(payload))
    for offer in normalised.get("incoming_offers", []):
        minutes = offer.get("expires_in_min")
        if isinstance(minutes, int):
            offer["expires_in_min"] = minutes - minutes % 15
    return normalised


@pytest.fixture(scope="module")
def payload() -> dict[str, Any]:
    return _normalise(_build_user_payload(_build_context()))


def test_payload_matches_snapshot(payload: dict[str, Any]) -> None:
    serialised = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(serialised)
        pytest.skip("Snapshot neu geschrieben — Diff prüfen und committen.")

    assert SNAPSHOT_PATH.exists(), (
        f"{SNAPSHOT_PATH} fehlt. Einmalig erzeugen mit: UPDATE_SNAPSHOTS=1 pytest {__file__}"
    )
    expected = json.loads(SNAPSHOT_PATH.read_text())
    assert payload == expected, (
        "Der USER-JSON hat sich geändert. Diff lesen: Sieht das LLM jetzt mehr oder weniger? "
        f"Wenn gewollt: UPDATE_SNAPSHOTS=1 pytest {SNAPSHOT_PATH.parent.parent.name}"
    )


def test_payload_is_deterministic() -> None:
    """Wächter gegen neue Wall-Clock-Zugriffe im Payload-Builder.

    Zwei Aufrufe mit identischem Kontext müssen byte-gleich sein. Schlägt dieser
    Test fehl, hat jemand `datetime.now()` eingebaut, statt `context.now` zu
    benutzen — der Snapshot wäre danach wertlos.
    """
    first = _normalise(_build_user_payload(_build_context()))
    second = _normalise(_build_user_payload(_build_context()))
    assert first == second


def test_payload_covers_all_prompt_blocks(payload: dict[str, Any]) -> None:
    """Der Master-Prompt referenziert diese Blöcke — fehlt einer, rät das LLM."""
    for key in (
        "now_iso",
        "next_matchday_start_iso",
        "ticks_until_matchday_start",
        "rules_last_verified",
        "budget",
        "squad",
        "market",
        "incoming_offers",
        "recent_actions",
        "constraints",
    ):
        assert key in payload, f"Block `{key}` fehlt im USER-JSON"
    assert payload["squad"], "Kader ist leer — Cassette kaputt?"
    assert payload["market"], "Markt ist leer — Cassette kaputt?"


def test_own_listing_is_visible(payload: dict[str, Any]) -> None:
    """Ohne diesen Block kann das LLM ein laufendes Listing doppelt anfassen."""
    listed = [p for p in payload["squad"] if p.get("listing")]
    assert listed, "Die Cassette enthält ein eigenes Listing — es muss im Payload auftauchen"
    assert listed[0]["listing"]["price"] > 0


# -- Gap-Assertions: das Definition-of-Done von Phase 1 -------------------
#
# Jede Lücke bekommt einen **eigenen** Test. Gebündelt in einer Funktion würde
# `xfail(strict=True)` weiter „grün" melden, solange irgendeine der Assertions
# fällt — der Merge von P0-1 bliebe unbemerkt und das Phase-1-DoD
# („die 4 Gap-Assertions sind grün") wäre nicht messbar. Einzeln schlägt
# `strict` beim richtigen Merge mit XPASS an und erzwingt, den Marker zu
# entfernen.


@pytest.mark.xfail(strict=True, reason="P0-1: team_value wird nie befüllt (Defekt D1)")
def test_gap_team_value_is_filled(payload: dict[str, Any]) -> None:
    assert payload["budget"]["team_value"] > 0


@pytest.mark.xfail(strict=True, reason="P0-1: max_negative_allowed bleibt 0 (Defekt D1)")
def test_gap_max_negative_allowed_is_negative(payload: dict[str, Any]) -> None:
    assert payload["budget"]["max_negative_allowed"] < 0


def test_market_players_always_have_a_start_probability(payload: dict[str, Any]) -> None:
    """Invariante, kein Gap: ein fehlender Wert wäre schlimmer als ein grober."""
    assert all(p["start_probability_next"] is not None for p in payload["market"])


@pytest.mark.xfail(
    strict=True, reason="P0-3: Startelf-Signal ist eine Status-Pauschale (Defekt D5)"
)
def test_gap_market_start_probability_is_more_than_a_status_guess(payload: dict[str, Any]) -> None:
    """D5 misst sich nicht an `is not None` — dieser Wert ist nie None.

    Der Defekt ist, dass Ersatzkeeper und Stammspieler **denselben** Wert
    bekommen, sobald beide `fit` sind: `_START_PROBABILITY_BY_STATUS` kennt nur
    den Verletzungsstatus. Messbar wird das erst über die Streuung innerhalb
    einer Statusgruppe — und darüber, dass das Heuristik-Flag verschwindet,
    wenn eine echte Quelle (`prob` bzw. `sl`) gegriffen hat.
    """
    fit = [p for p in payload["market"] if p["injury_status"] == "fit"]
    assert len(fit) > 1, "Cassette ohne fitte Marktspieler — Test aussagelos"

    distinct = {p["start_probability_next"] for p in fit}
    assert len(distinct) > 1, (
        f"Alle {len(fit)} fitten Marktspieler haben denselben Wert {distinct} — "
        "das ist der Verletzungsstatus, keine Startelf-Prognose."
    )
    assert not any(
        "missing_data:start_probability_next_heuristic" in p.get("missing_data_flags", [])
        for p in fit
    )


@pytest.mark.xfail(strict=True, reason="P0-3: Marktspieler ohne Leistungsdaten (Defekt D4)")
def test_gap_market_players_have_recent_form(payload: dict[str, Any]) -> None:
    assert all(p["avg_points_last5"] is not None for p in payload["market"])
