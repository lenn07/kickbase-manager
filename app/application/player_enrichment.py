"""Reichert Kader- und Markt-Spieler um Zusatz-Signale für den AI-Only-Modus an.

Der Master-Prompt erwartet pro Spieler:
- `market_trend_7d_pct`  — kann exakt aus Kickbase-Historie berechnet werden.
- `avg_points_last5`     — Kickbase liefert keinen offiziellen Endpoint dafür.
  Für v1 nutzen wir `Player.average_points` (Saison-Ø) als Proxy und markieren
  die Ungenauigkeit über `missing_data`-Flags im USER-JSON.
- `start_probability_next` — externe Startelf-Prognose (kicker & Co.) ist
  außerhalb des Scope; wir liefern eine heuristische Approximation aus dem
  Kickbase-`PlayerStatus` und markieren sie ebenfalls per `missing_data`.

Marktwert-Historie kostet einen HTTP-Call pro Spieler. Um das Ban-Risiko klein
zu halten, laden wir Historien nur für:
- alle Squad-Spieler (überschaubar, ~15),
- die teuersten N Markt-Spieler (Default 10).
Für alle übrigen Markt-Spieler bleibt `market_trend_7d_pct` `None`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.exceptions import KickbaseError
from app.domain.gateways import KickbaseGateway
from app.domain.models import MarketPlayer, Player, PlayerStatus, Squad

_log = logging.getLogger(__name__)


_INJURY_STATUS_LABELS: dict[PlayerStatus, str] = {
    PlayerStatus.FIT: "fit",
    PlayerStatus.INJURED: "injured",
    PlayerStatus.UNKNOWN_2: "questionable",
    PlayerStatus.OUT_OF_SQUAD: "out_of_squad",
    PlayerStatus.REHAB: "rehab",
    PlayerStatus.RED_CARD: "suspended_red",
    PlayerStatus.YELLOW_RED_CARD: "suspended_yellow_red",
    PlayerStatus.NOT_IN_TEAM: "not_in_team",
}


_START_PROBABILITY_BY_STATUS: dict[PlayerStatus, float] = {
    PlayerStatus.FIT: 0.85,
    PlayerStatus.UNKNOWN_2: 0.55,
    PlayerStatus.INJURED: 0.05,
    PlayerStatus.REHAB: 0.10,
    PlayerStatus.OUT_OF_SQUAD: 0.15,
    PlayerStatus.RED_CARD: 0.0,
    PlayerStatus.YELLOW_RED_CARD: 0.0,
    PlayerStatus.NOT_IN_TEAM: 0.0,
}


@dataclass(frozen=True, slots=True)
class PlayerEnrichment:
    """Zusatzsignale, die der Master-Prompt pro Spieler erwartet."""

    player_id: str
    market_trend_7d_pct: float | None
    avg_points_last5: float | None
    start_probability_next: float
    injury_status: str
    missing_data_flags: tuple[str, ...] = field(default_factory=tuple)


class PlayerEnricher:
    """Sammelt die Zusatzsignale aus Kickbase-Endpoints + Domain-Heuristiken."""

    def __init__(
        self,
        kickbase: KickbaseGateway,
        *,
        max_market_history: int = 10,
        history_days: int = 7,
    ) -> None:
        self._kickbase = kickbase
        self._max_market_history = max_market_history
        self._history_days = history_days

    async def enrich(
        self,
        league_id: str,
        squad: Squad,
        market: Sequence[MarketPlayer],
    ) -> dict[str, PlayerEnrichment]:
        squad_ids = [sp.player.id for sp in squad.players]
        market_by_value = sorted(market, key=lambda m: m.player.market_value, reverse=True)
        top_market_ids = [mp.player.id for mp in market_by_value[: self._max_market_history]]
        history_targets = list(dict.fromkeys(squad_ids + top_market_ids))

        trends = await self._fetch_trends(league_id, history_targets)

        players: dict[str, Player] = {sp.player.id: sp.player for sp in squad.players}
        for mp in market:
            players.setdefault(mp.player.id, mp.player)

        history_set = set(history_targets)
        result: dict[str, PlayerEnrichment] = {}
        for pid, player in players.items():
            trend = trends.get(pid)
            trend_missing = pid not in history_set or trend is None
            avg5, avg5_flag = _avg_points_proxy(player)
            start_prob = _START_PROBABILITY_BY_STATUS.get(player.status, 0.5)
            flags: list[str] = []
            if trend_missing:
                flags.append("missing_data:market_trend_7d_pct")
            if avg5_flag:
                flags.append("missing_data:avg_points_last5_using_season_avg")
            # start_probability_next ist immer eine grobe Heuristik — flag setzen.
            flags.append("missing_data:start_probability_next_heuristic")
            result[pid] = PlayerEnrichment(
                player_id=pid,
                market_trend_7d_pct=trend,
                avg_points_last5=avg5,
                start_probability_next=start_prob,
                injury_status=_INJURY_STATUS_LABELS.get(player.status, "unknown"),
                missing_data_flags=tuple(flags),
            )
        return result

    async def _fetch_trends(self, league_id: str, player_ids: Iterable[str]) -> dict[str, float]:
        ids = list(player_ids)
        if not ids:
            return {}
        results = await asyncio.gather(
            *(self._fetch_single_trend(league_id, pid) for pid in ids),
            return_exceptions=False,
        )
        return {pid: pct for pid, pct in zip(ids, results, strict=True) if pct is not None}

    async def _fetch_single_trend(self, league_id: str, player_id: str) -> float | None:
        try:
            history = await self._kickbase.get_market_value_history(
                league_id, player_id, days=self._history_days
            )
        except KickbaseError as exc:
            _log.info(
                "Marktwert-Historie für %s nicht ladbar (%s) — Trend bleibt None.",
                player_id,
                exc,
            )
            return None
        if len(history) < 2:  # noqa: PLR2004 — Minimum für Delta-Berechnung
            return None
        # Historie ist chronologisch aufsteigend (ältester zuerst) — Kickbase
        # liefert die letzten N Tage. Wir nutzen ersten vs. letzten Punkt.
        first = history[0].value
        last = history[-1].value
        if first <= Decimal(0):
            return None
        delta_pct = float((last - first) / first * Decimal(100))
        return round(delta_pct, 2)


def _avg_points_proxy(player: Player) -> tuple[float | None, bool]:
    """Fallback für `avg_points_last5`: nutzt Saison-Ø.

    Rückgabe: (Wert, hat_fallback_flag). `hat_fallback_flag=True` heißt, der
    Wert ist kein echter last-5-Wert, sondern eine gröbere Approximation —
    der USER-JSON-Builder markiert das in `risk_flags`.
    """
    if player.average_points > 0:
        return round(float(player.average_points), 2), True
    return None, True


__all__ = ["PlayerEnricher", "PlayerEnrichment"]
