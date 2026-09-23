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
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.domain.models import (
    League,
    LeagueMe,
    MarketPlayer,
    MarketSnapshot,
    MarketValuePoint,
    Matchday,
    Player,
    PlayerDetail,
    PlayerStatus,
    Position,
    Session,
    Squad,
    SquadPlayer,
)

_log = logging.getLogger(__name__)

_DTO_CONFIG = ConfigDict(populate_by_name=True, extra="ignore")

_POSITION_VALUES = frozenset(p.value for p in Position)
# `UNKNOWN` ist ein Domain-Zustand, kein Wire-Wert — sonst würde ein `st: -1`
# aus der API stillschweigend akzeptiert.
_STATUS_VALUES = frozenset(s.value for s in PlayerStatus if s is not PlayerStatus.UNKNOWN)


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
    """Mappt `st` auf den Domain-Status — Unbekanntes auf `UNKNOWN`, nie auf `FIT`.

    Die `st`-Liste ist nicht nachweislich vollständig (Plan §8/F3: real gesehen
    nur 0/2/4, die Doku nennt zusätzlich 128), und die Frage ist durch Sammeln
    nicht abschließbar. Vorher landete jeder unbekannte Wert auf `FIT` — ein
    gesperrter Spieler sah damit spielbereit aus, wurde aufgestellt und kostete
    100 Punkte (Defekt D6). `UNKNOWN` ist ehrlich: der Enricher hängt ein
    `missing_data`-Flag dran, statt eine Startelf-Chance zu erfinden.
    """
    if raw in _STATUS_VALUES:
        return PlayerStatus(raw)
    _log.warning(
        "Unbekannter Kickbase-Spielerstatus st=%r — als UNKNOWN behandelt. "
        "Wert in `PlayerStatus` ergänzen, sobald die Bedeutung geklärt ist (Plan §8/F3).",
        raw,
    )
    return PlayerStatus.UNKNOWN


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
    # None statt 0.0: „kein Einsatz" und „0 Punkte erzielt" sind verschiedene
    # Aussagen, und nur eine davon darf eine Kaufentscheidung tragen.
    average_points: float | None = Field(default=None, validation_alias="ap")
    total_points: int | None = Field(default=None, validation_alias="p")
    # `lo` = Lineup-Order (0..10 = Startelf-Slot laut Kickbase-App).
    lineup_order: int | None = Field(default=None, validation_alias="lo")

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
        return SquadPlayer(player=player, lineup_order=self.lineup_order)


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
    """Spieler-Wire-Format im Markt (Struktur unterscheidet sich von Squad).

    `extra="allow"` statt `ignore` — bewusst, und nur hier: das Gebots-Array
    liegt irgendwo in diesem Objekt, und sein Name ist unbekannt, weil er erst
    bei `ofc > 0` auftaucht (Plan §8/F1). Mit `ignore` würde pydantic es beim
    ersten echten Gebot still verschlucken; mit `allow` steht es in
    `model_extra` und `unknown_fields()` kann es melden. Der Fund kostet damit
    keinen glücklich getimten manuellen Skriptlauf mehr.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

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
    # `u` war früher die Seller-ID als String; seit einem API-Update kann es
    # auch ein User-Objekt sein (z. B. {"i": "...", "n": "...", "vft": 0, "st": 0}).
    seller_id: str | None = Field(default=None, validation_alias="u")
    # `ofc` = Anzahl der Gebote auf dieses Listing.
    offer_count: int = Field(default=0, validation_alias="ofc")
    # Leistungsdaten. Der Bot hat sie bis P0-3 hart auf 0 gesetzt und damit
    # jeden Marktspieler ohne Datengrundlage bewertet (Defekt D4). 4 von 21
    # Items der Cassette tragen die Felder nicht — dort bleibt es None.
    average_points: float | None = Field(default=None, validation_alias="ap")
    total_points: int | None = Field(default=None, validation_alias="p")
    # `prob` = Startelf-Stufe 1..5, nur in der Spieltagswoche vorhanden (F2).
    start_probability_raw: int | None = Field(default=None, validation_alias="prob")
    # `isn` = neu am Markt, `dt` = Listing-Zeitpunkt.
    is_new: bool = Field(default=False, validation_alias="isn")
    listed_at: datetime | None = Field(default=None, validation_alias="dt")

    @field_validator("seller_id", mode="before")
    @classmethod
    def _extract_seller_id(cls, v: object) -> str | None:
        if isinstance(v, dict):
            raw = v.get("i") or v.get("id")
            return str(raw) if raw is not None else None
        return v  # type: ignore[return-value]

    def to_market_player(self) -> MarketPlayer:
        # Keine Uhr hier: `exs` wird roh durchgereicht (siehe `MarketPlayer`).
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
        return MarketPlayer(
            player=player,
            price=self.price,
            expires_in_s=self.expires_in_s,
            seller_id=self.seller_id,
            offer_count=self.offer_count,
            start_probability_raw=self.start_probability_raw,
            is_new=self.is_new,
            listed_at=self.listed_at,
            # Leer, bis der Feldname gegen ein echtes Gebot verifiziert ist
            # (Plan §8/F1). `offer_count` trägt die Information bis dahin.
            offers=(),
        )

    def unknown_fields(self) -> dict[str, Any]:
        """Felder, die dieses DTO (noch) nicht kennt — Rohwerte inklusive.

        Einziger Zweck: den Namen des Gebots-Arrays festhalten, sobald Kickbase
        es erstmals mitschickt. Wer das Ergebnis liest, sieht Name **und**
        Struktur und kann F1 beantworten, ohne auf ein offenes Gebot zu warten.
        """
        return dict(self.model_extra or {})


class MarketResponseDTO(BaseModel):
    """Market-Response inkl. **Root-Feldern** (`tv`, `mvud`, `dt`, `day`, `nps`, `sn`).

    Die Root-Felder sind der eigentliche Fund aus Phase 0: der Mannschaftswert
    steht hier, nicht in `/squad`, und der Spieltagsstart auch — `dt` spart den
    separaten `list_matchdays()`-Call.
    """

    model_config = _DTO_CONFIG

    it: list[MarketPlayerDTO] = Field(default_factory=list)
    team_value: Decimal = Field(default=Decimal(0), validation_alias="tv")
    # `mvud` = *nächster* Marktwert-Update-Zeitpunkt (20:00 UTC = 22:00 Berlin).
    mv_update_at: datetime | None = Field(default=None, validation_alias="mvud")
    # `dt` auf Root-Ebene = Start des nächsten Spieltags (nicht zu verwechseln
    # mit `dt` im Item, das dort den Listing-Zeitpunkt meint).
    next_matchday_start: datetime | None = Field(default=None, validation_alias="dt")
    matchday: int = Field(default=0, validation_alias="day")
    squad_size: int = Field(default=0, validation_alias="nps")
    season: str = Field(default="", validation_alias="sn")

    def to_domain(self) -> MarketSnapshot:
        return MarketSnapshot(
            players=tuple(m.to_market_player() for m in self.it),
            team_value=self.team_value,
            mv_update_at=self.mv_update_at,
            next_matchday_start=self.next_matchday_start,
            matchday=self.matchday,
            squad_size=self.squad_size,
            season=self.season,
        )


class PlayerDetailDTO(BaseModel):
    """`GET /v4/leagues/{l}/players/{p}` — nur der Teil, den P0-3 braucht."""

    model_config = _DTO_CONFIG

    id: str = Field(default="", validation_alias="i")
    # `sl` = Startelf-Prognose als bool, `plpt` nennt die Quelle
    # (aktuell „Ligainsider"). Ganzjährig verfügbar, anders als `prob`.
    is_predicted_starter: bool | None = Field(default=None, validation_alias="sl")
    prediction_source: str = Field(default="", validation_alias="plpt")

    def to_domain(self, player_id: str) -> PlayerDetail:
        return PlayerDetail(
            player_id=self.id or player_id,
            is_predicted_starter=self.is_predicted_starter,
            prediction_source=self.prediction_source,
        )


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

    @field_validator("day", mode="before")
    @classmethod
    def _coerce_day(cls, v: object) -> object:
        # Kickbase liefert `dt` als Tage-seit-Epoch (int), z. B. 20717 = 2026-09-22.
        # Pydantic würde einen int sonst als Unix-Sekunden interpretieren.
        if isinstance(v, bool):
            return v
        if isinstance(v, int):
            return datetime.fromtimestamp(v * 86400, tz=UTC)
        return v

    def to_domain(self) -> MarketValuePoint:
        return MarketValuePoint(day=self.day, value=self.value)


class MarketValueResponseDTO(BaseModel):
    model_config = _DTO_CONFIG

    it: list[MarketValuePointDTO] = Field(default_factory=list)


# ---------- Bidding ----------


class BidResponseDTO(BaseModel):
    model_config = _DTO_CONFIG

    id: str = Field(default="", validation_alias="i")
