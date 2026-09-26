"""Spielplan-Kontext: Cache-Verhalten und Ausfalltoleranz (P2-11).

Zwei Zusagen stehen hier im Mittelpunkt, weil sie im Betrieb zählen:

1. **Zwei Calls pro Tag, nicht pro Tick.** Vor P1-7 hat derselbe Fehler den Bot
   25 Requests pro Tick gekostet (Defekt D8) — der Plan führt das unter
   Ban-Risiko (§9). Tabelle und Spielplan ändern sich zwischen zwei Spieltagen
   nicht; ein Tick, der sie erneut holt, holt Byte für Byte dasselbe.
2. **Ein Ausfall kostet keinen Tick.** Gegnerstärke ist ein Komfort-Signal. Die
   harten Regeln (Konto ≥ 0, elf Spieler) hängen nicht daran.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.team_context import TeamContextProvider
from app.domain.exceptions import TransportError
from app.domain.models import CompetitionContext, Fixture, TeamStanding
from app.infrastructure.persistence.models import CompetitionContextCacheRow
from app.infrastructure.persistence.repositories import CompetitionContextCacheRepository
from sqlmodel import select

NOW = datetime(2026, 9, 23, 16, 0, tzinfo=UTC)
NEXT_KICKOFF = datetime(2026, 10, 9, 18, 30, tzinfo=UTC)

_STANDINGS = [
    TeamStanding(
        team_id="3", team_name="Dortmund", rank=1, points=12, matches_played=4, goal_difference=7
    ),
    TeamStanding(
        team_id="15",
        team_name="M'gladbach",
        rank=18,
        points=0,
        matches_played=4,
        goal_difference=-10,
    ),
]
_FIXTURES = [
    Fixture(
        matchday=4,
        kickoff=NOW - timedelta(days=5),
        home_team_id="3",
        away_team_id="15",
        is_finished=True,
    ),
    Fixture(
        matchday=5,
        kickoff=NEXT_KICKOFF,
        home_team_id="15",
        away_team_id="3",
        is_finished=False,
    ),
]


class FakeCompetitionGateway:
    def __init__(
        self,
        *,
        standings: list[TeamStanding] | None = None,
        fixtures: list[Fixture] | None = None,
        table_error: Exception | None = None,
        fixtures_error: Exception | None = None,
    ) -> None:
        self._standings = _STANDINGS if standings is None else standings
        self._fixtures = _FIXTURES if fixtures is None else fixtures
        self._table_error = table_error
        self._fixtures_error = fixtures_error
        self.table_calls = 0
        self.fixture_calls = 0

    async def get_competition_table(self, competition_id: str = "1") -> list[TeamStanding]:
        del competition_id
        self.table_calls += 1
        if self._table_error is not None:
            raise self._table_error
        return list(self._standings)

    async def list_fixtures(self, competition_id: str = "1") -> list[Fixture]:
        del competition_id
        self.fixture_calls += 1
        if self._fixtures_error is not None:
            raise self._fixtures_error
        return list(self._fixtures)


def _provider(gateway: FakeCompetitionGateway, cache: object | None = None) -> TeamContextProvider:
    return TeamContextProvider(gateway, cache=cache)  # type: ignore[arg-type]


async def test_load_returns_the_next_opponent_per_team() -> None:
    provider = _provider(FakeCompetitionGateway())
    outlooks = await provider.load(next_matchday_start=NEXT_KICKOFF, now=NOW)

    assert outlooks["15"].next_opponent_name == "Dortmund"
    assert outlooks["15"].is_home is True
    assert outlooks["15"].fdr == 5
    assert outlooks["3"].fdr == 1


async def test_second_tick_reads_the_cache_instead_of_http(db_session) -> None:  # type: ignore[no-untyped-def]
    """Das eigentliche DoD dieses Pakets im Betrieb: zwei Calls pro Tag."""
    gateway = FakeCompetitionGateway()
    cache = CompetitionContextCacheRepository(db_session)

    first = await _provider(gateway, cache).load(next_matchday_start=NEXT_KICKOFF, now=NOW)
    second = await _provider(gateway, cache).load(
        next_matchday_start=NEXT_KICKOFF, now=NOW + timedelta(hours=2)
    )

    assert gateway.table_calls == 1
    assert gateway.fixture_calls == 1
    assert first["15"].fdr == second["15"].fdr
    assert second["15"].next_opponent_name == "Dortmund"


async def test_cache_expires_after_a_day_even_without_a_matchday(db_session) -> None:  # type: ignore[no-untyped-def]
    """In der Länderspielpause liegt der nächste Anpfiff drei Wochen weg.

    Die Tabelle steht dann still, der Spielplan nicht: Terminverlegungen werden
    mit Wochen Vorlauf bekannt gegeben. Deshalb der 24-Stunden-Deckel.
    """
    gateway = FakeCompetitionGateway()
    cache = CompetitionContextCacheRepository(db_session)

    await _provider(gateway, cache).load(next_matchday_start=NEXT_KICKOFF, now=NOW)
    await _provider(gateway, cache).load(
        next_matchday_start=NEXT_KICKOFF, now=NOW + timedelta(hours=25)
    )

    assert gateway.table_calls == 2


async def test_running_matchday_is_never_cached(db_session) -> None:  # type: ignore[no-untyped-def]
    """Läuft ein Spieltag, bewegt sich die Tabelle — jeder Tick sieht sie frisch.

    Erkennbar daran, dass der nächste Anpfiff in der Vergangenheit liegt: das
    Market-Root-Feld `dt` zeigt bis zum nächsten Payload-Update noch auf den
    laufenden Spieltag. Dieselbe Mechanik nutzt der Spieltags-Cache aus P1-8.
    """
    gateway = FakeCompetitionGateway()
    cache = CompetitionContextCacheRepository(db_session)
    running = NOW - timedelta(hours=1)

    await _provider(gateway, cache).load(next_matchday_start=running, now=NOW)
    await _provider(gateway, cache).load(next_matchday_start=running, now=NOW)

    assert gateway.table_calls == 2


async def test_missing_table_keeps_the_opponent_but_drops_the_difficulty() -> None:
    """Teilausfall: „auswärts bei Dortmund" ist mehr als nichts."""
    gateway = FakeCompetitionGateway(table_error=TransportError("500"))
    outlooks = await _provider(gateway).load(next_matchday_start=NEXT_KICKOFF, now=NOW)

    assert outlooks["15"].next_opponent_id == "3"
    assert outlooks["15"].is_home is True
    assert outlooks["15"].fdr is None
    assert outlooks["15"].next_opponent_name is None


async def test_partial_failure_is_not_written_to_the_cache(db_session) -> None:  # type: ignore[no-untyped-def]
    """Ein halber Kontext darf die fehlende Hälfte nicht für 24 Stunden festschreiben."""
    gateway = FakeCompetitionGateway(table_error=TransportError("500"))
    cache = CompetitionContextCacheRepository(db_session)

    await _provider(gateway, cache).load(next_matchday_start=NEXT_KICKOFF, now=NOW)
    assert cache.get("1", now=NOW) is None


async def test_missing_fixtures_yield_an_empty_mapping() -> None:
    """Ohne Spielplan gibt es keine Aussage — der Payload setzt dann das Flag."""
    gateway = FakeCompetitionGateway(fixtures_error=TransportError("500"))
    assert await _provider(gateway).load(now=NOW) == {}


async def test_cache_round_trip_preserves_every_field(db_session) -> None:  # type: ignore[no-untyped-def]
    """Serialisierung ist verlustfrei — sonst kommt die Tabelle mit Nullen zurück."""
    cache = CompetitionContextCacheRepository(db_session)
    context = CompetitionContext(standings=tuple(_STANDINGS), fixtures=tuple(_FIXTURES))
    cache.put("1", context, valid_until=NOW + timedelta(hours=6))

    restored = cache.get("1", now=NOW)
    assert restored is not None
    assert restored.standings == context.standings
    assert restored.fixtures == context.fixtures


def test_expired_cache_entry_is_not_served(db_session) -> None:  # type: ignore[no-untyped-def]
    cache = CompetitionContextCacheRepository(db_session)
    cache.put("1", CompetitionContext(fixtures=tuple(_FIXTURES)), valid_until=NOW)
    assert cache.get("1", now=NOW) is None
    assert cache.get("1", now=NOW - timedelta(minutes=1)) is not None


def test_cache_keeps_a_single_row_per_competition(db_session) -> None:  # type: ignore[no-untyped-def]
    """Überschreiben statt anhängen: die Tabelle wächst nicht mit den Ticks."""
    cache = CompetitionContextCacheRepository(db_session)
    for _ in range(3):
        cache.put("1", CompetitionContext(fixtures=tuple(_FIXTURES)), valid_until=NOW)

    rows = list(db_session.exec(select(CompetitionContextCacheRow)))
    assert len(rows) == 1
