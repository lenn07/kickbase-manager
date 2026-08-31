"""Wire-Format-DTOs für Kickbase v4.

Kickbase liefert kryptische Feldnamen (`fn`, `ln`, `mv`, `st`, ...).
Diese DTOs bilden das Wire-Format 1:1 ab und mappen anschließend auf die
Domain-Modelle — der Rest der App sieht die kryptischen Namen nie.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import (
    League,
    MarketOffer,
    MarketPlayer,
    MarketValuePoint,
    Matchday,
    Player,
    PlayerStatus,
    Position,
    Session,
    Squad,
    SquadPlayer,
)

_DTO_CONFIG = ConfigDict(populate_by_name=True, extra="ignore")

_POSITION_VALUES = frozenset(p.value for p in Position)
_STATUS_VALUES = frozenset(s.value for s in PlayerStatus)


class UserDTO(BaseModel):
    model_config = _DTO_CONFIG

    id: str
    email: str
    name: str = ""


class LoginResponseDTO(BaseModel):
    model_config = _DTO_CONFIG

    token: str
    token_exp: datetime = Field(alias="tokenExp")
    user: UserDTO

    def to_session(self) -> Session:
        return Session(
            token=self.token,
            token_expires_at=self.token_exp,
            user_id=self.user.id,
            email=self.user.email,
        )


class LeagueDTO(BaseModel):
    model_config = _DTO_CONFIG

    id: str = Field(alias="i")
    name: str = Field(alias="n")
    creator_id: str = Field(alias="ci", default="")
    budget: Decimal | None = Field(alias="b", default=None)

    def to_domain(self) -> League:
        return League(
            id=self.id,
            name=self.name,
            creator_id=self.creator_id,
            budget=self.budget,
        )


class LeagueSelectionDTO(BaseModel):
    model_config = _DTO_CONFIG

    it: list[LeagueDTO] = Field(default_factory=list)


class PlayerDTO(BaseModel):
    """Spieler-Wire-Format (in Squad, Market und Kader-Views).

    Die Feldnamen sind Kickbase-Kurzformen — hier zentral gemappt.
    """

    model_config = _DTO_CONFIG

    id: str = Field(alias="i")
    first_name: str = Field(alias="fn", default="")
    last_name: str = Field(alias="n")
    team_id: str = Field(alias="tid", default="")
    position: int = Field(alias="pos", default=0)
    status: int = Field(alias="st", default=0)
    market_value: Decimal = Field(alias="mv", default=Decimal(0))
    average_points: float = Field(alias="ap", default=0.0)
    total_points: int = Field(alias="tp", default=0)

    def to_domain(self) -> Player:
        pos = Position(self.position) if self.position in _POSITION_VALUES else Position.MIDFIELDER
        status = PlayerStatus(self.status) if self.status in _STATUS_VALUES else PlayerStatus.FIT
        return Player(
            id=self.id,
            first_name=self.first_name,
            last_name=self.last_name,
            team_id=self.team_id,
            position=pos,
            status=status,
            market_value=self.market_value,
            average_points=self.average_points,
            total_points=self.total_points,
        )


class SquadPlayerDTO(PlayerDTO):
    buy_price: Decimal = Field(alias="p", default=Decimal(0))

    def to_squad_player(self) -> SquadPlayer:
        return SquadPlayer(player=self.to_domain(), buy_price=self.buy_price)


class SquadResponseDTO(BaseModel):
    model_config = _DTO_CONFIG

    it: list[SquadPlayerDTO] = Field(default_factory=list)
    team_value: Decimal = Field(alias="tv", default=Decimal(0))
    budget: Decimal = Field(alias="b", default=Decimal(0))

    def to_domain(self, league_id: str, manager_id: str) -> Squad:
        return Squad(
            league_id=league_id,
            manager_id=manager_id,
            players=tuple(sp.to_squad_player() for sp in self.it),
            team_value=self.team_value,
            budget=self.budget,
        )


class MarketOfferDTO(BaseModel):
    model_config = _DTO_CONFIG

    id: str = Field(alias="i")
    user_id: str = Field(alias="uid", default="")
    user_name: str = Field(alias="un", default="")
    price: Decimal = Field(alias="p", default=Decimal(0))
    valid_until: datetime | None = Field(alias="exs", default=None)

    def to_domain(self) -> MarketOffer:
        return MarketOffer(
            id=self.id,
            user_id=self.user_id,
            user_name=self.user_name,
            price=self.price,
            valid_until=self.valid_until,
        )


class MarketPlayerDTO(PlayerDTO):
    price: Decimal = Field(alias="prc", default=Decimal(0))
    expires_at: datetime | None = Field(alias="exs", default=None)
    seller_id: str | None = Field(alias="u", default=None)
    offers: list[MarketOfferDTO] = Field(alias="ofs", default_factory=list)

    def to_market_player(self) -> MarketPlayer:
        return MarketPlayer(
            player=self.to_domain(),
            price=self.price,
            expires_at=self.expires_at,
            seller_id=self.seller_id,
            offers=tuple(o.to_domain() for o in self.offers),
        )


class MarketResponseDTO(BaseModel):
    model_config = _DTO_CONFIG

    it: list[MarketPlayerDTO] = Field(default_factory=list)


class MatchdayDTO(BaseModel):
    model_config = _DTO_CONFIG

    number: int = Field(alias="day")
    starts_at: datetime = Field(alias="dt")
    ends_at: datetime = Field(alias="dtl", default=None)  # type: ignore[assignment]
    is_current: bool = Field(alias="cur", default=False)

    def to_domain(self) -> Matchday:
        end = self.ends_at or self.starts_at
        return Matchday(
            number=self.number,
            starts_at=self.starts_at,
            ends_at=end,
            is_current=self.is_current,
        )


class MatchdaysResponseDTO(BaseModel):
    model_config = _DTO_CONFIG

    it: list[MatchdayDTO] = Field(default_factory=list)


class MarketValuePointDTO(BaseModel):
    model_config = _DTO_CONFIG

    day: datetime = Field(alias="dt")
    value: Decimal = Field(alias="mv")

    def to_domain(self) -> MarketValuePoint:
        return MarketValuePoint(day=self.day, value=self.value)


class MarketValueResponseDTO(BaseModel):
    model_config = _DTO_CONFIG

    it: list[MarketValuePointDTO] = Field(default_factory=list)


class BidResponseDTO(BaseModel):
    model_config = _DTO_CONFIG

    id: str = Field(alias="i", default="")
