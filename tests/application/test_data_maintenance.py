"""Tests der Datenverwaltung — Inventar, Abgleich, Vergessen, Export.

Schwerpunkt ist `reconcile`: es ist die einzige Funktion hier, die eine
Entscheidung trifft statt nur zu zählen oder zu löschen, und sie trifft sie
gegen eine Realität, die aus zwei Quellen kommt (Kader und Markt).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.application import data_maintenance as dm
from app.domain.exceptions import KickbaseError
from app.domain.models import (
    MarketPlayer,
    MarketSnapshot,
    Player,
    PlayerStatus,
    Position,
    Squad,
    SquadPlayer,
)
from app.infrastructure.persistence.models import (
    LeagueRow,
    MarketValueCacheRow,
    TradeLogRow,
    UserRow,
)
from app.infrastructure.persistence.repositories import TradeLogRepository
from sqlmodel import Session

from tests.application.conftest import FakeKickbase

_NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
_LEAGUE = "L1"
_MANAGER = "m1"


# -- Aufbau ------------------------------------------------------------


def _user(session: Session) -> UserRow:
    row = UserRow(email="lenn.muster@example.com", encrypted_password=b"x", kb_user_id=_MANAGER)
    session.add(row)
    session.commit()
    session.refresh(row)
    session.add(LeagueRow(user_id=row.id, kb_league_id=_LEAGUE, name="Noob-Liga", is_active=True))
    session.commit()
    return row


def _player(pid: str) -> Player:
    return Player(
        id=pid,
        first_name="Vor",
        last_name=f"Name{pid}",
        team_id="t1",
        position=Position.MIDFIELDER,
        status=PlayerStatus.FIT,
        market_value=Decimal(1_000_000),
    )


def _market_player(pid: str, *, seller_id: str | None, listed_at: datetime | None) -> MarketPlayer:
    return MarketPlayer(
        player=_player(pid),
        price=Decimal(1_000_000),
        expires_in_s=3600,
        seller_id=seller_id,
        listed_at=listed_at,
    )


def _log(
    session: Session,
    user_id: int,
    *,
    action: str,
    player_id: str,
    ts: datetime,
    executed: bool = True,
    intent: str | None = None,
) -> TradeLogRow:
    row = TradeLogRow(
        user_id=user_id,
        ts=ts,
        action=action,
        player_id=player_id,
        price=1_000_000,
        executed=executed,
        context={"intent": intent} if intent else {},
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


class _ReconcileKickbase(FakeKickbase):
    """Fake mit steuerbarem Kader und Markt — beides braucht der Abgleich."""

    def __init__(
        self,
        *,
        squad_ids: tuple[str, ...] = (),
        market: tuple[MarketPlayer, ...] = (),
        fail: bool = False,
    ) -> None:
        super().__init__()
        self._squad_ids = squad_ids
        self._market = market
        self._fail = fail

    async def get_squad(self, league_id: str, manager_id: str) -> Squad:
        if self._fail:
            raise KickbaseError("Kickbase down")
        return Squad(
            league_id=league_id,
            manager_id=manager_id,
            players=tuple(SquadPlayer(player=_player(pid)) for pid in self._squad_ids),
        )

    async def get_market(self, league_id: str) -> MarketSnapshot:
        if self._fail:
            raise KickbaseError("Kickbase down")
        return MarketSnapshot(
            players=self._market,
            team_value=Decimal(100_000_000),
            mv_update_at=None,
            next_matchday_start=None,
            matchday=5,
            squad_size=len(self._squad_ids),
            season="26/27",
        )


async def _run(session: Session, user_id: int, kickbase: FakeKickbase) -> dm.ReconcileReport:
    return await dm.reconcile(
        session,
        kickbase,
        user_id=user_id,
        manager_id=_MANAGER,
        league_id=_LEAGUE,
        now=_NOW,
    )


# -- Inventar ----------------------------------------------------------


def test_inventory_on_empty_database(db_session: Session) -> None:
    inventory = dm.collect_inventory(db_session, now=_NOW)
    assert inventory.account is None
    assert inventory.trade_log.total == 0
    assert inventory.cached_rows_total == 0
    assert [c.key for c in inventory.caches] == ["market_values", "performance", "competition"]


def test_inventory_counts_rows_and_masks_email(db_session: Session) -> None:
    user = _user(db_session)
    assert user.id is not None
    _log(db_session, user.id, action="BUY", player_id="p1", ts=_NOW - timedelta(days=2))
    _log(db_session, user.id, action="HOLD", player_id="p2", ts=_NOW, executed=False)
    db_session.add(
        MarketValueCacheRow(
            league_id=_LEAGUE,
            player_id="p1",
            points=[],
            valid_until=_NOW + timedelta(hours=1),
        )
    )
    db_session.commit()

    inventory = dm.collect_inventory(db_session, now=_NOW)

    assert inventory.trade_log.total == 2
    assert inventory.trade_log.executed == 1
    assert inventory.account is not None
    assert inventory.account.email_masked == "le…@example.com"
    assert inventory.account.league_name == "Noob-Liga"
    mv = next(c for c in inventory.caches if c.key == "market_values")
    assert mv.stats.rows == 1
    assert mv.stats.fresh == 1


def test_inventory_counts_expired_cache_rows_as_not_fresh(db_session: Session) -> None:
    db_session.add(
        MarketValueCacheRow(
            league_id=_LEAGUE, player_id="p1", points=[], valid_until=_NOW - timedelta(hours=1)
        )
    )
    db_session.commit()
    mv = next(
        c for c in dm.collect_inventory(db_session, now=_NOW).caches if c.key == "market_values"
    )
    assert mv.stats.rows == 1
    assert mv.stats.fresh == 0


# -- Caches ------------------------------------------------------------


def test_clear_caches_removes_rows_but_spares_market_meta(db_session: Session) -> None:
    db_session.add(
        MarketValueCacheRow(league_id=_LEAGUE, player_id="p1", points=[], valid_until=_NOW)
    )
    db_session.commit()

    result = dm.clear_caches(db_session)

    assert result.market_values == 1
    assert result.market_meta == 0  # nur mit include_market_meta
    assert dm.collect_inventory(db_session, now=_NOW).cached_rows_total == 0


# -- Vergessen und Löschen ---------------------------------------------


def test_forget_memory_marks_rows_but_keeps_them(db_session: Session) -> None:
    user = _user(db_session)
    assert user.id is not None
    _log(db_session, user.id, action="BUY", player_id="p1", ts=_NOW)
    _log(db_session, user.id, action="HOLD", player_id="p2", ts=_NOW, executed=False)

    assert dm.forget_memory(db_session, user.id, now=_NOW) == 2

    stats = dm.collect_inventory(db_session, now=_NOW).trade_log
    assert stats.total == 2  # Historie unberührt
    assert stats.superseded == 2


def test_forget_memory_is_idempotent(db_session: Session) -> None:
    user = _user(db_session)
    assert user.id is not None
    _log(db_session, user.id, action="BUY", player_id="p1", ts=_NOW)

    assert dm.forget_memory(db_session, user.id, now=_NOW) == 1
    assert dm.forget_memory(db_session, user.id, now=_NOW) == 0


def test_forgotten_buy_disappears_from_the_memory_query(db_session: Session) -> None:
    """Der eigentliche Zweck: `last_executed_buys` ist die Quelle der offenen Gebote."""
    user = _user(db_session)
    assert user.id is not None
    _log(db_session, user.id, action="BUY", player_id="p1", ts=_NOW)
    trades = TradeLogRepository(db_session)
    assert "p1" in trades.last_executed_buys(user.id)

    dm.forget_memory(db_session, user.id, now=_NOW)

    assert trades.last_executed_buys(user.id) == {}


def test_purge_history_removes_rows(db_session: Session) -> None:
    user = _user(db_session)
    assert user.id is not None
    _log(db_session, user.id, action="BUY", player_id="p1", ts=_NOW)
    _log(db_session, user.id, action="HOLD", player_id="p2", ts=_NOW, executed=False)

    assert dm.purge_history(db_session, user.id) == 2
    assert dm.collect_inventory(db_session, now=_NOW).trade_log.total == 0


# -- Abgleich ----------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_keeps_buy_of_a_player_still_in_the_squad(db_session: Session) -> None:
    user = _user(db_session)
    assert user.id is not None
    row = _log(db_session, user.id, action="BUY", player_id="p1", ts=_NOW - timedelta(days=1))

    report = await _run(db_session, user.id, _ReconcileKickbase(squad_ids=("p1",)))

    db_session.refresh(row)
    assert row.superseded_at is None
    assert report.superseded_buys == 0
    assert report.squad_size == 1


@pytest.mark.asyncio
async def test_reconcile_supersedes_buy_of_a_player_gone_from_squad_and_market(
    db_session: Session,
) -> None:
    """Der Fall „selbst verkauft" bzw. „Gebot verloren"."""
    user = _user(db_session)
    assert user.id is not None
    row = _log(db_session, user.id, action="BUY", player_id="p1", ts=_NOW - timedelta(days=1))

    report = await _run(db_session, user.id, _ReconcileKickbase(squad_ids=("p2",)))

    db_session.refresh(row)
    assert row.superseded_at is not None
    assert report.superseded_buys == 1
    assert any("laufende Gebote" in note for note in report.notes)


@pytest.mark.asyncio
async def test_reconcile_keeps_bid_on_a_player_still_listed(db_session: Session) -> None:
    user = _user(db_session)
    assert user.id is not None
    listed = _NOW - timedelta(days=2)
    row = _log(db_session, user.id, action="BUY", player_id="p1", ts=_NOW - timedelta(days=1))

    report = await _run(
        db_session,
        user.id,
        _ReconcileKickbase(market=(_market_player("p1", seller_id="other", listed_at=listed),)),
    )

    db_session.refresh(row)
    assert row.superseded_at is None
    assert report.superseded_buys == 0


@pytest.mark.asyncio
async def test_reconcile_supersedes_bid_that_predates_the_current_listing(
    db_session: Session,
) -> None:
    """Der Spieler war zwischendurch weg und ist neu gelistet — das Gebot ist erloschen."""
    user = _user(db_session)
    assert user.id is not None
    row = _log(db_session, user.id, action="BUY", player_id="p1", ts=_NOW - timedelta(days=5))
    listed_after_the_bid = _NOW - timedelta(hours=2)

    report = await _run(
        db_session,
        user.id,
        _ReconcileKickbase(
            market=(_market_player("p1", seller_id="other", listed_at=listed_after_the_bid),)
        ),
    )

    db_session.refresh(row)
    assert row.superseded_at is not None
    assert report.superseded_buys == 1


@pytest.mark.asyncio
async def test_reconcile_supersedes_listing_that_is_no_longer_on_the_market(
    db_session: Session,
) -> None:
    user = _user(db_session)
    assert user.id is not None
    row = _log(db_session, user.id, action="LIST_ON_MARKET", player_id="p1", ts=_NOW)

    report = await _run(db_session, user.id, _ReconcileKickbase(squad_ids=("p1",)))

    db_session.refresh(row)
    assert row.superseded_at is not None
    assert report.superseded_listings == 1


@pytest.mark.asyncio
async def test_reconcile_keeps_listing_that_is_still_our_own(db_session: Session) -> None:
    user = _user(db_session)
    assert user.id is not None
    row = _log(db_session, user.id, action="LIST_ON_MARKET", player_id="p1", ts=_NOW)

    report = await _run(
        db_session,
        user.id,
        _ReconcileKickbase(
            squad_ids=("p1",),
            market=(_market_player("p1", seller_id=_MANAGER, listed_at=_NOW),),
        ),
    )

    db_session.refresh(row)
    assert row.superseded_at is None
    assert report.superseded_listings == 0
    assert report.own_listings == 1


@pytest.mark.asyncio
async def test_reconcile_leaves_hold_rows_alone(db_session: Session) -> None:
    """HOLD behauptet nichts über Besitz — es gibt dort nichts zu widerlegen."""
    user = _user(db_session)
    assert user.id is not None
    row = _log(db_session, user.id, action="HOLD", player_id="p1", ts=_NOW, executed=False)

    await _run(db_session, user.id, _ReconcileKickbase())

    db_session.refresh(row)
    assert row.superseded_at is None


@pytest.mark.asyncio
async def test_reconcile_counts_squad_players_the_bot_never_bought(db_session: Session) -> None:
    user = _user(db_session)
    assert user.id is not None
    _log(db_session, user.id, action="BUY", player_id="p1", ts=_NOW, intent="PROFIT")

    report = await _run(db_session, user.id, _ReconcileKickbase(squad_ids=("p1", "p2", "p3")))

    assert report.squad_without_intent == 2
    assert any("nicht selbst" in note for note in report.notes)


@pytest.mark.asyncio
async def test_reconcile_reports_error_without_touching_anything(db_session: Session) -> None:
    user = _user(db_session)
    assert user.id is not None
    row = _log(db_session, user.id, action="BUY", player_id="p1", ts=_NOW)

    report = await _run(db_session, user.id, _ReconcileKickbase(fail=True))

    assert not report.ok
    assert report.error is not None
    db_session.refresh(row)
    assert row.superseded_at is None


@pytest.mark.asyncio
async def test_reconcile_clears_caches_by_default(db_session: Session) -> None:
    user = _user(db_session)
    assert user.id is not None
    db_session.add(
        MarketValueCacheRow(league_id=_LEAGUE, player_id="p1", points=[], valid_until=_NOW)
    )
    db_session.commit()

    report = await _run(db_session, user.id, _ReconcileKickbase())

    assert report.caches_cleared is not None
    assert report.caches_cleared.market_values == 1


# -- Export ------------------------------------------------------------


def test_export_contains_history_but_no_secrets(db_session: Session) -> None:
    user = _user(db_session)
    assert user.id is not None
    _log(db_session, user.id, action="BUY", player_id="p1", ts=_NOW, intent="PROFIT")

    payload = dm.build_export(db_session, now=_NOW)
    flat = repr(payload)

    assert payload["trade_log"]["total"] == 1
    assert payload["trade_log"]["entries"][0]["action"] == "BUY"
    assert payload["settings"]["interval_min"] == 120
    # Weder die Klartext-Adresse noch irgendein Cipher-Text.
    assert "lenn.muster@example.com" not in flat
    assert payload["account"]["email_masked"] == "le…@example.com"
    assert "encrypted" not in flat
    assert "password" not in flat


def test_export_works_before_setup(db_session: Session) -> None:
    payload = dm.build_export(db_session, now=_NOW)
    assert payload["account"] is None
    assert payload["settings"] is None
    assert payload["trade_log"]["entries"] == []
