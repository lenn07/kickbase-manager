"""Wer liest überholte Zeilen — und wer nicht.

Die Trennung ist der Kern der Datenverwaltung: `superseded_at` darf nur dort
wirken, wo der Bot seinen **Weltzustand** ableitet. Wo die Tabelle als
**Sicherung** dient, muss die Zeile weiter zählen. Diese Tests halten beide
Seiten fest, weil die Unterscheidung dem Code nicht anzusehen ist.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from app.infrastructure.persistence.models import TradeLogRow, UserRow
from app.infrastructure.persistence.repositories import TradeLogRepository
from sqlmodel import Session, SQLModel, create_engine

_NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


@pytest.fixture
def user_id(session: Session) -> int:
    row = UserRow(email="u@example.com", encrypted_password=b"x", kb_user_id="m1")
    session.add(row)
    session.commit()
    session.refresh(row)
    assert row.id is not None
    return row.id


def _add(session: Session, user_id: int, action: str, *, superseded: bool) -> TradeLogRow:
    row = TradeLogRow(
        user_id=user_id,
        ts=_NOW - timedelta(minutes=1),
        action=action,
        player_id="p1",
        price=1_000_000,
        executed=True,
        superseded_at=_NOW if superseded else None,
    )
    session.add(row)
    session.commit()
    return row


# -- Gefiltert: der Weltzustand ----------------------------------------


def test_last_executed_buys_skips_superseded(session: Session, user_id: int) -> None:
    _add(session, user_id, "BUY", superseded=True)
    assert TradeLogRepository(session).last_executed_buys(user_id) == {}


def test_last_listing_ts_skips_superseded(session: Session, user_id: int) -> None:
    _add(session, user_id, "LIST_ON_MARKET", superseded=True)
    assert TradeLogRepository(session).last_listing_ts_by_player(user_id) == {}


def test_list_recent_can_exclude_superseded(session: Session, user_id: int) -> None:
    _add(session, user_id, "BUY", superseded=True)
    _add(session, user_id, "HOLD", superseded=False)
    repo = TradeLogRepository(session)

    assert len(repo.list_recent(user_id=user_id)) == 2  # Dashboard sieht alles
    live = repo.list_recent(user_id=user_id, include_superseded=False)
    assert [r.action for r in live] == ["HOLD"]


# -- Ungefiltert: die Sicherungen ---------------------------------------


def test_list_by_action_still_counts_superseded_rows(session: Session, user_id: int) -> None:
    """Die Bonus-Tagessperre überlebt ein Zurücksetzen — sonst löst das
    Aufräumen genau den zweiten Abruf aus, den sie verhindern soll."""
    _add(session, user_id, "BONUS", superseded=True)
    rows = TradeLogRepository(session).list_by_action(user_id=user_id, action="BONUS")
    assert len(rows) == 1


def test_pending_holds_still_include_superseded_rows(session: Session, user_id: int) -> None:
    """Der Digest meldet, was passiert ist — auch wenn es überholt wurde."""
    row = TradeLogRow(user_id=user_id, ts=_NOW, action="HOLD", superseded_at=_NOW)
    session.add(row)
    session.commit()
    assert len(TradeLogRepository(session).list_pending_holds(user_id)) == 1


# -- Markieren ----------------------------------------------------------


def test_mark_superseded_keeps_the_first_timestamp(session: Session, user_id: int) -> None:
    row = _add(session, user_id, "BUY", superseded=False)
    repo = TradeLogRepository(session)

    assert repo.mark_superseded([row], ts=_NOW) == 1
    first = row.superseded_at
    assert repo.mark_superseded([row], ts=_NOW + timedelta(days=1)) == 0
    assert row.superseded_at == first
