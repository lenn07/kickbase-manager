"""Domain-Entities — framework-frei, immutable."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import IntEnum


class Position(IntEnum):
    GOALKEEPER = 1
    DEFENDER = 2
    MIDFIELDER = 3
    FORWARD = 4


class PlayerStatus(IntEnum):
    FIT = 0
    INJURED = 1
    UNKNOWN_2 = 2
    OUT_OF_SQUAD = 4
    REHAB = 8
    RED_CARD = 16
    YELLOW_RED_CARD = 32
    NOT_IN_TEAM = 64


@dataclass(frozen=True, slots=True)
class Session:
    token: str
    token_expires_at: datetime
    user_id: str
    email: str


@dataclass(frozen=True, slots=True)
class League:
    id: str
    name: str
    creator_id: str
    budget: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Player:
    id: str
    first_name: str
    last_name: str
    team_id: str
    position: Position
    status: PlayerStatus
    market_value: Decimal
    average_points: float
    total_points: int


@dataclass(frozen=True, slots=True)
class SquadPlayer:
    player: Player
    # Kickbase v4 Squad-Response enthält keinen Kaufpreis mehr — bleibt 0.
    buy_price: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class Squad:
    league_id: str
    manager_id: str
    players: tuple[SquadPlayer, ...]
    # team_value/budget stehen in /leagues/{id}/me, nicht in /squad — Default 0.
    team_value: Decimal = Decimal(0)
    budget: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class MarketOffer:
    id: str
    user_id: str
    user_name: str
    price: Decimal
    valid_until: datetime | None


@dataclass(frozen=True, slots=True)
class MarketPlayer:
    player: Player
    price: Decimal
    expires_at: datetime | None
    seller_id: str | None  # None → Kickbase-eigener Angebotspool
    offers: tuple[MarketOffer, ...] = ()


@dataclass(frozen=True, slots=True)
class Matchday:
    number: int
    starts_at: datetime
    ends_at: datetime
    is_current: bool


@dataclass(frozen=True, slots=True)
class MarketValuePoint:
    day: datetime
    value: Decimal


@dataclass(frozen=True, slots=True)
class LeagueMe:
    """Meine Sicht auf eine Liga: Budget, Team-Wert, Metadaten."""

    league_id: str
    budget: Decimal
    unread_notifications: int = 0
    is_admin: bool = False
