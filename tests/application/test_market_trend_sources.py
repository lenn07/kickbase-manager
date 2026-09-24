"""P1-7 — Trends aus dem Payload statt aus 25 HTTP-Calls (Defekt D8).

Drei Dinge werden hier gemessen, und alle drei sind Zusagen des Pakets:

1. **Die Feldsemantik stimmt.** `tfhmvt` und `sdmvt` sollen 24-h- und
   7-d-Delta in Euro sein. Der Plan verlangt die Gegenprobe gegen die
   Marktwert-Historie (±0,1 pp) — sie geht sogar exakt auf.
2. **Die Payload-Quelle gewinnt.** Steht der Trend im Squad-Payload, darf er
   nicht aus einer womöglich gecachten Historie überschrieben werden.
3. **Der Cache spart Calls.** Ohne diese Ersparnis ist das DoD des Pakets
   („Requests/Tick messbar gesunken") nicht erfüllt: die Historie wird für
   Kader + Shortlist weiter gebraucht, nur eben nicht zwölfmal am Tag.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.application.player_enrichment import PlayerEnricher, _metrics_from_history
from app.domain.models import (
    MarketPlayer,
    MarketValuePoint,
    Player,
    PlayerDetail,
    PlayerPerformance,
    PlayerStatus,
    Position,
    Squad,
)
from app.infrastructure.kickbase.dto import (
    MarketValueResponseDTO,
    SquadResponseDTO,
)

from tests.infrastructure.kickbase.vcr_config import (
    FAKE_LEAGUE_ID,
    FAKE_USER_ID,
    load_cassette_payload,
)

NOW = datetime(2026, 9, 23, 16, 0, tzinfo=UTC)
MV_UPDATE_AT = datetime(2026, 9, 23, 20, 0, tzinfo=UTC)

# Die Marktwert-Cassette gehört zu diesem Spieler — nur für ihn ist die
# Gegenprobe Payload-gegen-Historie überhaupt aussagekräftig.
HISTORY_PLAYER_ID = "1991"


def _history() -> list[MarketValuePoint]:
    raw = load_cassette_payload("market_value")
    return [p.to_domain() for p in MarketValueResponseDTO.model_validate(raw).it]


def _squad() -> Squad:
    raw = load_cassette_payload("squad")
    return SquadResponseDTO.model_validate(raw).to_domain(FAKE_LEAGUE_ID, FAKE_USER_ID)


def test_payload_deltas_match_the_market_value_history() -> None:
    """`tfhmvt`/`sdmvt` gegen die echte Serie — der Semantik-Nachweis.

    Stimmt die Zuordnung nicht (etwa `sdmvt` als 6-d- oder 8-d-Fenster), ist
    jeder daraus abgeleitete Prozentwert falsch, und zwar still: die Zahl
    sähe plausibel aus.
    """
    history = _history()
    sp = next(s for s in _squad().players if s.player.id == HISTORY_PLAYER_ID)

    assert sp.mv_change_1d == history[-1].value - history[-2].value
    assert sp.mv_change_7d == history[-1].value - history[-8].value

    # Und in Prozent, so wie es in den Payload geht: dieselbe Zahl wie aus der
    # Historie gerechnet, innerhalb der vom Plan geforderten 0,1 pp.
    from_history = _metrics_from_history(history)
    enriched = _enrich_with(squad=_squad(), history=history, cache=None)[HISTORY_PLAYER_ID]
    assert abs(enriched.market_trend_1d_pct - from_history.trend_1d_pct) < 0.1
    assert abs(enriched.market_trend_7d_pct - from_history.trend_7d_pct) < 0.1


def test_squad_trends_survive_a_stale_history() -> None:
    """Der Payload-Wert gewinnt gegen eine veraltete Historie.

    Die Historie darf aus dem Cache kommen, `tfhmvt` kommt immer frisch aus dem
    Squad-Call dieses Ticks. Käme die Reihenfolge durcheinander, zeigte der
    Payload den Trend von gestern und niemand würde es merken.
    """
    stale = [
        MarketValuePoint(day=NOW - timedelta(days=i), value=Decimal(1_000_000))
        for i in range(31, 0, -1)
    ]
    sp = next(s for s in _squad().players if s.player.id == HISTORY_PLAYER_ID)
    enriched = _enrich_with(squad=_squad(), history=stale, cache=None)[HISTORY_PLAYER_ID]

    # Flache Serie ⇒ 0.0 aus der Historie. Der Payload sagt etwas anderes.
    assert enriched.market_trend_1d_pct != 0.0
    assert sp.mv_change_1d != 0
    # Die Fenster ohne Payload-Quelle kommen weiter aus der Serie.
    assert enriched.market_trend_3d_pct == 0.0
    assert enriched.market_trend_30d_pct == 0.0


def test_market_players_keep_history_trends() -> None:
    """Markt-Items tragen `tfhmvt`/`sdmvt` nicht — dort bleibt die Historie.

    Der Wächter gegen die naheliegende Fehlannahme, die Felder stünden überall:
    das DoD des Pakets meint für Marktspieler ausdrücklich die Shortlist.
    """
    market = (_market_player("m1", avg=200.0),)
    enriched = _enrich_with(squad=_squad(), history=_history(), cache=None, market=market)
    from_history = _metrics_from_history(_history())
    assert enriched["m1"].market_trend_1d_pct == from_history.trend_1d_pct


def test_shortlist_follows_points_not_price() -> None:
    """Die Historie geht an den Rohpunkte-Sammler, nicht an den teuersten Namen."""
    gateway = _CountingGateway(_history())
    enricher = PlayerEnricher(gateway, max_market_history=1)
    market = (
        _market_player("teuer", avg=10.0, mv=40_000_000),
        _market_player("stark", avg=180.0, mv=900_000),
    )
    _run(enricher, squad=_empty_squad(), market=market)

    assert gateway.history_calls == ["stark"]


def test_cache_removes_the_repeat_calls() -> None:
    """Zweiter Tick vor dem nächsten Marktwert-Update ⇒ kein einziger Call.

    Das ist die messbare Zusage des Pakets. Ohne Cache holt der Bot bei
    120-min-Takt zwölfmal täglich dieselbe Serie — die sich per Definition nur
    einmal täglich ändert (Plan §2.3).
    """
    gateway = _CountingGateway(_history())
    cache = _FakeCache()
    enricher = PlayerEnricher(gateway, cache=cache, max_market_history=0)
    squad = _squad()

    _run(enricher, squad=squad, market=(), mv_update_at=MV_UPDATE_AT)
    first_round = len(gateway.history_calls)
    assert first_round == len(squad.players)

    _run(enricher, squad=squad, market=(), mv_update_at=MV_UPDATE_AT)
    assert len(gateway.history_calls) == first_round, (
        "Die Historie wurde erneut geholt, obwohl der Marktwert sich bis "
        f"{MV_UPDATE_AT} nicht ändert."
    )


def test_cache_expires_with_the_market_value_update() -> None:
    """Nach dem Update-Zeitpunkt ist der Eintrag wertlos und wird neu geholt."""
    gateway = _CountingGateway(_history())
    cache = _FakeCache()
    enricher = PlayerEnricher(gateway, cache=cache, max_market_history=0)
    squad = _squad()

    _run(enricher, squad=squad, market=(), mv_update_at=MV_UPDATE_AT)
    before = len(gateway.history_calls)

    _run(
        enricher,
        squad=squad,
        market=(),
        mv_update_at=MV_UPDATE_AT + timedelta(days=1),
        now=MV_UPDATE_AT + timedelta(minutes=15),
    )
    assert len(gateway.history_calls) == 2 * before


def test_unknown_update_time_is_not_cached() -> None:
    """Ohne `mvud` wird nichts geschrieben — eine geratene Haltbarkeit wäre schlimmer.

    Ein zu lange gehaltener Eintrag macht den Bot für einen ganzen
    Marktwert-Zyklus blind für die Bewegung, die er handeln soll.
    """
    gateway = _CountingGateway(_history())
    cache = _FakeCache()
    enricher = PlayerEnricher(gateway, cache=cache, max_market_history=0)

    _run(enricher, squad=_squad(), market=(), mv_update_at=None)
    assert cache.entries == {}


def test_broken_cache_does_not_break_the_tick() -> None:
    """Ein Cache-Fehler kostet Requests, nicht den Tick."""

    class _Exploding:
        def get_many(self, league_id, player_ids, *, now):  # type: ignore[no-untyped-def]
            raise RuntimeError("DB weg")

        def put(self, league_id, player_id, points, *, valid_until):  # type: ignore[no-untyped-def]
            raise RuntimeError("DB weg")

    gateway = _CountingGateway(_history())
    enricher = PlayerEnricher(gateway, cache=_Exploding(), max_market_history=0)  # type: ignore[arg-type]
    result = _run(enricher, squad=_squad(), market=(), mv_update_at=MV_UPDATE_AT)

    assert result[HISTORY_PLAYER_ID].mv_max_30d is not None
    assert len(gateway.history_calls) == len(_squad().players)


# -- Hilfen ---------------------------------------------------------------


class _CountingGateway:
    """Liefert jedem Spieler dieselbe Serie und merkt sich, wen es gefragt wurde."""

    def __init__(self, history: list[MarketValuePoint]) -> None:
        self._history = history
        self.history_calls: list[str] = []

    async def get_market_value_history(
        self, league_id: str, player_id: str, days: int = 7
    ) -> list[MarketValuePoint]:
        del league_id, days
        self.history_calls.append(player_id)
        return list(self._history)

    async def get_player_detail(self, league_id: str, player_id: str) -> PlayerDetail:
        # Die Startelf-Kette ist hier Beiwerk; sie darf nur nicht ins Leere
        # laufen. Gemessen wird ausschliesslich `history_calls`.
        del league_id
        return PlayerDetail(player_id=player_id, is_predicted_starter=True)

    async def get_player_performance(self, league_id: str, player_id: str) -> PlayerPerformance:
        # Seit P1-8 fragt der Enricher die Spieltagshistorie ab. Dieses Doppel
        # liefert keine — der Fall „keine Form geladen" ist genauso gültig wie
        # der mit, und die Form-Tests stehen in `test_form_window.py`.
        del league_id
        return PlayerPerformance(player_id=player_id)


class _FakeCache:
    """In-Memory-Doppel des Repositories — prüft das Protokoll, nicht SQLite."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], tuple[list[MarketValuePoint], datetime]] = {}

    def get_many(
        self, league_id: str, player_ids, *, now: datetime
    ) -> dict[str, list[MarketValuePoint]]:
        out = {}
        for pid in player_ids:
            hit = self.entries.get((league_id, pid))
            if hit is not None and hit[1] > now:
                out[pid] = list(hit[0])
        return out

    def put(self, league_id: str, player_id: str, points, *, valid_until: datetime) -> None:
        self.entries[(league_id, player_id)] = (list(points), valid_until)


def _market_player(pid: str, *, avg: float, mv: int = 5_000_000) -> MarketPlayer:
    return MarketPlayer(
        player=Player(
            id=pid,
            first_name="",
            last_name=pid,
            team_id="T1",
            position=Position.MIDFIELDER,
            status=PlayerStatus.FIT,
            market_value=Decimal(mv),
            average_points=avg,
            total_points=int(avg) * 4,
        ),
        price=Decimal(mv),
        expires_in_s=3600,
        seller_id=None,
    )


def _empty_squad() -> Squad:
    return Squad(league_id=FAKE_LEAGUE_ID, manager_id=FAKE_USER_ID, players=())


def _run(
    enricher: PlayerEnricher,
    *,
    squad: Squad,
    market: tuple[MarketPlayer, ...],
    mv_update_at: datetime | None = MV_UPDATE_AT,
    now: datetime = NOW,
):  # type: ignore[no-untyped-def]
    return asyncio.run(
        enricher.enrich(FAKE_LEAGUE_ID, squad, market, mv_update_at=mv_update_at, now=now)
    )


def _enrich_with(
    *,
    squad: Squad,
    history: list[MarketValuePoint],
    cache,  # type: ignore[no-untyped-def]
    market: tuple[MarketPlayer, ...] = (),
):  # type: ignore[no-untyped-def]
    enricher = PlayerEnricher(_CountingGateway(history), cache=cache)
    return _run(enricher, squad=squad, market=market)
