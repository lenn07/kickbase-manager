"""Reichert Kader- und Markt-Spieler um Zusatz-Signale für den AI-Only-Modus an.

Der Master-Prompt erwartet pro Spieler:
- `market_trend_{1,3,7,30}d_pct` + `mv_max_30d` — aus der Kickbase-Marktwert-
  Historie abgeleitet. Mehrere Zeitfenster gleichzeitig, damit die LLM
  Momentum und Beschleunigung erkennen kann (7 d steigend + 1 d fallend =
  Wendepunkt).
- `avg_points_last5`     — Kickbase liefert keinen offiziellen Endpoint dafür.
  Für v1 nutzen wir `Player.average_points` (Saison-Ø) als Proxy und markieren
  die Ungenauigkeit über `missing_data`-Flags im USER-JSON.
- `start_probability_next` — seit P0-3 eine **Quellen-Kette** statt einer
  Pauschale (Defekt D5). In dieser Reihenfolge:

  1. `prob` (Kickbase, 5 Stufen) — genau, aber nur in der Spieltagswoche da:
     am 23.09. in 0 von 21 Market-Items, am 31.08. in 22 von 22 (Plan §8/F2).
  2. `sl` (bool, Quelle „Ligainsider") aus `GET /players/{p}` — gröber, dafür
     ganzjährig. Kostet einen Request pro Spieler, siehe Kostendeckel unten.
  3. Verletzungsstatus-Heuristik — sagt nur etwas über *Verfügbarkeit*, nichts
     über Rotation: Ersatzkeeper und Kapitän sind beide „fit".
  4. `None` + `missing_data`-Flag. Nie ein erfundener Default — §9 des Plans.

  Welche Stufe gegriffen hat, steht als `start_probability_source` im
  USER-JSON: ohne Herkunft kann das Modell die Verlässlichkeit nicht gewichten
  und behandelt eine Statuspauschale wie eine echte Prognose.

**Kostendeckel (Plan §9, Ban-Risiko).** Beide Zusatzquellen kosten einen
HTTP-Call pro Spieler. Geladen wird deshalb nur für:
- alle Squad-Spieler (überschaubar, ~15),
- die N aussichtsreichsten Markt-Spieler (Default 10),
und `sl` zusätzlich nur dann, wenn `prob` für diesen Spieler fehlt. In der
Spieltagswoche — wenn `prob` da ist — kostet die Kette also **null** zusätzliche
Requests. Für alle übrigen Markt-Spieler bleibt der Trend `None`.

**Woher die Trends seit P1-7 kommen (Defekt D8).** Drei Quellen, in dieser
Reihenfolge:

1. **Squad-Payload.** `tfhmvt` (24 h in €) und `sdmvt` (7 d in €) stehen in
   der Squad-Response, die der Tick ohnehin holt. Für Kaderspieler decken sie
   das 1-d- und 7-d-Fenster ohne einen einzigen zusätzlichen Request ab. Die
   Market-Items tragen die Felder **nicht** — dort bleibt es bei der Historie.
2. **Cache.** Die Marktwert-Historie wird bis zum nächsten Update-Zeitpunkt
   (`mvud`) aufbewahrt. Kickbase schreibt Marktwerte einmal täglich fort; bei
   120-min-Takt holte der Bot elf von zwölf Malen unveränderte Daten.
3. **HTTP.** Nur noch, was in 1 und 2 nicht beantwortet ist.

Die Shortlist folgt seit P1-7 der **Punkteausbeute (`ap`)**, nicht mehr dem
Marktwert. Teuer heißt nicht interessant: nach Marktwert sortiert landeten
zuverlässig dieselben Stars in der Liste, während der 900k-Rohpunktesammler —
der Fall, den §2.6 als PROFIT-These beschreibt — nie eine Historie bekam.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal

from app.domain.exceptions import KickbaseError
from app.domain.gateways import KickbaseGateway, MarketValueCache
from app.domain.models import (
    MarketPlayer,
    MarketValuePoint,
    Player,
    PlayerStatus,
    Squad,
    SquadPlayer,
)

_log = logging.getLogger(__name__)


_INJURY_STATUS_LABELS: dict[PlayerStatus, str] = {
    PlayerStatus.UNKNOWN: "unknown",
    PlayerStatus.FIT: "fit",
    PlayerStatus.INJURED: "injured",
    PlayerStatus.UNKNOWN_2: "questionable",
    PlayerStatus.OUT_OF_SQUAD: "out_of_squad",
    PlayerStatus.REHAB: "rehab",
    PlayerStatus.RED_CARD: "suspended_red",
    PlayerStatus.YELLOW_RED_CARD: "suspended_yellow_red",
    PlayerStatus.NOT_IN_TEAM: "not_in_team",
}


# Stufe 3 der Kette: reine Verfügbarkeits-Heuristik. Sie unterscheidet nicht
# zwischen Stammspieler und Ersatzbank — deshalb bekommt jeder Wert von hier
# das `..._heuristic`-Flag und die Quelle `injury_status`.
# `PlayerStatus.UNKNOWN` steht bewusst **nicht** drin: ein unbekanntes `st`
# rechtfertigt keine Zahl (Defekt D6).
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

# Stufe 1: `prob` 1..5 → Wahrscheinlichkeit. **`1` ist die sicherste Startelf**
# (Plan §8/F2, empirisch: der Median-Marktwert fällt monoton von 25,4 Mio bei
# Stufe 1 auf 3,6 Mio bei Stufe 5). Die Richtung ist das Risiko Nr. 1 dieses
# Pakets — sie invertiert zu lesen hieße, Ersatzspieler für Stammkräfte zu
# halten. Deshalb benannte Konstante statt Inline-Arithmetik.
_PROB_TO_PROBABILITY: dict[int, float] = {1: 0.95, 2: 0.80, 3: 0.55, 4: 0.30, 5: 0.05}

# Stufe 2: `sl` ist ein bool — die Werte sind bewusst weniger extrem als bei
# `prob`, weil eine Ja/Nein-Prognose weniger Information trägt als fünf Stufen.
_SL_TO_PROBABILITY: dict[bool, float] = {True: 0.80, False: 0.20}

_SOURCE_PROB = "kickbase_prob"
_SOURCE_LINEUP_PREDICTION = "lineup_prediction"
_SOURCE_INJURY_STATUS = "injury_status_heuristic"
_SOURCE_NONE = "none"

_FLAG_START_PROBABILITY_HEURISTIC = "missing_data:start_probability_next_heuristic"
_FLAG_START_PROBABILITY_MISSING = "missing_data:start_probability_next"
_FLAG_STATUS_UNKNOWN = "missing_data:injury_status"
_FLAG_AVG_POINTS_MISSING = "missing_data:avg_points_last5"
_FLAG_AVG_POINTS_SEASON = "missing_data:avg_points_last5_using_season_avg"
_FLAG_TREND_MISSING = "missing_data:market_trend_7d_pct"


@dataclass(frozen=True, slots=True)
class HistoryMetrics:
    """Aus der Marktwert-Historie abgeleitete Momentum-Kennzahlen."""

    trend_1d_pct: float | None
    trend_3d_pct: float | None
    trend_7d_pct: float | None
    trend_30d_pct: float | None
    mv_max_30d: int | None


_EMPTY_METRICS = HistoryMetrics(
    trend_1d_pct=None,
    trend_3d_pct=None,
    trend_7d_pct=None,
    trend_30d_pct=None,
    mv_max_30d=None,
)


@dataclass(frozen=True, slots=True)
class PlayerEnrichment:
    """Zusatzsignale, die der Master-Prompt pro Spieler erwartet."""

    player_id: str
    market_trend_1d_pct: float | None
    market_trend_3d_pct: float | None
    market_trend_7d_pct: float | None
    market_trend_30d_pct: float | None
    mv_max_30d: int | None
    avg_points_last5: float | None
    # `None`, wenn keine Quelle gegriffen hat. Ein erfundener Default wäre
    # schlimmer: das Modell kann `null` als „unbekannt" lesen, eine 0.5 nicht
    # von einer echten Prognose unterscheiden (Plan §9).
    start_probability_next: float | None
    # Welche Stufe der Kette den Wert geliefert hat — siehe Modul-Docstring.
    start_probability_source: str
    injury_status: str
    missing_data_flags: tuple[str, ...] = field(default_factory=tuple)


class PlayerEnricher:
    """Sammelt die Zusatzsignale aus Kickbase-Endpoints + Domain-Heuristiken."""

    def __init__(
        self,
        kickbase: KickbaseGateway,
        *,
        max_market_history: int = 10,
        history_days: int = 30,
        max_lineup_predictions: int | None = None,
        cache: MarketValueCache | None = None,
    ) -> None:
        self._kickbase = kickbase
        # Ohne Cache verhält sich der Enricher wie vor P1-7: jeder Tick holt
        # jede Historie neu. Das ist der Zustand in Tests, die den Cache nicht
        # interessiert — nicht ein stiller Default.
        self._cache = cache
        self._max_market_history = max_market_history
        # Deckel für Stufe 2 der Startelf-Kette (`sl`, ein Request pro Spieler).
        # Ohne Deckel wären es Kader + *alle* Marktspieler — knapp 30 zusätzliche
        # Requests pro Tick, obendrauf auf die Historien-Calls. §9 des Plans
        # führt genau das als Ban-Risiko. Default: dieselbe Auswahl, die schon
        # Historien bekommt, also keine *neuen* Spieler, nur ein zweiter Call
        # für die, die ohnehin interessieren.
        self._max_lineup_predictions = max_lineup_predictions
        # 30 d ist das größte gebrauchte Fenster (trend_30d, mv_max_30d). Für den
        # 30 d-Trend brauchen wir 31 Punkte (Index -31 vs -1). Kleinere Fenster
        # (1/3/7 d) und `mv_max_30d` fallen aus derselben Serie ab.
        self._history_days = max(history_days, 31)

    async def enrich(
        self,
        league_id: str,
        squad: Squad,
        market: Sequence[MarketPlayer],
        *,
        mv_update_at: datetime | None = None,
        now: datetime | None = None,
    ) -> dict[str, PlayerEnrichment]:
        """Baut die Zusatzsignale je Spieler.

        `mv_update_at` ist der nächste Marktwert-Update-Zeitpunkt (`mvud` aus
        dem Market-Root) und damit die Gültigkeitsgrenze des Historien-Caches.
        Fehlt er, wird nicht gecacht — eine geratene Haltbarkeit wäre schlimmer
        als gar keine, weil eine zu lange gehaltene Serie den Bot einen ganzen
        Marktwert-Zyklus lang blind für die Bewegung machen würde.
        """
        now = now or datetime.now(UTC)
        squad_ids = [sp.player.id for sp in squad.players]
        shortlist_ids = _shortlist(market, limit=self._max_market_history)
        history_targets = list(dict.fromkeys(squad_ids + shortlist_ids))

        metrics = await self._fetch_all_metrics(
            league_id, history_targets, mv_update_at=mv_update_at, now=now
        )
        # Die Payload-Trends überschreiben die 1-d-/7-d-Fenster der Historie:
        # sie stammen aus dem Live-Squad-Call dieses Ticks, die Historie
        # womöglich aus dem Cache von heute Nachmittag.
        metrics = _apply_payload_trends(metrics, squad.players)

        players: dict[str, Player] = {sp.player.id: sp.player for sp in squad.players}
        for mp in market:
            players.setdefault(mp.player.id, mp.player)

        prob_by_player = {
            mp.player.id: mp.start_probability_raw
            for mp in market
            if mp.start_probability_raw is not None
        }
        # Nur ein Wert, den wir auch abbilden können, macht Stufe 2 überflüssig.
        # Eine unbekannte `prob`-Stufe (etwa eine sechste) darf den Nachfass-Call
        # nicht blockieren — sonst fiele die Kette still auf die Statuspauschale.
        usable_prob = {pid for pid, raw in prob_by_player.items() if raw in _PROB_TO_PROBABILITY}
        predictions = await self._fetch_lineup_predictions(
            league_id, history_targets, known_prob=usable_prob
        )

        history_set = set(history_targets)
        result: dict[str, PlayerEnrichment] = {}
        for pid, player in players.items():
            m = metrics.get(pid, _EMPTY_METRICS)
            avg5, avg5_flag = _avg_points_proxy(player)
            start_prob, source = _start_probability(
                raw_prob=prob_by_player.get(pid),
                predicted_starter=predictions.get(pid),
                status=player.status,
            )
            flags = _collect_flags(
                trend_missing=pid not in history_set or m.trend_7d_pct is None,
                avg5=avg5,
                avg5_is_season_average=avg5_flag,
                source=source,
                status=player.status,
            )
            result[pid] = PlayerEnrichment(
                player_id=pid,
                market_trend_1d_pct=m.trend_1d_pct,
                market_trend_3d_pct=m.trend_3d_pct,
                market_trend_7d_pct=m.trend_7d_pct,
                market_trend_30d_pct=m.trend_30d_pct,
                mv_max_30d=m.mv_max_30d,
                avg_points_last5=avg5,
                start_probability_next=start_prob,
                start_probability_source=source,
                injury_status=_INJURY_STATUS_LABELS.get(player.status, "unknown"),
                missing_data_flags=flags,
            )
        return result

    async def _fetch_lineup_predictions(
        self, league_id: str, candidates: Sequence[str], *, known_prob: set[str]
    ) -> dict[str, bool | None]:
        """Stufe 2 der Kette: `sl` je Spieler — nur wo `prob` fehlt.

        In der Spieltagswoche liefert Kickbase `prob` für den ganzen Markt; dann
        ist `targets` leer und es geht kein einziger zusätzlicher Request raus.
        Außerhalb kostet es einen Call je Spieler aus der ohnehin beobachteten
        Auswahl — nicht je Marktspieler.
        """
        targets = [pid for pid in candidates if pid not in known_prob]
        if self._max_lineup_predictions is not None:
            targets = targets[: self._max_lineup_predictions]
        if not targets:
            return {}

        results = await asyncio.gather(
            *(self._fetch_lineup_prediction(league_id, pid) for pid in targets),
            return_exceptions=False,
        )
        return dict(zip(targets, results, strict=True))

    async def _fetch_lineup_prediction(self, league_id: str, player_id: str) -> bool | None:
        try:
            detail = await self._kickbase.get_player_detail(league_id, player_id)
        except KickbaseError as exc:
            _log.info(
                "Startelf-Prognose für %s nicht ladbar (%s) — Kette fällt eine Stufe tiefer.",
                player_id,
                exc,
            )
            return None
        return detail.is_predicted_starter

    async def _fetch_all_metrics(
        self,
        league_id: str,
        player_ids: Iterable[str],
        *,
        mv_update_at: datetime | None,
        now: datetime,
    ) -> dict[str, HistoryMetrics]:
        ids = list(player_ids)
        if not ids:
            return {}

        cached = self._read_cache(league_id, ids, now=now)
        missing = [pid for pid in ids if pid not in cached]
        fetched = await asyncio.gather(
            *(self._fetch_history(league_id, pid) for pid in missing),
            return_exceptions=False,
        )
        # Die Zahlen gehören ins Log, weil das DoD von P1-7 sie verlangt:
        # „Requests/Tick im Log messbar gesunken". Ohne die Zeile ist die
        # Wirkung des Pakets im Betrieb nicht nachweisbar.
        _log.info(
            "Marktwert-Historien: %d aus dem Cache, %d per HTTP geholt (von %d Spielern).",
            len(cached),
            len(missing),
            len(ids),
        )

        histories: dict[str, list[MarketValuePoint]] = dict(cached)
        for pid, history in zip(missing, fetched, strict=True):
            if history is None:
                continue
            histories[pid] = history
            self._write_cache(league_id, pid, history, valid_until=mv_update_at, now=now)

        return {pid: _metrics_from_history(histories.get(pid, [])) for pid in ids}

    def _read_cache(
        self, league_id: str, player_ids: Sequence[str], *, now: datetime
    ) -> dict[str, list[MarketValuePoint]]:
        if self._cache is None:
            return {}
        try:
            return self._cache.get_many(league_id, player_ids, now=now)
        except Exception:  # ein kaputter Cache darf den Tick nicht kippen
            _log.warning("Marktwert-Cache nicht lesbar — hole alles per HTTP.", exc_info=True)
            return {}

    def _write_cache(
        self,
        league_id: str,
        player_id: str,
        history: list[MarketValuePoint],
        *,
        valid_until: datetime | None,
        now: datetime,
    ) -> None:
        # Ohne bekannten nächsten Update-Zeitpunkt wird nicht geschrieben, und
        # ein bereits verstrichener taugt auch nicht: er läge sofort in der
        # Vergangenheit und der Eintrag wäre beim Anlegen schon abgelaufen.
        if self._cache is None or valid_until is None or valid_until <= now:
            return
        try:
            self._cache.put(league_id, player_id, history, valid_until=valid_until)
        except Exception:  # siehe `_read_cache`
            _log.warning("Marktwert-Cache nicht schreibbar (%s).", player_id, exc_info=True)

    async def _fetch_history(self, league_id: str, player_id: str) -> list[MarketValuePoint] | None:
        """Historie eines Spielers. `None` heißt „nicht ladbar", nicht „leer"."""
        try:
            return await self._kickbase.get_market_value_history(
                league_id, player_id, days=self._history_days
            )
        except KickbaseError as exc:
            _log.info(
                "Marktwert-Historie für %s nicht ladbar (%s) — Metriken bleiben None.",
                player_id,
                exc,
            )
            return None


def _shortlist(market: Sequence[MarketPlayer], *, limit: int) -> list[str]:
    """Die Markt-Spieler, für die sich eine Historie lohnt — nach `ap` sortiert.

    Bis P1-7 war das Kriterium der Marktwert. Das hat zuverlässig dieselben
    Stars ausgewählt, die man ohnehin nicht kauft, und die billigen
    Rohpunkte-Sammler übersehen — genau die Gruppe, auf die §2.6 die
    PROFIT-These stützt.

    Spieler ohne `ap` stehen hinten, nicht vorne: „keine Daten" ist kein Grund,
    ein knappes Request-Budget auszugeben (Plan §9, Ban-Risiko).
    """
    ranked = sorted(
        market,
        key=lambda m: (
            m.player.average_points is None,
            -(m.player.average_points or 0.0),
        ),
    )
    return [mp.player.id for mp in ranked[:limit]]


def _apply_payload_trends(
    metrics: dict[str, HistoryMetrics], squad_players: Sequence[SquadPlayer]
) -> dict[str, HistoryMetrics]:
    """Ersetzt 1-d-/7-d-Trend der Kaderspieler durch die Werte aus dem Payload.

    `tfhmvt`/`sdmvt` sind absolute Euro-Deltas; der Prompt will Prozent. Aus
    `delta = mv_heute - mv_damals` folgt `mv_damals = mv_heute - delta`, und
    damit `pct = delta / (mv_heute - delta) * 100`.

    Die Gegenprobe gegen die Historie geht exakt auf (Plan §6/P1-7):
    Upamecano `tfhmvt` 5.697 bei `mv` 33.697.577 ergibt einen Vortageswert von
    33.691.880 — genau der Wert, der in der 365-Tage-Serie steht.
    """
    updated = dict(metrics)
    for sp in squad_players:
        base = updated.get(sp.player.id, _EMPTY_METRICS)
        one_day = _pct_from_delta(sp.player.market_value, sp.mv_change_1d)
        seven_day = _pct_from_delta(sp.player.market_value, sp.mv_change_7d)
        if one_day is None and seven_day is None:
            continue
        updated[sp.player.id] = replace(
            base,
            trend_1d_pct=one_day if one_day is not None else base.trend_1d_pct,
            trend_7d_pct=seven_day if seven_day is not None else base.trend_7d_pct,
        )
    return updated


def _pct_from_delta(current: Decimal, delta: Decimal | None) -> float | None:
    if delta is None:
        return None
    return _pct_delta(current - delta, current)


def _pct_delta(base: Decimal, latest: Decimal) -> float | None:
    if base <= Decimal(0):
        return None
    return round(float((latest - base) / base * Decimal(100)), 2)


def _metrics_from_history(history: list[MarketValuePoint]) -> HistoryMetrics:
    """Berechnet Trend-Metriken aus einer chronologisch aufsteigenden Serie.

    Fenster, für die weniger Historie da ist als nötig, geben `None` zurück —
    die LLM sieht dann ein explizites `null` statt einen verzerrten Wert.
    """
    if len(history) < 2:  # noqa: PLR2004 — Minimum für Delta-Berechnung
        return _EMPTY_METRICS
    last = history[-1].value

    def _trend(days: int) -> float | None:
        if len(history) <= days:
            return None
        return _pct_delta(history[-1 - days].value, last)

    # `mv_max_30d`: höchster Marktwert der letzten (bis zu) 30 Tage. Als grobes
    # „Wie weit weg sind wir vom kürzlichen Hoch?"-Signal für die LLM.
    window_30 = history[-30:]
    mv_max_30d = max((int(p.value) for p in window_30), default=None)

    return HistoryMetrics(
        trend_1d_pct=_trend(1),
        trend_3d_pct=_trend(3),
        trend_7d_pct=_trend(7),
        trend_30d_pct=_trend(30),
        mv_max_30d=mv_max_30d,
    )


def _start_probability(
    *,
    raw_prob: int | None,
    predicted_starter: bool | None,
    status: PlayerStatus,
) -> tuple[float | None, str]:
    """Die Quellen-Kette aus dem Modul-Docstring, in ihrer Reihenfolge.

    Gibt (Wahrscheinlichkeit, Quelle) zurück. Die Quelle ist Teil der Antwort,
    nicht Beiwerk: eine 0.85 aus `prob` und eine 0.85 aus der Statuspauschale
    sehen im JSON identisch aus, taugen aber unterschiedlich viel.
    """
    if raw_prob is not None:
        mapped = _PROB_TO_PROBABILITY.get(raw_prob)
        if mapped is not None:
            return mapped, _SOURCE_PROB
        _log.warning(
            "Unbekannte `prob`-Stufe %r — erwartet 1..5 (Plan §8/F2). Kette fällt eine "
            "Stufe tiefer, statt den Wert zu raten.",
            raw_prob,
        )

    if predicted_starter is not None:
        return _SL_TO_PROBABILITY[predicted_starter], _SOURCE_LINEUP_PREDICTION

    heuristic = _START_PROBABILITY_BY_STATUS.get(status)
    if heuristic is not None:
        return heuristic, _SOURCE_INJURY_STATUS

    # Bleibt nur bei `PlayerStatus.UNKNOWN`: ein `st`, das wir nicht kennen,
    # trägt keine Aussage über die Startelf (Defekt D6).
    return None, _SOURCE_NONE


def _collect_flags(
    *,
    trend_missing: bool,
    avg5: float | None,
    avg5_is_season_average: bool,
    source: str,
    status: PlayerStatus,
) -> tuple[str, ...]:
    """Baut die `missing_data`-Flags — ein Flag je tatsächlich fehlender Sache.

    Vor P0-3 hing `..._start_probability_next_heuristic` an **jedem** Spieler,
    unabhängig davon, woher der Wert kam. Ein Flag, das immer gesetzt ist,
    trägt keine Information; das Modell kann daran nicht erkennen, wann es der
    Prognose trauen darf.
    """
    flags: list[str] = []
    if trend_missing:
        flags.append(_FLAG_TREND_MISSING)
    if avg5 is None:
        flags.append(_FLAG_AVG_POINTS_MISSING)
    elif avg5_is_season_average:
        flags.append(_FLAG_AVG_POINTS_SEASON)
    if source == _SOURCE_INJURY_STATUS:
        flags.append(_FLAG_START_PROBABILITY_HEURISTIC)
    elif source == _SOURCE_NONE:
        flags.append(_FLAG_START_PROBABILITY_MISSING)
    if status is PlayerStatus.UNKNOWN:
        flags.append(_FLAG_STATUS_UNKNOWN)
    return tuple(flags)


def _avg_points_proxy(player: Player) -> tuple[float | None, bool]:
    """Fallback für `avg_points_last5`: nutzt Saison-Ø.

    Rückgabe: (Wert, ist_saison_durchschnitt). `None` heißt **nur**, dass
    Kickbase gar keine Punktedaten liefert — bis P0-3 galt hier `ap > 0`, und
    damit fielen die beiden Spieler mit negativem Saison-Ø (Platzverweis,
    Eigentor) in denselben Topf wie die vier ohne jede Angabe. Ein Minuswert
    ist aber ein Datum, und zwar ein besonders aussagekräftiges.
    """
    if player.average_points is None:
        return None, False
    return round(float(player.average_points), 2), True


__all__ = ["HistoryMetrics", "PlayerEnricher", "PlayerEnrichment"]
