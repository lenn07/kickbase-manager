"""Async-HTTP-Adapter für OpenLigaDB — implementiert `ExternalDataGateway`.

Der Client zieht einmalig die Saison-Match-Liste per `/getmatchdata/bl1/{season}`,
cached sie im Prozess (TTL) und berechnet daraus pro Team ein Form-Signal
in [0, 1]. Kein Auth, kein Rate-Limit auf OpenLigaDB-Seite bekannt — der
TTL-Cache dient primär dem Pi-Ressourcen-Schutz.

Signal-Definition (v1, bewusst simpel):
- Punkte aus den letzten N (Default 5) beendeten Bundesliga-Spielen des Teams,
  gewichtet nach Sieg=3 / Remis=1 / Niederlage=0.
- Normalisiert auf [0, 1] gegen das Maximum (3·N Punkte).

Neutralwert 0.5 für Teams, die (a) unbekannt in unserer Alias-Map sind oder
(b) noch zu wenige Spiele in der aktuellen Saison bestritten haben.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping
from http import HTTPStatus

import httpx
from pydantic import TypeAdapter, ValidationError

from app.domain.exceptions import TransportError
from app.infrastructure.openligadb.config import OpenLigaDBConfig
from app.infrastructure.openligadb.dto import OpenLigaMatchDTO
from app.infrastructure.openligadb.team_map import build_team_index, match_opendb_team_name

_log = logging.getLogger(__name__)

_NEUTRAL_SIGNAL = 0.5
_WIN_POINTS = 3
_DRAW_POINTS = 1

_MATCH_LIST_ADAPTER: TypeAdapter[list[OpenLigaMatchDTO]] = TypeAdapter(list[OpenLigaMatchDTO])


class HttpxOpenLigaDBClient:
    """Konkrete `ExternalDataGateway`-Implementierung auf httpx-Basis.

    - Ein Request pro TTL-Fenster (Default 6 h): `/getmatchdata/bl1/{season}`.
    - Verarbeitet die Antwort in ~kB — kein Datenbank-Backing nötig.
    - Fehler werden geloggt, aber nicht propagiert: `get_team_signals`
      liefert dann ein leeres Mapping, der Aufrufer nimmt neutral 0.5 an.
    """

    def __init__(
        self,
        config: OpenLigaDBConfig | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config or OpenLigaDBConfig()
        self._http = httpx.AsyncClient(
            base_url=self._config.base_url,
            timeout=self._config.request_timeout_s,
            headers={"User-Agent": self._config.user_agent, "Accept": "application/json"},
            transport=transport,
        )
        self._cached_matches: list[OpenLigaMatchDTO] | None = None
        self._cached_at: float = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> HttpxOpenLigaDBClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def get_team_signals(self, team_ids: Iterable[str]) -> Mapping[str, float]:
        wanted = [tid for tid in dict.fromkeys(team_ids) if tid]
        if not wanted:
            return {}
        try:
            matches = await self._load_matches()
        except TransportError as exc:
            _log.info("OpenLigaDB nicht erreichbar (%s) — externe Signale fallen aus.", exc)
            return {}
        if not matches:
            return {}
        index = build_team_index(wanted)
        if not index:
            return {}
        points_by_team_id = _compute_recent_form_points(
            matches, index, window=self._config.form_window_matches
        )
        max_points = _WIN_POINTS * self._config.form_window_matches
        signals: dict[str, float] = {}
        for kb_id, (points, games) in points_by_team_id.items():
            if games <= 0:
                continue
            # Wenn ein Team noch nicht die volle Fenster-Breite an Spielen
            # bestritten hat (Saisonstart), skalieren wir das Maximum auf die
            # tatsächliche Spielzahl — damit sind 2 Siege aus 2 Spielen fair
            # gleich stark wie 6 Punkte aus 5 in ihrer Signal-Aussage.
            effective_max = _WIN_POINTS * min(games, self._config.form_window_matches)
            signal = points / effective_max if effective_max else _NEUTRAL_SIGNAL
            signals[kb_id] = _clip01(signal)
        # Für angefragte, aber gar nicht in OpenLigaDB gefundene Teams (z. B.
        # unbekannte Alias): Aufrufer nimmt Neutralwert selbst an — wir lassen
        # den Key bewusst weg, damit „unbekannt" von „0 Punkte" unterscheidbar bleibt.
        _ = max_points  # reserviert für spätere Trend-Metrik
        return signals

    async def _load_matches(self) -> list[OpenLigaMatchDTO]:
        now = time.monotonic()
        if self._cached_matches is not None and (now - self._cached_at) < self._config.cache_ttl_s:
            return self._cached_matches
        path = f"/getmatchdata/{self._config.league_shortcut}/{self._config.season}"
        try:
            response = await self._http.get(path)
        except httpx.RequestError as exc:
            raise TransportError(f"OpenLigaDB Netzwerkfehler bei {path}: {exc}") from exc
        if response.status_code >= HTTPStatus.BAD_REQUEST:
            raise TransportError(f"OpenLigaDB {path} → HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise TransportError(f"OpenLigaDB {path}: ungültiges JSON: {exc}") from exc
        if not isinstance(payload, list):
            raise TransportError(
                f"OpenLigaDB {path}: erwartete Liste, bekam {type(payload).__name__}"
            )
        try:
            matches = _MATCH_LIST_ADAPTER.validate_python(payload)
        except ValidationError as exc:
            raise TransportError(f"OpenLigaDB {path}: ungültiges Schema: {exc}") from exc
        self._cached_matches = matches
        self._cached_at = now
        return matches


def _compute_recent_form_points(
    matches: list[OpenLigaMatchDTO],
    index: dict[str, str],
    *,
    window: int,
) -> dict[str, tuple[int, int]]:
    """Aggregiert (Punkte, Spielanzahl) pro Kickbase-team_id über die letzten `window` Spiele.

    Iteriert die Match-Liste nach Spieltag absteigend — das jüngste Match jedes
    Teams fließt zuerst ein. Ergebnis: pro team_id ein Tupel (gesammelte
    Punkte, tatsächliche Spielanzahl). Nicht beendete Spiele werden ignoriert.
    """
    finished = [m for m in matches if m.match_is_finished]
    finished.sort(key=lambda m: m.group.group_order_id, reverse=True)

    points: dict[str, int] = {}
    games: dict[str, int] = {}

    for match in finished:
        result = match.final_result()
        if result is None:
            continue
        kb1 = match_opendb_team_name(match.team1.team_name, index) or match_opendb_team_name(
            match.team1.short_name, index
        )
        kb2 = match_opendb_team_name(match.team2.team_name, index) or match_opendb_team_name(
            match.team2.short_name, index
        )
        for kb_id, own, opp in (
            (kb1, result.points_team1, result.points_team2),
            (kb2, result.points_team2, result.points_team1),
        ):
            if kb_id is None or games.get(kb_id, 0) >= window:
                continue
            if own > opp:
                pts = _WIN_POINTS
            elif own == opp:
                pts = _DRAW_POINTS
            else:
                pts = 0
            points[kb_id] = points.get(kb_id, 0) + pts
            games[kb_id] = games.get(kb_id, 0) + 1

    return {kb_id: (points.get(kb_id, 0), games.get(kb_id, 0)) for kb_id in games}


def _clip01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
