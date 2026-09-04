"""Wire-Format-DTOs für die OpenLigaDB-REST-API.

OpenLigaDB liefert alle Spiele einer Saison als JSON-Liste; jeder Eintrag
hat Fixture-Daten (Teams, Spieltag) und eine `matchResults`-Liste, aus der
das Endergebnis (`resultTypeID == 2`) das relevante ist.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_DTO_CONFIG = ConfigDict(populate_by_name=True, extra="ignore")

_FINAL_RESULT_TYPE_ID = 2


class OpenLigaTeamDTO(BaseModel):
    model_config = _DTO_CONFIG

    team_id: int = Field(validation_alias="teamId")
    team_name: str = Field(validation_alias="teamName")
    short_name: str = Field(default="", validation_alias="shortName")


class OpenLigaResultDTO(BaseModel):
    model_config = _DTO_CONFIG

    result_type_id: int = Field(validation_alias="resultTypeID")
    points_team1: int = Field(default=0, validation_alias="pointsTeam1")
    points_team2: int = Field(default=0, validation_alias="pointsTeam2")


class OpenLigaGroupDTO(BaseModel):
    model_config = _DTO_CONFIG

    group_order_id: int = Field(default=0, validation_alias="groupOrderID")


class OpenLigaMatchDTO(BaseModel):
    model_config = _DTO_CONFIG

    match_id: int = Field(validation_alias="matchID")
    match_is_finished: bool = Field(default=False, validation_alias="matchIsFinished")
    team1: OpenLigaTeamDTO
    team2: OpenLigaTeamDTO
    match_results: list[OpenLigaResultDTO] = Field(
        default_factory=list, validation_alias="matchResults"
    )
    group: OpenLigaGroupDTO = Field(default_factory=OpenLigaGroupDTO)

    def final_result(self) -> OpenLigaResultDTO | None:
        for r in self.match_results:
            if r.result_type_id == _FINAL_RESULT_TYPE_ID:
                return r
        return None
