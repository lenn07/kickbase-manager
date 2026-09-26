"""Lädt Tabelle + Spielplan und leitet daraus die Gegnerstärke ab (P2-11).

Der Master-Prompt fordert in §1.2 seit der ersten Fassung, Käufe und
Aufstellung am Spielplan auszurichten — und musste bis P2-11 im selben Absatz
einräumen, dass die Daten fehlen. Dieses Modul beschafft sie:

- `GET /v4/competitions/1/table` → Tabelle (18 Zeilen)
- `GET /v4/competitions/1/matchdays` → alle 306 Paarungen der Saison

Zwei HTTP-Calls für **alle** Spieler, Kader wie Markt: der Spielplan gilt pro
Verein, nicht pro Spieler. Der Optimizing-Plan hatte `mdsum[]` aus
`GET /leagues/{l}/players/{p}` vorgesehen — dieselbe Information, aber ein Call
pro Spieler, also 25 bis 35 statt zwei (Plan-Korrektur in §6/P2-11).

**Haltbarkeit.** Tabelle und Spielplan ändern sich nur durch gespielte Spiele.
Gültig ist der Eintrag deshalb bis zum nächsten Anpfiff — mit einem Deckel von
24 Stunden, weil die DFL Termine noch verlegt, während die Tabelle steht. Läuft
gerade ein Spieltag, liegt der nächste Anpfiff in der Vergangenheit; dann greift
kein Cache und jeder Tick sieht die Live-Tabelle. Dieselbe Mechanik wie beim
Spieltags-Cache aus P1-8.

**Teilausfall ist erlaubt.** Fällt nur die Tabelle aus, bleiben Gegner und
Heimrecht bekannt und `fdr` ist `None` — das ist mehr als nichts und ehrlicher
als eine geratene 3. Gecacht wird ein solcher Lauf aber nicht: ein halber
Kontext würde die fehlende Hälfte 24 Stunden lang festschreiben.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.domain.exceptions import KickbaseError
from app.domain.fixtures import TeamOutlook, build_team_outlooks
from app.domain.gateways import CompetitionContextCache, KickbaseGateway
from app.domain.models import CompetitionContext, Fixture, TeamStanding

_log = logging.getLogger(__name__)

# Obergrenze der Cache-Haltbarkeit. Zwischen zwei Spieltagen liegen in der
# Länderspielpause drei Wochen — die Tabelle steht dann still, der Spielplan
# nicht: Terminverlegungen werden mit vier bis sechs Wochen Vorlauf bekannt
# gegeben. Ein Tag ist der Kompromiss aus „ein Call pro Tag" und „nie länger als
# einen Tag falsch".
_MAX_CACHE_AGE = timedelta(hours=24)

DEFAULT_COMPETITION_ID = "1"


class TeamContextProvider:
    """Beschafft `TeamOutlook` je Verein — gecacht, ausfalltolerant."""

    def __init__(
        self,
        kickbase: KickbaseGateway,
        *,
        cache: CompetitionContextCache | None = None,
        competition_id: str = DEFAULT_COMPETITION_ID,
    ) -> None:
        self._kickbase = kickbase
        # Ohne Cache holt jeder Tick beide Antworten neu — der Zustand in
        # Tests, die den Cache nicht interessiert, nicht ein stiller Default.
        self._cache = cache
        self._competition_id = competition_id

    async def load(
        self,
        *,
        next_matchday_start: datetime | None = None,
        now: datetime | None = None,
    ) -> dict[str, TeamOutlook]:
        """Gegner, Heimrecht und Schwierigkeit je Verein.

        Leeres Mapping heißt „keine Spielplan-Information" — der Payload setzt
        dann pro Spieler `missing_data:fixtures`, und der Prompt entscheidet
        ohne. Ein Ausfall dieser beiden Calls darf einen Tick nicht kosten: die
        Regel-Compliance (Konto, Elf) hängt nicht am Spielplan.
        """
        now = now or datetime.now(UTC)
        context = self._read_cache(now=now)
        if context is None:
            context, complete = await self._fetch(now=now)
            if complete:
                self._write_cache(context, next_matchday_start=next_matchday_start, now=now)
        if not context.fixtures:
            return {}
        return build_team_outlooks(context.standings, context.fixtures, now=now)

    async def _fetch(self, *, now: datetime) -> tuple[CompetitionContext, bool]:
        """Beide Quellen parallel. Zweiter Rückgabewert: waren beide erfolgreich?"""
        del now  # Der Zeitpunkt zählt erst beim Cache-Schreiben.
        standings, fixtures = await asyncio.gather(
            self._fetch_standings(),
            self._fetch_fixtures(),
        )
        complete = standings is not None and fixtures is not None
        context = CompetitionContext(
            standings=tuple(standings or ()),
            fixtures=tuple(fixtures or ()),
        )
        _log.info(
            "Spielplan-Kontext geladen: %d Tabellenzeilen, %d Paarungen.",
            len(context.standings),
            len(context.fixtures),
        )
        return context, complete

    async def _fetch_standings(self) -> list[TeamStanding] | None:
        try:
            return await self._kickbase.get_competition_table(self._competition_id)
        except KickbaseError as exc:
            _log.info("Tabelle nicht verfügbar (%s) — Gegnerstärke bleibt unbekannt.", exc)
            return None

    async def _fetch_fixtures(self) -> list[Fixture] | None:
        try:
            return await self._kickbase.list_fixtures(self._competition_id)
        except KickbaseError as exc:
            _log.info("Spielplan nicht verfügbar (%s) — Prompt läuft ohne Gegner.", exc)
            return None

    def _read_cache(self, *, now: datetime) -> CompetitionContext | None:
        if self._cache is None:
            return None
        return self._cache.get(self._competition_id, now=now)

    def _write_cache(
        self,
        context: CompetitionContext,
        *,
        next_matchday_start: datetime | None,
        now: datetime,
    ) -> None:
        if self._cache is None:
            return
        valid_until = _valid_until(now=now, next_matchday_start=next_matchday_start)
        if valid_until <= now:
            # Es läuft ein Spieltag: die Tabelle bewegt sich gerade, jeder Tick
            # soll sie frisch sehen.
            return
        self._cache.put(self._competition_id, context, valid_until=valid_until)


def _valid_until(*, now: datetime, next_matchday_start: datetime | None) -> datetime:
    ceiling = now + _MAX_CACHE_AGE
    if next_matchday_start is None:
        return ceiling
    return min(next_matchday_start, ceiling)


__all__ = ["DEFAULT_COMPETITION_ID", "TeamContextProvider"]
