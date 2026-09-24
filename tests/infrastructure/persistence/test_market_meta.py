"""P1-10 — die Markt-Uhren überleben den Neustart.

Der Scheduler legt seine beweglichen Fenster aus `next_matchday_start`. Den
Wert liefert nur ein Tick, und beim Start des Containers ist noch keiner
gelaufen: `IntervalTrigger(minutes=120)` feuert erstmals **nach** zwei Stunden.

Ohne diese Zeile stünde nach jedem Neustart bis dahin kein Deadline-Fenster.
Startet der Container am Freitagabend neu, ist genau der Moment weg, an dem
das Konto ins Plus muss — und ein negatives Konto zum Anpfiff kostet den
ganzen Spieltag.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.infrastructure.persistence.repositories import MarketMetaRepository
from sqlmodel import Session, SQLModel, create_engine

LEAGUE = "L1"
KICKOFF = datetime(2026, 10, 9, 18, 30, tzinfo=UTC)
MV_UPDATE = datetime(2026, 9, 23, 20, 0, tzinfo=UTC)


@pytest.fixture
def session():  # type: ignore[no-untyped-def]
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def test_both_clocks_survive_a_roundtrip(session: Session) -> None:
    repo = MarketMetaRepository(session)
    repo.upsert(LEAGUE, next_matchday_start=KICKOFF, mv_update_at=MV_UPDATE)

    row = repo.get(LEAGUE)
    assert row is not None
    assert row.next_matchday_start.replace(tzinfo=UTC) == KICKOFF
    assert row.mv_update_at.replace(tzinfo=UTC) == MV_UPDATE


def test_a_league_keeps_exactly_one_row(session: Session) -> None:
    """Jeder Tick schreibt — die Tabelle darf trotzdem nicht wachsen."""
    repo = MarketMetaRepository(session)
    for i in range(5):
        repo.upsert(
            LEAGUE,
            next_matchday_start=KICKOFF + timedelta(days=i),
            mv_update_at=MV_UPDATE,
        )
    row = repo.get(LEAGUE)
    assert row is not None
    assert row.next_matchday_start.replace(tzinfo=UTC) == KICKOFF + timedelta(days=4)
    assert repo.latest() is not None


def test_latest_works_without_knowing_the_league(session: Session) -> None:
    """Beim Start kennt der Scheduler die Liga-ID noch nicht.

    Sie steht hinter dem Setup, das zu diesem Zeitpunkt nicht gelaufen sein
    muss. Die App ist Single-League, also reicht „die zuletzt geschriebene".
    """
    repo = MarketMetaRepository(session)
    assert repo.latest() is None

    repo.upsert(LEAGUE, next_matchday_start=KICKOFF, mv_update_at=MV_UPDATE)
    latest = repo.latest()
    assert latest is not None
    assert latest.league_id == LEAGUE


def test_unknown_clocks_are_stored_as_unknown(session: Session) -> None:
    """Liefert Kickbase keinen Anpfiff, wird auch keiner erfunden."""
    repo = MarketMetaRepository(session)
    repo.upsert(LEAGUE, next_matchday_start=None, mv_update_at=None)
    row = repo.get(LEAGUE)
    assert row is not None
    assert row.next_matchday_start is None
    assert row.mv_update_at is None
