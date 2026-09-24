"""Der Bot muss seine eigenen laufenden Gebote sehen (Defekt D3).

**Der Fall aus dem Betrieb am 2026-09-24.** Im `trade_log` stand ein Spieler
siebenmal als ausgeführter `BUY`, und er lag immer noch im Markt. Gleichzeitig
meldete der Payload `open_bids_total: 0`. Das Modell hat den Widerspruch selbst
bemerkt — „das deutet auf ein systematisches Problem hin" — und trotzdem weiter
geboten, weil die Daten ihm sagten, es gebe kein offenes Gebot.

Die Ursache ist die Gebots-Mechanik von Kickbase: ein Gebot ist kein Kauf. Es
läuft bis zum Ablauf des Listings, und erst dann bekommt der Höchstbietende den
Zuschlag (bei Gleichstand der früher Bietende). In der Zwischenzeit ist das Geld
gebunden, der Spieler aber noch nicht da — und weil das Gebots-Array im
Market-Payload bis heute unbenannt ist (Plan §8/F1), sieht der Bot davon nichts.

Rekonstruiert wird es deshalb aus dem eigenen `trade_log`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.application.run_tick_uc import _open_bids
from app.domain.models import (
    MarketPlayer,
    Player,
    PlayerStatus,
    Position,
)
from app.infrastructure.persistence.models import TradeLogRow
from app.infrastructure.persistence.repositories import TradeLogRepository, UserRepository
from sqlmodel import Session

MANAGER_ID = "9999999"
NOW = datetime(2026, 9, 24, 20, 0, tzinfo=UTC)


@pytest.fixture
def user_id(db_session: Session) -> int:
    user = UserRepository(db_session).upsert_credentials(
        email="user@example.com", encrypted_password=b"x"
    )
    assert user.id is not None
    return user.id


def _market_player(
    pid: str, *, seller: str | None = "fremd", listed_at: datetime | None = None
) -> MarketPlayer:
    return MarketPlayer(
        player=Player(
            id=pid,
            first_name="",
            last_name=f"Spieler{pid}",
            team_id="2",
            position=Position.MIDFIELDER,
            status=PlayerStatus.FIT,
            market_value=Decimal(10_000_000),
        ),
        price=Decimal(10_000_000),
        expires_in_s=3600,
        seller_id=seller,
        listed_at=listed_at,
    )


def _log_buy(
    trades: TradeLogRepository, user_id: int, pid: str, *, price: int, ts: datetime
) -> None:
    trades.add(
        TradeLogRow(
            user_id=user_id,
            ts=ts,
            action="BUY",
            player_id=pid,
            price=price,
            executed=True,
            reason_text="Gebot abgegeben",
        )
    )


def test_an_executed_buy_on_a_listed_player_is_an_open_bid(
    db_session: Session, user_id: int
) -> None:
    """Der Kernfall: geboten, Spieler liegt noch im Markt, nicht im Kader."""
    trades = TradeLogRepository(db_session)
    _log_buy(trades, user_id, "3284", price=11_487_348, ts=NOW - timedelta(hours=2))

    bids = _open_bids(
        market=[_market_player("3284")],
        manager_id=MANAGER_ID,
        trades=trades,
        user_id=user_id,
        squad_ids=set(),
    )
    assert set(bids) == {"3284"}
    assert bids["3284"].price == Decimal(11_487_348)


def test_repeated_bids_count_once(db_session: Session, user_id: int) -> None:
    """Siebenmal geboten ist **ein** offenes Gebot, nicht sieben.

    Genau diese Zahl stand im Log. Würde jeder Eintrag zählen, wäre
    `open_bids_total` siebenfach überhöht und die 33 %-Rechnung genauso falsch
    wie vorher — nur in die andere Richtung.
    """
    trades = TradeLogRepository(db_session)
    for i in range(7):
        _log_buy(trades, user_id, "2300", price=9_000_000, ts=NOW - timedelta(hours=7 - i))

    bids = _open_bids(
        market=[_market_player("2300")],
        manager_id=MANAGER_ID,
        trades=trades,
        user_id=user_id,
        squad_ids=set(),
    )
    assert len(bids) == 1
    assert bids["2300"].price == Decimal(9_000_000)


def test_a_player_already_in_the_squad_is_not_an_open_bid(
    db_session: Session, user_id: int
) -> None:
    """Steht er im Kader, haben wir den Zuschlag bekommen — das Gebot ist erledigt."""
    trades = TradeLogRepository(db_session)
    _log_buy(trades, user_id, "3284", price=11_000_000, ts=NOW - timedelta(hours=2))

    bids = _open_bids(
        market=[_market_player("3284")],
        manager_id=MANAGER_ID,
        trades=trades,
        user_id=user_id,
        squad_ids={"3284"},
    )
    assert bids == {}


def test_a_player_no_longer_listed_is_not_an_open_bid(db_session: Session, user_id: int) -> None:
    """Kein Listing mehr, keine Entscheidung mehr offen — so oder so."""
    trades = TradeLogRepository(db_session)
    _log_buy(trades, user_id, "3284", price=11_000_000, ts=NOW - timedelta(hours=2))

    bids = _open_bids(
        market=[], manager_id=MANAGER_ID, trades=trades, user_id=user_id, squad_ids=set()
    )
    assert bids == {}


def test_a_bid_from_before_the_current_listing_is_stale(db_session: Session, user_id: int) -> None:
    """Der subtile Fall: derselbe Spieler, aber ein neues Listing.

    Wir haben vor drei Tagen geboten, jemand anders hat ihn bekommen und heute
    neu gelistet. Unser altes Gebot ist mit dem alten Listing erloschen — es als
    offen zu führen, würde Budget binden, das längst frei ist, und das Modell
    davon abhalten, auf das neue Listing zu bieten.
    """
    trades = TradeLogRepository(db_session)
    _log_buy(trades, user_id, "3284", price=11_000_000, ts=NOW - timedelta(days=3))

    bids = _open_bids(
        market=[_market_player("3284", listed_at=NOW - timedelta(hours=4))],
        manager_id=MANAGER_ID,
        trades=trades,
        user_id=user_id,
        squad_ids=set(),
    )
    assert bids == {}


def test_bids_on_our_own_listing_do_not_exist(db_session: Session, user_id: int) -> None:
    """Auf den eigenen Spieler bietet man nicht — das wäre keine Verpflichtung."""
    trades = TradeLogRepository(db_session)
    _log_buy(trades, user_id, "1809", price=9_000_000, ts=NOW - timedelta(hours=1))

    bids = _open_bids(
        market=[_market_player("1809", seller=MANAGER_ID)],
        manager_id=MANAGER_ID,
        trades=trades,
        user_id=user_id,
        squad_ids=set(),
    )
    assert bids == {}


def test_a_dry_run_bid_binds_nothing(db_session: Session, user_id: int) -> None:
    """Nur ausgeführte Gebote binden Geld — Vormerkungen aus dem Dry-Run nicht."""
    trades = TradeLogRepository(db_session)
    trades.add(
        TradeLogRow(
            user_id=user_id,
            ts=NOW - timedelta(hours=1),
            action="BUY",
            player_id="3284",
            price=11_000_000,
            executed=False,
            reason_text="Dry-Run",
        )
    )

    bids = _open_bids(
        market=[_market_player("3284")],
        manager_id=MANAGER_ID,
        trades=trades,
        user_id=user_id,
        squad_ids=set(),
    )
    assert bids == {}
