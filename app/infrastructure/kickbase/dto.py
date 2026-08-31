"""Wire-Format-DTOs für Kickbase v4.

Kickbase verwendet **unterschiedliche** Kurzformen je nach Endpoint:
- Squad-Spieler: `pi`, `pn`, `pos`, `p`, `ap`, `mv`, ...
- Market-Spieler: `i`, `fn`, `n`, `pos`, `mv`, `prc`, `exs`, ...
- User (im Login): lange Namen `id`, `email`, `name`
- Login-Root: `tkn`, `tknex`, `u`

Diese DTOs bilden das jeweilige Wire-Format 1:1 ab und mappen dann auf
Domain-Modelle — der Rest der App sieht die Kurzform nie.
"""

from __future__ import annotations

import base64
import contextlib
import json as _json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.domain.models import (
    League,
    LeagueMe,
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


# ---------- Auth / User ----------


class UserDTO(BaseModel):
    model_config = _DTO_CONFIG

    id: str = Field(default="", validation_alias=AliasChoices("id", "i"))
    email: str = Field(default="", validation_alias=AliasChoices("email", "em"))
    name: str = Field(default="", validation_alias=AliasChoices("name", "n"))


class LoginResponseDTO(BaseModel):
    model_config = _DTO_CONFIG

    token: str = Field(validation_alias=AliasChoices("tkn", "token"))
    # v4 liefert Token-Ablauf direkt als ISO-String in `tknex`.
    token_exp: datetime | None = Field(
        default=None, validation_alias=AliasChoices("tknex", "tokenExp")
    )
    user: UserDTO = Field(default_factory=UserDTO, validation_alias=AliasChoices("u", "user"))

    def to_session(self) -> Session:
        expires = (
            self.token_exp or _extract_jwt_exp(self.token) or datetime.now(UTC) + timedelta(days=7)
        )
        return Session(
            token=self.token,
            token_expires_at=expires,
            user_id=self.user.id,
            email=self.user.email,
        )


def _extract_jwt_exp(token: str) -> datetime | None:
    parts = token.split(".")
    if len(parts) != 3:  # noqa: PLR2004 — JWT-Struktur
        return None
    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    with contextlib.suppress(ValueError, _json.JSONDecodeError, UnicodeDecodeError):
        payload = _json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        exp = payload.get("exp")
        if isinstance(exp, int | float):
            return datetime.fromtimestamp(exp, tz=UTC)
    return None


# ---------- Leagues ----------


class LeagueDTO(BaseModel):
    model_config = _DTO_CONFIG

    id: str = Field(validation_alias=AliasChoices("i", "id"))
    name: str = Field(validation_alias=AliasChoices("n", "name"))
    competition_id: str = Field(default="1", validation_alias=AliasChoices("cpi", "cp"))
    # `b` = Budget, `tv` = Team-Value; nur in /leagues/selection direkt vorhanden.
    budget: Decimal | None = Field(default=None, validation_alias="b")

    def to_domain(self) -> League:
        return League(id=self.id, name=self.name, creator_id="", budget=self.budget)


class LeagueSelectionDTO(BaseModel):
    model_config = _DTO_CONFIG

    it: list[LeagueDTO] = Field(default_factory=list)


class LeagueMeDTO(BaseModel):
    model_config = _DTO_CONFIG

    budget: Decimal = Field(default=Decimal(0), validation_alias="b")
    unread_notifications: int = Field(default=0, validation_alias=AliasChoices("un", "unm"))
    is_admin: bool = Field(default=False, validation_alias="adm")

    def to_domain(self, league_id: str) -> LeagueMe:
        return LeagueMe(
            league_id=league_id,
            budget=self.budget,
            unread_notifications=self.unread_notifications,
            is_admin=self.is_admin,
        )


# ---------- Player-Basis + Squad ----------


def _to_position(raw: int) -> Position:
    return Position(raw) if raw in _POSITION_VALUES else Position.MIDFIELDER


def _to_status(raw: int) -> PlayerStatus:
    return PlayerStatus(raw) if raw in _STATUS_VALUES else PlayerStatus.FIT


class SquadPlayerDTO(BaseModel):
    """Spieler-Wire-Format innerhalb `/managers/{mid}/squad`."""

    model_config = _DTO_CONFIG

    id: str = Field(validation_alias="pi")
    last_name: str = Field(default="", validation_alias="pn")
    first_name: str = Field(default="", validation_alias="fn")
    team_id: str = Field(default="", validation_alias="tid")
    position: int = Field(default=0, validation_alias="pos")
    status: int = Field(default=0, validation_alias="st")
    market_value: Decimal = Field(default=Decimal(0), validation_alias="mv")
    average_points: float = Field(default=0.0, validation_alias="ap")
    total_points: int = Field(default=0, validation_alias="p")

    def to_squad_player(self) -> SquadPlayer:
        player = Player(
            id=self.id,
            first_name=self.first_name,
            last_name=self.last_name,
            team_id=self.team_id,
            position=_to_position(self.position),
            status=_to_status(self.status),
            market_value=self.market_value,
            average_points=self.average_points,
            total_points=self.total_points,
        )
        return SquadPlayer(player=player)


class SquadResponseDTO(BaseModel):
    model_config = _DTO_CONFIG

    it: list[SquadPlayerDTO] = Field(default_factory=list)
    manager_id: str = Field(default="", validation_alias="u")

    def to_domain(self, league_id: str, manager_id: str) -> Squad:
        return Squad(
            league_id=league_id,
            manager_id=manager_id or self.manager_id,
            players=tuple(sp.to_squad_player() for sp in self.it),
        )


# ---------- Market ----------


class MarketPlayerDTO(BaseModel):
    """Spieler-Wire-Format im Markt (Struktur unterscheidet sich von Squad)."""

    model_config = _DTO_CONFIG

    id: str = Field(validation_alias="i")
    first_name: str = Field(default="", validation_alias="fn")
    last_name: str = Field(default="", validation_alias="n")
    team_id: str = Field(default="", validation_alias="tid")
    position: int = Field(default=0, validation_alias="pos")
    status: int = Field(default=0, validation_alias="st")
    market_value: Decimal = Field(default=Decimal(0), validation_alias="mv")
    price: Decimal = Field(default=Decimal(0), validation_alias="prc")
    # `exs` = Sekunden bis Ablauf. Wir konvertieren in absolute Zeit.
    expires_in_s: int | None = Field(default=None, validation_alias="exs")
    seller_id: str | None = Field(default=None, validation_alias="u")

    def to_market_player(self) -> MarketPlayer:
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=self.expires_in_s)
            if self.expires_in_s is not None
            else None
        )
        player = Player(
            id=self.id,
            first_name=self.first_name,
            last_name=self.last_name,
            team_id=self.team_id,
            position=_to_position(self.position),
            status=_to_status(self.status),
            market_value=self.market_value,
            average_points=0.0,
            total_points=0,
        )
        return MarketPlayer(
            player=player,
            price=self.price,
            expires_at=expires_at,
            seller_id=self.seller_id,
            offers=(),
        )


class MarketResponseDTO(BaseModel):
    model_config = _DTO_CONFIG

    it: list[MarketPlayerDTO] = Field(default_factory=list)


# ---------- Matchdays ----------


class MatchDTO(BaseModel):
    model_config = _DTO_CONFIG

    id: str = Field(default="", validation_alias="mi")
    day: int = Field(default=0)
    starts_at: datetime = Field(validation_alias="dt")
    status: int = Field(default=0, validation_alias="st")


class MatchdayGroupDTO(BaseModel):
    model_config = _DTO_CONFIG

    day: int
    it: list[MatchDTO] = Field(default_factory=list)


class MatchdaysResponseDTO(BaseModel):
    model_config = _DTO_CONFIG

    it: list[MatchdayGroupDTO] = Field(default_factory=list)
    # `day` auf Root-Ebene = aktueller Spieltag.
    current_day: int = Field(default=0, validation_alias="day")

    def to_domain(self) -> list[Matchday]:
        result: list[Matchday] = []
        for group in self.it:
            if not group.it:
                continue
            starts = min(m.starts_at for m in group.it)
            # Ohne echtes Endzeit-Feld: letzter Kickoff + 2h (Halbzeitpause + Nachspielzeit).
            ends = max(m.starts_at for m in group.it) + timedelta(hours=2)
            result.append(
                Matchday(
                    number=group.day,
                    starts_at=starts,
                    ends_at=ends,
                    is_current=group.day == self.current_day,
                )
            )
        return result


# ---------- Market Value History ----------


class MarketValuePointDTO(BaseModel):
    model_config = _DTO_CONFIG

    day: datetime = Field(validation_alias=AliasChoices("dt", "d"))
    value: Decimal = Field(validation_alias=AliasChoices("mv", "v"))

    def to_domain(self) -> MarketValuePoint:
        return MarketValuePoint(day=self.day, value=self.value)


class MarketValueResponseDTO(BaseModel):
    model_config = _DTO_CONFIG

    it: list[MarketValuePointDTO] = Field(default_factory=list)


# ---------- Bidding ----------


class BidResponseDTO(BaseModel):
    model_config = _DTO_CONFIG

    id: str = Field(default="", validation_alias="i")
