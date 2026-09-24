"""P1-8 — echte Form und Minuten statt Saison-Durchschnitt (Defekt D9).

Der Kern des Pakets sind drei Entscheidungen, und jede davon kann still
danebengehen:

1. **Welche Einträge zählen.** `/performance` liefert alle Saisons seit
   2016/17 *und* alle kommenden Spieltage. Wer einfach die letzten fünf
   Einträge nimmt, mittelt über die Zukunft und bekommt lauter Nullen.
2. **Wie das Fenster bei kurzer Historie aussieht.** Am 4. Spieltag hat
   niemand fünf Einträge — ein pauschales `None` wäre D9 mit neuem Etikett.
3. **Wann gecacht werden darf.** Während ein Spieltag läuft, bewegen sich die
   Punkte noch.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.application.player_enrichment import PlayerEnricher, _form_from_performance
from app.domain.exceptions import KickbaseError
from app.domain.models import (
    MarketValuePoint,
    MatchdayPerformance,
    PlayerDetail,
    PlayerPerformance,
    Squad,
)
from app.infrastructure.kickbase.dto import (
    PlayerPerformanceResponseDTO,
    SquadResponseDTO,
)

from tests.infrastructure.kickbase.vcr_config import (
    FAKE_LEAGUE_ID,
    FAKE_USER_ID,
    load_cassette_payload,
)

NOW = datetime(2026, 9, 23, 16, 0, tzinfo=UTC)
NEXT_MATCHDAY = datetime(2026, 10, 9, 18, 30, tzinfo=UTC)


def _performance() -> PlayerPerformance:
    raw = load_cassette_payload("player_performance")
    return PlayerPerformanceResponseDTO.model_validate(raw).to_domain("1991")


def _squad() -> Squad:
    raw = load_cassette_payload("squad")
    return SquadResponseDTO.model_validate(raw).to_domain(FAKE_LEAGUE_ID, FAKE_USER_ID)


# -- Parsing --------------------------------------------------------------


def test_only_the_current_season_survives() -> None:
    """Elf Saisons kommen an, eine bleibt übrig.

    Die Form von 2019/20 beantwortet keine Frage, die dieser Bot stellt — und
    sie stünde als jüngste Einträge im Fenster, wenn man die Gruppen zusammen
    wirft.
    """
    perf = _performance()
    assert perf.season == "2026/2027"
    assert len(load_cassette_payload("player_performance")["it"]) > 1


def test_future_matchdays_are_not_form() -> None:
    """Nur abgeschlossene Spieltage (`mdst == 2`) zählen.

    In der Cassette stehen 34 Spieltage der laufenden Saison, gespielt sind
    vier. Die übrigen 30 tragen weder Punkte noch Minuten — als Datenpunkte
    gelesen würden sie jeden Schnitt gegen null ziehen.
    """
    perf = _performance()
    assert [m.day for m in perf.matchdays] == [1, 2, 3, 4]
    assert all(m.minutes > 0 for m in perf.matchdays)


def test_minutes_are_parsed_from_the_apostrophe_format() -> None:
    """`mp` kommt als `"96'"`, nicht als Zahl.

    Ein int-Feld im DTO würde hier mit einem ValidationError aussteigen und
    die gesamte Anreicherung mitreißen — der Tick liefe dann ohne jedes
    Zusatzsignal weiter, ohne dass jemand die Ursache sähe.
    """
    raw = load_cassette_payload("player_performance")["it"][-1]["ph"][0]
    assert isinstance(raw["mp"], str) and raw["mp"].endswith("'")
    assert _performance().matchdays[0].minutes == int(raw["mp"].rstrip("'"))


def test_starting_eleven_is_read_from_the_appearance_code() -> None:
    """`st == 5` markiert die Startelf.

    Die Zuordnung ist nicht dokumentiert, aber über die Minutenverteilung
    derselben Cassette eindeutig: `st=5` hat einen Median von 90 Minuten,
    `st=3` (eingewechselt) einen von 22.
    """
    perf = _performance()
    assert all(m.was_in_starting_xi for m in perf.matchdays)
    minutes_when_starting = [m.minutes for m in perf.matchdays if m.was_in_starting_xi]
    assert min(minutes_when_starting) >= 45


# -- Fenster-Mathematik ---------------------------------------------------


def test_window_uses_the_last_five_played_matchdays() -> None:
    long_history = PlayerPerformance(
        player_id="p",
        season="2026/2027",
        matchdays=tuple(
            MatchdayPerformance(day=d, points=d * 10, minutes=90, was_in_starting_xi=True)
            for d in range(1, 9)
        ),
    )
    form = _form_from_performance(long_history)
    assert form.matchdays_counted == 5
    # Spieltage 4..8 ⇒ 40,50,60,70,80
    assert form.avg_points == 60.0
    assert form.starts == 5


def test_short_history_is_counted_not_discarded() -> None:
    """Die Korrektur am Plan-Test: ein kürzeres Fenster wird gerechnet.

    Der Plan verlangt für „< 5 Spieltage" ein `None`. Am 4. Spieltag hätte
    dann **kein einziger** Spieler eine Form — also genau der Zustand, den D9
    beschreibt. Der Wert wird stattdessen geliefert und die Fenstergröße
    daneben gestellt, damit das Modell ihn gewichten kann.
    """
    form = _form_from_performance(_performance())
    assert form.matchdays_counted == 4
    assert form.avg_points == round((272 + 217 + 126 + 97) / 4, 2)
    assert form.avg_minutes == round((96 + 96 + 98 + 48) / 4, 1)
    assert form.starts == 4


def test_no_played_matchday_yields_nothing() -> None:
    """Ohne einen einzigen Einsatz gibt es nichts zu mitteln — das bleibt `None`."""
    empty = PlayerPerformance(player_id="p", season="2026/2027", matchdays=())
    form = _form_from_performance(empty)
    assert form.avg_points is None
    assert form.avg_minutes is None
    assert form.starts is None
    assert form.matchdays_counted == 0

    assert _form_from_performance(None).avg_points is None


def test_minutes_separate_two_players_with_the_same_points() -> None:
    """Der Fall, für den das Paket da ist.

    Gleiche Punkte, andere Einsatzzeit: der eine spielt durch, der andere
    kommt zweimal für zehn Minuten. Ohne Minuten sehen beide identisch aus.
    """
    starter = PlayerPerformance(
        player_id="a",
        season="2026/2027",
        matchdays=tuple(
            MatchdayPerformance(day=d, points=100, minutes=90, was_in_starting_xi=True)
            for d in range(1, 5)
        ),
    )
    joker = PlayerPerformance(
        player_id="b",
        season="2026/2027",
        matchdays=tuple(
            MatchdayPerformance(day=d, points=100, minutes=10, was_in_starting_xi=False)
            for d in range(1, 5)
        ),
    )
    a, b = _form_from_performance(starter), _form_from_performance(joker)
    assert a.avg_points == b.avg_points
    assert a.avg_minutes != b.avg_minutes
    assert (a.starts, b.starts) == (4, 0)


# -- Integration im Enricher ---------------------------------------------


def test_enricher_prefers_real_form_over_the_season_average() -> None:
    gateway = _Gateway(performance=_performance())
    enricher = PlayerEnricher(gateway, performance_cache=_FakeCache(), max_market_history=0)
    result = _run(enricher)

    entry = result["1991"]
    assert entry.avg_points_last5 == round((272 + 217 + 126 + 97) / 4, 2)
    assert entry.form_matchdays_counted == 4
    assert "missing_data:avg_points_last5_using_season_avg" not in entry.missing_data_flags
    # Kürzeres Fenster als versprochen — das muss sichtbar bleiben.
    assert "missing_data:avg_points_last5_partial_window" in entry.missing_data_flags


def test_without_performance_data_the_season_average_remains() -> None:
    """Hat ein Spieler diese Saison nicht gespielt, greift der Saison-Ø weiter.

    Der Fall ist häufiger als er klingt: Winterzugänge, lange Verletzte,
    Aufsteiger-Kader. Eine leere Spieltagsliste ist dann eine Aussage über
    diese Saison, kein Ladefehler — und der Saison-Durchschnitt bleibt das
    Beste, was da ist.
    """
    empty = PlayerPerformance(player_id="x", season="2026/2027", matchdays=())
    enricher = PlayerEnricher(
        _Gateway(performance=empty), performance_cache=_FakeCache(), max_market_history=0
    )
    entry = _run(enricher)["1991"]
    assert entry.form_matchdays_counted == 0
    assert entry.minutes_last5 is None
    assert "missing_data:avg_points_last5_using_season_avg" in entry.missing_data_flags


def test_the_cache_is_optional_the_data_is_not() -> None:
    """Ohne Cache wird jeder Tick geholt — teuer, aber nicht blind.

    Hinge die Form am Cache, verlöre ein Cache-Ausfall sie stillschweigend und
    der Bot fiele auf den Saison-Ø zurück, ohne dass es irgendwo auffiele.
    """
    gateway = _Gateway(performance=_performance())
    entry = _run(PlayerEnricher(gateway, max_market_history=0))["1991"]
    assert entry.form_matchdays_counted == 4
    assert len(gateway.performance_calls) == len(_squad().players)


def test_performance_cache_saves_the_repeat_calls() -> None:
    gateway = _Gateway(performance=_performance())
    cache = _FakeCache()
    enricher = PlayerEnricher(gateway, performance_cache=cache, max_market_history=0)

    _run(enricher)
    first = len(gateway.performance_calls)
    assert first == len(_squad().players)

    _run(enricher)
    assert len(gateway.performance_calls) == first


def test_a_running_matchday_is_never_cached() -> None:
    """Liegt der nächste Anpfiff hinter uns, läuft gerade ein Spieltag.

    Dann bewegen sich die Punkte noch — ein Cache-Eintrag würde den Bot genau
    in den Stunden einfrieren, in denen sich am meisten ändert.
    """
    gateway = _Gateway(performance=_performance())
    cache = _FakeCache()
    enricher = PlayerEnricher(gateway, performance_cache=cache, max_market_history=0)

    _run(enricher, next_matchday_start=NOW - timedelta(hours=1))
    assert cache.entries == {}


def test_failed_performance_call_falls_back_to_the_season_average() -> None:
    """Ein Fehler kostet Genauigkeit, nicht den Tick."""
    gateway = _Gateway(performance=None, raise_on_performance=True)
    enricher = PlayerEnricher(gateway, performance_cache=_FakeCache(), max_market_history=0)
    entry = _run(enricher)["1991"]
    assert entry.avg_points_last5 is not None
    assert "missing_data:avg_points_last5_using_season_avg" in entry.missing_data_flags


# -- Hilfen ---------------------------------------------------------------


class _Gateway:
    def __init__(
        self, *, performance: PlayerPerformance | None, raise_on_performance: bool = False
    ) -> None:
        self._performance = performance
        self._raise = raise_on_performance
        self.performance_calls: list[str] = []

    async def get_market_value_history(
        self, league_id: str, player_id: str, days: int = 7
    ) -> list[MarketValuePoint]:
        del league_id, player_id, days
        return [MarketValuePoint(day=NOW, value=Decimal(1_000_000))]

    async def get_player_detail(self, league_id: str, player_id: str) -> PlayerDetail:
        del league_id
        return PlayerDetail(player_id=player_id, is_predicted_starter=True)

    async def get_player_performance(self, league_id: str, player_id: str) -> PlayerPerformance:
        del league_id
        self.performance_calls.append(player_id)
        if self._raise:
            raise KickbaseError("kaputt")
        assert self._performance is not None
        return PlayerPerformance(
            player_id=player_id,
            season=self._performance.season,
            matchdays=self._performance.matchdays,
        )


class _FakeCache:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], tuple[PlayerPerformance, datetime]] = {}

    def get_many(
        self, league_id: str, player_ids, *, now: datetime
    ) -> dict[str, PlayerPerformance]:
        out = {}
        for pid in player_ids:
            hit = self.entries.get((league_id, pid))
            if hit is not None and hit[1] > now:
                out[pid] = hit[0]
        return out

    def put(self, league_id: str, player_id: str, performance, *, valid_until: datetime) -> None:
        self.entries[(league_id, player_id)] = (performance, valid_until)


def _run(enricher: PlayerEnricher, *, next_matchday_start: datetime | None = NEXT_MATCHDAY):  # type: ignore[no-untyped-def]
    return asyncio.run(
        enricher.enrich(
            FAKE_LEAGUE_ID,
            _squad(),
            (),
            mv_update_at=None,
            next_matchday_start=next_matchday_start,
            now=NOW,
        )
    )
