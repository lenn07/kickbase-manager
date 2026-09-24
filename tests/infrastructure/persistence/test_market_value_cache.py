"""Der Marktwert-Cache gegen echtes SQLite (P1-7).

Das In-Memory-Doppel in `tests/application/test_market_trend_sources.py` prüft
das Verhalten des Enrichers. Hier geht es um die Dinge, die nur das echte
Repository falsch machen kann: Serialisierung von `Decimal` und `datetime`
durch die JSON-Spalte, die Zeitzone beim Rücklesen (SQLite gibt naive
Datetimes) und die Ein-Zeile-pro-Spieler-Garantie.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.domain.models import MarketValuePoint
from app.infrastructure.persistence.models import MarketValueCacheRow
from app.infrastructure.persistence.repositories import MarketValueCacheRepository
from sqlmodel import Session, SQLModel, create_engine, select

LEAGUE = "L1"
NOW = datetime(2026, 9, 23, 16, 0, tzinfo=UTC)
VALID_UNTIL = datetime(2026, 9, 23, 20, 0, tzinfo=UTC)


@pytest.fixture
def session():  # type: ignore[no-untyped-def]
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _points() -> list[MarketValuePoint]:
    return [
        MarketValuePoint(day=datetime(2026, 9, 21, tzinfo=UTC), value=Decimal("33690210")),
        MarketValuePoint(day=datetime(2026, 9, 22, tzinfo=UTC), value=Decimal("33691880")),
        MarketValuePoint(day=datetime(2026, 9, 23, tzinfo=UTC), value=Decimal("33697577")),
    ]


def test_roundtrip_keeps_values_and_timestamps(session: Session) -> None:
    """Rein und raus muss dieselbe Serie ergeben — sonst rechnen Trends falsch.

    Besonders `Decimal`: würde der Wert als float durch die JSON-Spalte
    laufen, käme bei 33.697.577 zwar noch derselbe Betrag heraus, aber die
    Differenz zweier Tage (hier 5.697 von 33 Millionen) lebt in genau den
    Stellen, die float verliert.
    """
    repo = MarketValueCacheRepository(session)
    repo.put(LEAGUE, "1991", _points(), valid_until=VALID_UNTIL)

    loaded = repo.get_many(LEAGUE, ["1991"], now=NOW)
    assert loaded["1991"] == _points()
    assert all(isinstance(p.value, Decimal) for p in loaded["1991"])


def test_expired_entries_are_not_served(session: Session) -> None:
    repo = MarketValueCacheRepository(session)
    repo.put(LEAGUE, "1991", _points(), valid_until=VALID_UNTIL)

    assert repo.get_many(LEAGUE, ["1991"], now=VALID_UNTIL + timedelta(seconds=1)) == {}
    # Exakt auf der Grenze zählt der Eintrag ebenfalls als abgelaufen: zu
    # diesem Zeitpunkt schreibt Kickbase gerade die neuen Marktwerte.
    assert repo.get_many(LEAGUE, ["1991"], now=VALID_UNTIL) == {}


def test_a_player_never_gets_a_second_row(session: Session) -> None:
    """Wiederholtes Schreiben ersetzt, es sammelt nicht an.

    Sonst wüchse die Tabelle mit jedem Tick statt mit der Zahl der Spieler —
    auf einer SD-Karte im Pi ist das kein theoretisches Problem.
    """
    repo = MarketValueCacheRepository(session)
    for _ in range(3):
        repo.put(LEAGUE, "1991", _points(), valid_until=VALID_UNTIL)

    rows = list(session.exec(select(MarketValueCacheRow)))
    assert len(rows) == 1


def test_leagues_do_not_share_entries(session: Session) -> None:
    repo = MarketValueCacheRepository(session)
    repo.put(LEAGUE, "1991", _points(), valid_until=VALID_UNTIL)

    assert repo.get_many("other-league", ["1991"], now=NOW) == {}


def test_unknown_players_are_simply_absent(session: Session) -> None:
    """Ein Cache-Miss ist kein Fehler — der Aufrufer holt dann per HTTP."""
    repo = MarketValueCacheRepository(session)
    assert repo.get_many(LEAGUE, ["1991", "4711"], now=NOW) == {}
    assert repo.get_many(LEAGUE, [], now=NOW) == {}
