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
from app.application.run_tick_uc import _max_negative_allowed
from app.application.team_context import TeamContextProvider
from app.domain.fixtures import TeamOutlook
from app.domain.models import (
    Fixture,
    LeagueConstraints,
    MarketPlayer,
    MarketValuePoint,
    PlayerDetail,
    PlayerPerformance,
    Squad,
    TeamStanding,
)
from app.domain.trade import TradeAction, TradeIntent
from app.infrastructure.kickbase.dto import (
    CompetitionTableDTO,
    LeagueMeDTO,
    LineupOverviewDTO,
    MarketResponseDTO,
    MarketValueResponseDTO,
    MatchdaysResponseDTO,
    PlayerDetailDTO,
    PlayerPerformanceResponseDTO,
    RankingResponseDTO,
    SquadResponseDTO,
)

from tests.infrastructure.kickbase.vcr_config import (
    FAKE_LEAGUE_ID,
    FAKE_USER_ID,
    load_cassette_payload,
)

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "user_payload.json"

# Feste Uhrzeit für den gesamten Kontext. Seit P0-1 rechnet die DTO-Schicht
# `exs` nicht mehr gegen die Wall-Clock (`MarketPlayer.expires_in_s` ist roh,
# `expires_at(now)` braucht eine Uhr) — der Payload ist damit allein über
# `context.now` reproduzierbar. Einzige verbliebene Ausnahme: `_offer_entry()`,
# siehe `_normalise`.
NOW = datetime(2026, 9, 23, 16, 0, 0, tzinfo=UTC)


def _build_context() -> DecisionContext:
    squad_raw = load_cassette_payload("squad")
    market_raw = load_cassette_payload("market")
    league_me_raw = load_cassette_payload("league_me")
    history_raw = load_cassette_payload("market_value")

    squad = SquadResponseDTO.model_validate(squad_raw).to_domain(FAKE_LEAGUE_ID, FAKE_USER_ID)
    league_me = LeagueMeDTO.model_validate(league_me_raw).to_domain(FAKE_LEAGUE_ID)
    # Seit P0-1 trägt der Snapshot die Root-Felder mit: `tv` (Mannschaftswert),
    # `mvud` (nächstes MW-Update) und `dt` (Spieltagsstart) kommen damit aus
    # derselben Quelle wie die Listings, statt im Test hart gesetzt zu werden.
    snapshot = MarketResponseDTO.model_validate(market_raw).to_domain()
    market = snapshot.players
    # Die echte Aufstellung aus der Cassette: Formation 3-5-2, **8 von 11**
    # Slots besetzt. Genau der Zustand, der am nächsten Spieltag 300 Punkte
    # kostet — er gehört in den Snapshot, nicht in eine Handannahme.
    lineup = LineupOverviewDTO.model_validate(load_cassette_payload("lineup_overview")).to_domain()
    history = [p.to_domain() for p in MarketValueResponseDTO.model_validate(history_raw).it]

    enrichment = _enrich(squad, market, history)

    # Keine offenen Gebote in dieser Lage: der `trade_log` ist im Snapshot
    # leer, also gibt es nichts zu rekonstruieren (siehe `_open_bids`).
    open_bids_total = Decimal(0)
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
        team_value=snapshot.team_value,
        open_bids_total=open_bids_total,
        now=NOW,
        next_matchday_start=snapshot.next_matchday_start,
        mv_update_at=snapshot.mv_update_at,
        interval_min=120,
        buy_history=_buy_history(squad),
        own_listings=_own_listings(market),
        enrichment=enrichment,
        recent_actions=_recent_actions(),
        max_negative_allowed=_max_negative_allowed(
            team_value=snapshot.team_value, cash=league_me.budget
        ),
        current_balance_after_open_bids=league_me.budget - open_bids_total,
        lineup=lineup,
        lineup_deadline=snapshot.next_matchday_start,
        # Kaderlimit und Vereinsverteilung aus der `/me`-Cassette; die drei
        # übrigen Werte liefert die API nicht und sie stehen deshalb in der
        # Konfiguration. Hier die echte Liga (§8/F6, am 2026-09-24 in den
        # Admin-Einstellungen abgelesen): kein Vereinslimit, Unterbieten
        # deaktiviert, Wertung nach Saisonpunkten.
        constraints=LeagueConstraints(
            squad_limit=league_me.squad_limit,
            players_per_club=league_me.players_per_club,
            club_limit_is_unlimited=True,
            underpay_blocked=True,
            scoring_mode="season_points",
        ),
        # Tabelle und Spielplan aus den echten Cassettes (P2-11): am 23.09. sind
        # vier Spieltage gespielt, der fünfte ist für den 09.10. angesetzt. Der
        # Snapshot zeigt damit die Lage, in der der Bot entscheidet — Bayern auf
        # Platz 2 mit +12 Toren, Gladbach Letzter mit -10.
        team_outlook=_team_outlook(),
        # Die echte Ligatabelle aus der Cassette (P2-12): vier Manager, eigener
        # Platz 3 von 4, 780 Punkte hinter dem Führenden, Spieltag 4 von 34.
        league_ranking=RankingResponseDTO.model_validate(
            load_cassette_payload("ranking")
        ).to_domain(FAKE_LEAGUE_ID),
    )


class _StaticGateway:
    """Liefert jedem Spieler dieselben echten Serien: Marktwert, Detail, Spieltage.

    Der Enricher soll im Snapshot seinen echten Pfad laufen (Trendfenster,
    `mv_max_30d`, Startelf-Kette, Form-Fenster, `missing_data`-Flags) — nur die
    Datenquellen sind fixiert, damit das Ergebnis reproduzierbar bleibt.

    Dass dadurch alle Spieler dieselbe Startelf-Prognose und dieselbe
    Spieltags-Form bekommen, ist ein Artefakt des Fakes und kein Befund. Die
    Streuung prüft `test_prob_scale_spreads_the_start_probability` gegen die
    Archiv-Stichprobe; die Form-Mathematik steht in `test_form_window.py`.
    Einzig die Marktwert-Trends der Kaderspieler streuen hier echt — die
    kommen seit P1-7 aus dem Squad-Payload, nicht aus der Fake-Serie.
    """

    def __init__(
        self,
        history: list[MarketValuePoint],
        detail: PlayerDetail,
        performance: PlayerPerformance,
    ) -> None:
        self._history = history
        self._detail = detail
        self._performance = performance

    async def get_market_value_history(
        self, league_id: str, player_id: str, days: int = 7
    ) -> list[MarketValuePoint]:
        del league_id, player_id, days
        return list(self._history)

    async def get_player_detail(self, league_id: str, player_id: str) -> PlayerDetail:
        del league_id
        return replace(self._detail, player_id=player_id)

    async def get_player_performance(self, league_id: str, player_id: str) -> PlayerPerformance:
        del league_id
        return replace(self._performance, player_id=player_id)


class _FixtureGateway:
    """Liefert Tabelle und Spielplan aus den Cassettes — sonst nichts.

    Der Provider soll im Snapshot seinen echten Pfad laufen (FDR-Bänder,
    Tordifferenz-Korrektur, Auswahl des nächsten Spiels), nur ohne HTTP.
    """

    def __init__(self, standings: list[TeamStanding], fixtures: list[Fixture]) -> None:
        self._standings = standings
        self._fixtures = fixtures

    async def get_competition_table(self, competition_id: str = "1") -> list[TeamStanding]:
        del competition_id
        return list(self._standings)

    async def list_fixtures(self, competition_id: str = "1") -> list[Fixture]:
        del competition_id
        return list(self._fixtures)


def _team_outlook() -> dict[str, TeamOutlook]:
    standings = CompetitionTableDTO.model_validate(
        load_cassette_payload("competition_table")
    ).to_domain()
    fixtures = MatchdaysResponseDTO.model_validate(load_cassette_payload("matchdays")).to_fixtures()
    provider = TeamContextProvider(_FixtureGateway(standings, fixtures))  # type: ignore[arg-type]
    return asyncio.run(provider.load(now=NOW))


def _enrich(
    squad: Squad, market: tuple[MarketPlayer, ...], history: list[MarketValuePoint]
) -> dict[str, Any]:
    detail = PlayerDetailDTO.model_validate(load_cassette_payload("player_detail")).to_domain("x")
    performance = PlayerPerformanceResponseDTO.model_validate(
        load_cassette_payload("player_performance")
    ).to_domain("x")
    gateway = _StaticGateway(history, detail, performance)
    enricher = PlayerEnricher(gateway)  # type: ignore[arg-type]
    return asyncio.run(
        enricher.enrich(
            FAKE_LEAGUE_ID,
            squad,
            market,
            mv_update_at=None,
            next_matchday_start=None,
            now=NOW,
        )
    )


def _buy_history(squad: Squad) -> dict[str, BuyRecord]:
    """Ein historischer Kauf, damit `bought_at_price`/`bought_intent` im Snapshot stehen.

    Mit Kaufdatum, sonst bliebe `days_held` im Snapshot `null` und die
    Slot-Ökonomie aus §3a wäre nicht geprüft: eine Trade-Position, deren Alter
    man nicht kennt, sieht ewig frisch aus.
    """
    first = squad.players[0].player
    return {
        first.id: BuyRecord(
            intent=TradeIntent.PROFIT,
            buy_price=Decimal(12_000_000),
            bought_at=NOW - timedelta(days=4, hours=3),
        )
    }


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
            expires_at=mp.expires_at(NOW),
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
        "Der USER-JSON hat sich geändert. Diff lesen: Sieht das LLM jetzt mehr oder weniger?\n"
        "Häufigster Grund: die Cassettes wurden neu aufgenommen, der Markt hat sich also "
        "bewegt — dann ist der Diff Rauschen und der Snapshot wird nachgezogen.\n"
        "Wenn gewollt: UPDATE_SNAPSHOTS=1 pytest tests/application/test_user_payload_snapshot.py"
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
    # Seit P0-2: wie viele Manager bereits geboten haben. `has_offers` allein
    # sagt nur ob, nicht wie stark — der Unterschied entscheidet zwischen
    # „Listing halten" und „Preis nachziehen".
    assert "offer_count" in listed[0]["listing"]


def test_market_entries_never_claim_to_show_foreign_bids(payload: dict[str, Any]) -> None:
    """`ofc` heißt auf fremden Listings **eigene** Gebote — nicht Konkurrenz (P2-13).

    Bis P2-13 stand der Zähler als `offer_count` im Payload, und der Prompt las
    ihn als Zahl der Mitbieter. Kickbase zeigt fremde Gebote aber nirgends an
    (help.kickbase.com, „Warum habe ich den Spieler nicht bekommen?"): der Wert
    ist die Zahl der *eigenen* Gebote. Das Modell überbot damit systematisch
    sich selbst und hielt jeden echten Mitbieter für nicht vorhanden.

    Der Name im Payload ist deshalb Teil der Korrektur — ein Feld `offer_count`
    neben `my_open_bid_price` lädt zur alten Fehldeutung ein, egal was im Prompt
    steht.
    """
    assert all("offer_count" not in p for p in payload["market"]), (
        "`offer_count` ist auf der Kaufseite ein irreführender Name — "
        "er zählt die eigenen Gebote, nicht die fremden."
    )
    assert all(isinstance(p["my_open_bid_count"], int) for p in payload["market"])
    assert all(p["my_open_bid_count"] >= 0 for p in payload["market"])


def test_own_listings_keep_the_incoming_offer_count(payload: dict[str, Any]) -> None:
    """Auf **eigenen** Listings ist `ofc` echt — dort zählt Kickbase Fremd-Gebote.

    Die Gegenrichtung zum Test oben: die Umbenennung auf der Kaufseite darf das
    Signal auf der Verkaufsseite nicht mitnehmen. `listing.offer_count` steuert,
    ob ein Listing gehalten oder in den Sofortverkauf gegeben wird.
    """
    listings = [p["listing"] for p in payload["squad"] if p.get("listing")]
    assert listings, "Cassette ohne eigenes Listing — der Test prüfte nichts."
    assert all(isinstance(entry["offer_count"], int) for entry in listings)
    assert all("has_offers" in entry for entry in listings)


def test_market_entries_carry_the_drift_horizon(payload: dict[str, Any]) -> None:
    """Der Drift-Anteil des Overbids braucht die Zahl der Updates bis zum Zuschlag.

    Maßgeblich ist der Marktwert **zum Transferzeitpunkt**; ein Gebot zum
    heutigen Wert platzt, wenn das 22-Uhr-Update darüber hinweggeht. Ohne
    `mv_updates_until_expiry` müsste das Modell das aus zwei ISO-Strings
    herleiten — und genau diese Rechnung ging in der Praxis schief.
    """
    for entry in payload["market"]:
        assert "expires_in_min" in entry
        assert "mv_updates_until_expiry" in entry
        updates = entry["mv_updates_until_expiry"]
        assert updates is None or updates >= 0


def test_trading_block_makes_the_slot_economy_visible(payload: dict[str, Any]) -> None:
    """Trading wird über Kaderplätze gespielt, nicht über Geld (§3a).

    Ohne diesen Block musste das Modell Slot-Auslastung, Buchgewinne und die
    Zahl laufender Trade-Positionen aus dem `squad`-Array zusammenrechnen. In
    der Praxis tat es das nicht und handelte fast nur auf Punkte-Motive — der
    Ertrag zwischen zwei Spieltagen blieb liegen.
    """
    trading = payload["trading"]
    assert trading["phase"] in {"trading", "matchday_prep", "deadline", "unknown"}
    assert trading["squad_slots_used"] == len(payload["squad"])
    assert isinstance(trading["unrealized_pnl_total"], int)
    assert isinstance(trading["profit_positions"], int)
    # Wie viel dieser Tick ausgeben könnte, bevor die 33 %-Grenze reißt.
    budget = payload["budget"]
    expected = budget["current_balance_after_open_bids"] - budget["max_negative_allowed"]
    assert trading["spendable_before_debt_limit"] == expected
    # Die Zahl der Marktwert-Updates bis zum Anpfiff ist die Trading-Uhr.
    updates = trading["mv_updates_until_matchday"]
    assert updates is None or updates >= 0


def test_squad_entries_expose_the_holding_period(payload: dict[str, Any]) -> None:
    """`days_held` trennt eine frische Position von einer, die nur Platz belegt."""
    held = [p for p in payload["squad"] if p.get("bought_intent")]
    assert held, "Kein Kaderspieler mit Kaufhistorie — der Test prüfte nichts."
    for entry in held:
        assert entry["days_held"] is None or entry["days_held"] >= 0
        assert "bought_at_iso" in entry


# -- Gap-Assertions: das Definition-of-Done von Phase 1 -------------------
#
# Jede Lücke bekommt einen **eigenen** Test. Gebündelt in einer Funktion würde
# `xfail(strict=True)` weiter „grün" melden, solange irgendeine der Assertions
# fällt — der Merge von P0-1 bliebe unbemerkt und das Phase-1-DoD
# („die 4 Gap-Assertions sind grün") wäre nicht messbar. Einzeln schlägt
# `strict` beim richtigen Merge mit XPASS an und erzwingt, den Marker zu
# entfernen.


def test_gap_team_value_is_filled(payload: dict[str, Any]) -> None:
    """Geschlossen mit P0-1 (Defekt D1). Bleibt als Regressionswächter stehen.

    `tv` steht im Market-Root; der Wert darf nie wieder aus einem Default-0-Feld
    kommen. Ein LLM, das `team_value: 0` liest, hält sich für handlungsunfähig —
    empirisch belegt im Tick vom 2026-09-23 (Plan §6/P0-1).
    """
    assert payload["budget"]["team_value"] > 0


def test_gap_max_negative_allowed_is_negative(payload: dict[str, Any]) -> None:
    """Geschlossen mit P0-1 (Defekt D1). Die 33 %-Regel muss Spielraum ausweisen."""
    assert payload["budget"]["max_negative_allowed"] < 0
    # Basis der 33 %-Regel ist `team_value + min(0, cash)` — ein bestehendes
    # Kontominus verkleinert sie. Gegenrechnen, damit ein Vorzeichen-, Faktor-
    # oder Basisfehler auffällt statt still durchzulaufen.
    budget = payload["budget"]
    basis = budget["team_value"] + min(0, budget["cash"])
    assert abs(budget["max_negative_allowed"] - -round(basis * 0.33)) <= 1


def test_both_clocks_are_in_the_payload(payload: dict[str, Any]) -> None:
    """Der Bot braucht zwei Uhren, nicht eine.

    Die Spieltags-Deadline entscheidet über Regel-Compliance, das tägliche
    Marktwert-Update (22:00 Berlin) über jede Trading-Entscheidung. Vor P0-1
    kannte der Prompt nur die erste.
    """
    assert payload["next_matchday_start_iso"]
    assert payload["mv_update_at_iso"]
    assert payload["minutes_until_mv_update"] is not None


def test_every_start_probability_names_its_source(payload: dict[str, Any]) -> None:
    """Invariante seit P0-3: entweder ein Wert **mit** Herkunft, oder ein Flag.

    Der ursprüngliche Test forderte hier „nie None" — begründet damit, ein
    fehlender Wert sei schlimmer als ein grober. Das steht im Widerspruch zu
    §8/F2 und §9 des Plans („niemals ein erfundener Default"), und der Plan hat
    recht: eine 0.5 für einen Spieler mit unbekanntem Status ist von einer
    echten Prognose nicht zu unterscheiden, ein `null` mit Flag schon. Die
    Invariante ist deshalb nicht „hat einen Wert", sondern „ist ehrlich über
    das, was sie hat".
    """
    for entry in payload["market"] + payload["squad"]:
        source = entry["start_probability_source"]
        assert source, f"{entry['player_id']}: Startelf-Prognose ohne Herkunftsangabe"
        if entry["start_probability_next"] is None:
            assert source == "none"
            assert "missing_data:start_probability_next" in entry["missing_data_flags"]
        else:
            assert source != "none"


def test_start_probability_flags_only_the_players_it_guessed(payload: dict[str, Any]) -> None:
    """Das Heuristik-Flag muss unterscheiden, sonst trägt es keine Information.

    Vor P0-3 hing es an **jedem** Spieler — das Modell konnte daran nicht
    erkennen, wann es der Prognose trauen darf. Jetzt steht es genau dort, wo
    wirklich nur der Verletzungsstatus die Grundlage war.
    """
    flag = "missing_data:start_probability_next_heuristic"
    for entry in payload["market"] + payload["squad"]:
        guessed = entry["start_probability_source"] == "injury_status_heuristic"
        assert (flag in entry["missing_data_flags"]) is guessed, (
            f"{entry['player_id']}: Flag und Quelle widersprechen sich "
            f"({entry['start_probability_source']!r})"
        )
    assert any(e["start_probability_source"] == "lineup_prediction" for e in payload["market"]), (
        "Ohne `prob` muss die `sl`-Stufe greifen — sonst ist D5 für 11 von 14 "
        "Tagen unbehoben (Plan §8/F2)."
    )


def test_gap_market_players_have_recent_form(payload: dict[str, Any]) -> None:
    """Geschlossen mit P0-3 (Defekt D4). Formulierung gegenüber P0-0.4 korrigiert.

    Die ursprüngliche Fassung lautete `all(avg_points_last5 is not None)` und
    war **nicht erfüllbar**: 4 der 21 Marktspieler tragen weder `ap` noch `p`
    im Payload (Spieler ohne Einsatz), und für die ist `null` die richtige
    Antwort — ein erfundener Wert wäre genau der Fehler aus §9. Der Defekt war
    nie „fehlt bei manchen", sondern „`to_market_player()` setzt für **alle**
    hart 0". Gemessen wird deshalb: was Kickbase liefert, kommt an; was fehlt,
    trägt ein Flag.
    """
    raw_by_id = {item["i"]: item for item in load_cassette_payload("market")["it"]}
    with_data = [p for p in payload["market"] if raw_by_id[p["player_id"]].get("ap") is not None]
    without = [p for p in payload["market"] if raw_by_id[p["player_id"]].get("ap") is None]

    assert len(with_data) > len(without), "Stichprobe zu dünn — Test wäre aussagelos"
    assert all(p["avg_points_last5"] is not None for p in with_data), (
        "Kickbase liefert `ap`, der Payload zeigt es nicht — genau das war D4."
    )
    assert all("missing_data:avg_points_last5" in p["missing_data_flags"] for p in without), (
        "Fehlende Leistungsdaten müssen als fehlend markiert sein, nicht als 0 getarnt."
    )
    # Die Werte müssen streuen: vorher war jeder Marktspieler auf 0 gesetzt und
    # damit von jedem anderen ununterscheidbar.
    assert len({p["avg_points_last5"] for p in with_data}) > 1


def test_every_squad_player_shows_its_entry_price(payload: dict[str, Any]) -> None:
    """Das DoD von P1-6 (Defekt D7): kein Kaderspieler ohne `bought_at_price`.

    Der Kontext dieses Snapshots trägt **einen** historischen BUY im
    `trade_log` — vor P1-6 hatte damit genau ein Spieler von acht einen
    Einstand. Die anderen sieben wären für jede PROFIT-Rechnung unsichtbar
    gewesen, obwohl Kickbase den Wert über `mvgl` die ganze Zeit mitgeliefert
    hat.
    """
    squad = payload["squad"]
    assert all(p["bought_at_price"] is not None for p in squad), (
        f"Kaderspieler ohne Einstand: {[p['name'] for p in squad if p['bought_at_price'] is None]}"
    )
    assert all(p["unrealized_pnl"] is not None for p in squad)
    # Die Identität muss im Payload halten, nicht nur in der Domain-Schicht.
    for entry in squad:
        assert entry["market_value"] - entry["bought_at_price"] == entry["unrealized_pnl"]
    # Nur ein Spieler trägt einen Intent — der kommt weiter aus dem `trade_log`,
    # denn *warum* gekauft wurde, weiß Kickbase nicht.
    assert sum(1 for p in squad if p.get("bought_intent")) == 1


def test_squad_trends_come_from_the_payload_not_the_shared_fake(payload: dict[str, Any]) -> None:
    """P1-7: die 24-h-/7-d-Trends der Kaderspieler streuen.

    Der `_StaticGateway` gibt **jedem** Spieler dieselbe Marktwert-Serie.
    Solange die Trends allein daraus kamen, trug jeder Kaderspieler denselben
    Wert — im Snapshot 0,02 % für alle acht. Seit `tfhmvt`/`sdmvt` aus dem
    Squad-Payload kommen, sind es echte, verschiedene Zahlen; Marius Wolf
    steht mit -13,2 % auf sieben Tagen da, wo vorher +0,02 % stand.

    Dass die Werte auseinandergehen, ist deshalb die eigentliche Messung:
    ohne sie wäre das Paket an dieser Stelle wirkungslos, und der Test würde
    es nicht merken.
    """
    squad = payload["squad"]
    assert all(p["market_trend_1d_pct"] is not None for p in squad)
    assert all(p["market_trend_7d_pct"] is not None for p in squad)
    assert len({p["market_trend_7d_pct"] for p in squad}) > 1, (
        "Alle Kaderspieler tragen denselben 7-d-Trend — die Payload-Quelle greift nicht."
    )
    # Die Fenster ohne Payload-Quelle kommen weiter aus der Historie und
    # dürfen im Snapshot ruhig gleich sein — der Fake liefert ja eine Serie.
    assert len({p["market_trend_30d_pct"] for p in squad}) == 1


def test_form_reports_minutes_and_its_own_window(payload: dict[str, Any]) -> None:
    """P1-8 (Defekt D9): `avg_points_last5` ist echte Form, mit Minuten daneben.

    Drei Zusagen in einem Test, weil sie nur zusammen etwas wert sind:
    der Wert stammt aus Spieltagsdaten (nicht mehr aus dem Saison-Ø), die
    Einsatzminuten stehen daneben (§2.4: „Minuten sind die Basis von allem"),
    und die Fenstergröße ist ausgewiesen — am 4. Spieltag beruht jede Form auf
    vier Spielen, und das muss das Modell wissen dürfen.
    """
    squad = payload["squad"]
    assert all(p["form_matchdays_counted"] > 0 for p in squad)
    assert all(p["minutes_last5"] is not None for p in squad)
    assert all(p["starts_last5"] is not None for p in squad)
    for entry in squad:
        # Solange das Fenster kürzer als fünf Spieltage ist, muss genau das
        # Flag stehen — und nicht mehr das alte „ist in Wahrheit der Saison-Ø".
        assert "missing_data:avg_points_last5_using_season_avg" not in entry["missing_data_flags"]
        if entry["form_matchdays_counted"] < 5:
            assert "missing_data:avg_points_last5_partial_window" in entry["missing_data_flags"]

    # Marktspieler außerhalb der Shortlist bekommen keine Spieltagsdaten —
    # ein Request pro Spieler wäre das Ban-Risiko aus §9.
    with_form = [p for p in payload["market"] if p["form_matchdays_counted"] > 0]
    assert 0 < len(with_form) < len(payload["market"])


def test_constraints_carry_the_league_limits(payload: dict[str, Any]) -> None:
    """P1-9 (Defekt D10) + §8/F6: alle fünf Liga-Regeln stehen im Payload.

    Das Kaderlimit ist 16 und kommt aus `mppu`; der Code hatte 15 stehen. Die
    drei übrigen liefert **keine** Kickbase-Response — `/leagues/{l}/settings`
    existiert nicht — sie kommen aus der Konfiguration, nachdem sie in den
    Admin-Einstellungen abgelesen wurden.
    """
    limits = payload["constraints"]
    assert limits["squad_limit"] == 16
    assert limits["players_per_club"], "tpc[] ist leer — Cassette kaputt?"
    assert limits["squad_slots_left"] == 16 - len(payload["squad"])

    assert limits["underpay_blocked"] is True
    assert limits["scoring_mode"] == "season_points"
    assert limits["missing_data_flags"] == [], (
        "Alle Liga-Regeln sind bekannt — es darf kein Flag übrig bleiben."
    )

    # Die Nutzer-Guardrails dürfen dabei nicht verloren gehen.
    for field_name in ("min_cash_reserve", "max_trade_pct", "blacklist", "interval_min"):
        assert field_name in limits


def test_unlimited_is_an_answer_not_a_gap(payload: dict[str, Any]) -> None:
    """„Kein Vereinslimit" und „Limit unbekannt" dürfen nicht gleich aussehen.

    Beide erscheinen als `club_limit: null`. Nur im zweiten Fall muss sich der
    Bot zurückhalten; im ersten darf er frei nachlegen. Unterscheidbar ist das
    allein über `club_limit_is_unlimited` und das Flag — fällt eines von
    beiden weg, liest das Modell eine bewusste Liga-Einstellung als Datenlücke.
    """
    limits = payload["constraints"]
    assert limits["club_limit"] is None
    assert limits["club_limit_is_unlimited"] is True
    assert "missing_data:constraints.club_limit" not in limits["missing_data_flags"]


def test_negative_season_average_is_data_not_a_gap(payload: dict[str, Any]) -> None:
    """Ein Minuswert ist ein Datum — und ein besonders aussagekräftiges.

    `_avg_points_proxy` filterte auf `> 0` und warf damit die beiden Spieler
    mit negativem Saison-Ø (Platzverweis, Eigentor) in denselben Topf wie die
    ohne jede Angabe. Wer -60 nicht sieht, kauft ihn.
    """
    negatives = [
        p
        for p in payload["market"]
        if p["avg_points_last5"] is not None and p["avg_points_last5"] < 0
    ]
    assert negatives, "Cassette ohne negativen Saison-Ø — Test aussagelos"
    assert all("missing_data:avg_points_last5" not in p["missing_data_flags"] for p in negatives)


def test_every_player_carries_its_next_fixture(payload: dict[str, Any]) -> None:
    """P2-11: Gegner, Heimrecht und Schwierigkeit stehen an **jedem** Spieler.

    Vor P2-11 verbot §1.2 des Prompts ausdrücklich, mit Restspielplan und
    Gegnerstärke zu rechnen — die Daten fehlten. Gemessen wird deshalb dreierlei:
    die Felder sind da, sie sind gefüllt (die Cassette hat für jedes Team ein
    kommendes Spiel), und die Schwierigkeit **streut**. Ein konstanter FDR wäre
    schlimmer als keiner: er sähe nach Information aus und wäre keine.
    """
    entries = payload["squad"] + payload["market"]
    for entry in entries:
        for field_name in ("next_opponent", "next_opponent_rank", "is_home", "fdr", "fdr_next3"):
            assert field_name in entry, f"{entry['player_id']}: `{field_name}` fehlt"

    known = [e for e in entries if e["fdr"] is not None]
    assert len(known) == len(entries), (
        "Die Cassette hat für jeden Verein ein kommendes Spiel — "
        f"ohne FDR: {[e['name'] for e in entries if e['fdr'] is None]}"
    )
    assert all(1 <= e["fdr"] <= 5 for e in known)
    assert len({e["fdr"] for e in known}) > 1, (
        "Alle Spieler haben dieselbe Gegnerstärke — die FDR-Ableitung greift nicht."
    )
    # Heim und Auswärts müssen beide vorkommen, sonst prüft `is_home` nichts.
    assert {e["is_home"] for e in entries} == {True, False}
    # Kein Flag, wo der Spielplan bekannt ist.
    assert all("missing_data:fixtures" not in e["missing_data_flags"] for e in known)


def test_fixture_difficulty_points_the_right_way(payload: dict[str, Any]) -> None:
    """Die Skala darf nicht verdreht sein: 1 = leicht, 5 = schwer.

    Dieselbe Falle wie bei `prob` (Plan §8/F2), nur mit umgekehrtem Vorzeichen
    der Folgen: eine invertierte Skala lässt das Modell systematisch die Spieler
    mit den schwersten Spielen kaufen. Der Test hängt sie an die Tabelle der
    Cassette — Spitzenteams müssen höhere Werte erzeugen als Kellerkinder.
    """
    by_opponent = {
        entry["next_opponent"]: entry["fdr"]
        for entry in payload["squad"] + payload["market"]
        if entry["fdr"] is not None
    }
    # Dortmund (Platz 1) und Bayern (Platz 2, +12 Tore) sind die schwersten
    # Gegner der Cassette, Gladbach (Platz 18) und Union (17, -13) die leichtesten.
    hard = [by_opponent[name] for name in ("Dortmund", "Bayern") if name in by_opponent]
    easy = [by_opponent[name] for name in ("M'gladbach", "Union Berlin") if name in by_opponent]
    assert hard and easy, f"Erwartete Gegner fehlen im Payload: {sorted(by_opponent)}"
    assert min(hard) > max(easy), f"FDR-Skala verdreht: schwere Gegner {hard}, leichte {easy}"


def test_league_block_shows_where_the_manager_stands(payload: dict[str, Any]) -> None:
    """P2-12: Rang, Rückstand und Saison-Uhr stehen im Payload.

    Der Teamwert ist in keinem Modus ein Siegkriterium — die Saisonpunkte sind
    es. Ohne diesen Block hat das Modell jede Lage gleich behandelt: maximaler
    Erwartungswert, egal ob er zum Aufholen reicht. Aufholen verlangt aber
    Varianz und Verteidigen das Gegenteil.
    """
    league = payload["league"]
    assert league["my_rank"] == 3
    assert league["managers_total"] == 4
    assert league["my_season_points"] == 3311
    # 4091 (Platz 1) minus 3311, und 3497 (Platz 2) minus 3311.
    assert league["points_behind_leader"] == 780
    assert league["points_to_next_rank"] == 186
    assert league["matchday"] == 4
    assert league["matchdays_left"] == 30
    assert league["missing_data_flags"] == []

    rivals = league["rivals"]
    assert [r["rank"] for r in rivals] == [1, 2, 3, 4]
    assert sum(1 for r in rivals if r["is_me"]) == 1
    me = next(r for r in rivals if r["is_me"])
    assert me["points_vs_me"] == 0
    # Positiv = liegt vor mir, negativ = dahinter.
    assert rivals[0]["points_vs_me"] == 780
    assert rivals[3]["points_vs_me"] == -683
    # Der Teamwert der Rivalen ist das einzige Maß für ihre Finanzkraft.
    assert all(r["team_value"] > 0 for r in rivals)


def test_league_block_never_ships_the_rivals_names(payload: dict[str, Any]) -> None:
    """Der Payload geht an die Anthropic-API — Klarnamen Dritter haben dort nichts zu suchen.

    Rang, Punkte und Teamwert tragen jede Entscheidung; ein Name trägt keine.
    Dieselbe Linie, die die Cassette-Redaktion fürs Repo zieht (`vcr_config.py`).
    Der Liganame bleibt: den hat der Nutzer selbst vergeben.
    """
    for rival in payload["league"]["rivals"]:
        assert "name" not in rival, "Managername im Payload — Datensparsamkeit verletzt"
    assert set(payload["league"]["rivals"][0]) == {
        "rank",
        "season_points",
        "matchday_points",
        "team_value",
        "points_vs_me",
        "is_me",
    }


def test_head_to_head_fields_stay_out_of_a_season_points_league(payload: dict[str, Any]) -> None:
    """`hhmp`/`hhsp` sind hier 0 und würden nur Rauschen in den Prompt tragen.

    Im H2H-Modus kommen sie dazu — zusammen mit dem Flag für den Wochengegner,
    den Kickbase in **keiner** Response nennt.
    """
    assert payload["constraints"]["scoring_mode"] == "season_points"
    assert "my_h2h_match_points" not in payload["league"]
    assert all("h2h_match_points" not in r for r in payload["league"]["rivals"])
    assert "missing_data:h2h_opponent" not in payload["league"]["missing_data_flags"]
