"""Gegnerstärke und Restspielplan — die Datengrundlage für Prompt-§1.2 (P2-11).

Der Master-Prompt verlangt seit der ersten Fassung, Käufe und Aufstellung am
Spielplan auszurichten: „ein Innenverteidiger eines starken Teams bringt bei
einem 2:0-Heimsieg rund +72 Punkte ohne eine einzige Offensivaktion". Bis P2-11
stand dazu kein einziges Feld im USER-JSON, und §1.2 musste das ausdrücklich
verbieten („rechne nicht mit ihnen und erfinde sie nicht").

Dieses Modul schließt die Lücke aus zwei Quellen, die **je einen** HTTP-Call
kosten:

- `GET /v4/competitions/1/table` → `TeamStanding` je Verein (Platz, Punkte,
  Tordifferenz).
- `GET /v4/competitions/1/matchdays` → `Fixture` für **alle 34** Spieltage.

Beides ist ligaweit, nicht spielerbezogen: 18 Tabellenzeilen und 306
Paarungen beantworten die Frage für jeden Spieler im Kader *und* auf dem Markt.

## Die FDR-Skala

`fdr` (fixture difficulty rating) läuft von **1 = leichtester Gegner** bis
**5 = schwerster Gegner**. Die Richtung ist die Hauptfehlerquelle dieses
Pakets — bei `prob` hat dieselbe Frage einen eigenen Eintrag im Plan gebraucht
(§8/F2), weil eine invertierte Skala Ersatzspieler zu Stammkräften macht. Hier
wäre die Folge: das Modell kauft systematisch die Spieler mit den schwersten
Spielen. Deshalb stehen die Bänder als benannte Konstante da, nicht als
Inline-Arithmetik, und der Payload trägt zusätzlich den Klartext-Namen des
Gegners samt Tabellenplatz — damit eine Verdrehung im Log auffällt.

Die Ableitung:

1. **Tabellenplatz des Gegners** → Grundstufe (`_FDR_RANK_BANDS`).
2. **Tordifferenz pro Spiel des Gegners** → ±1 Stufe ab ±1,5 Tore. Die
   Tabelle ist im August dünn: nach vier Spieltagen trennen Platz 4 und
   Platz 12 drei Punkte, aber Bayern (+12 Tore in 4 Spielen) und Hamburg
   (-11) sind schon klar unterschiedlich stark. Pro Spiel normiert, damit die
   Schwelle über die ganze Saison dieselbe Aussage hat.
3. **Clamp auf 1..5.**

**Heim/Auswärts fließt bewusst nicht ein.** Der Heimvorteil der Bundesliga
liegt bei gut einem Drittel Punkt pro Spiel — eine ganze FDR-Stufe wäre dafür
eine erfundene Gewichtung, und die verbietet §9 des Plans. `is_home` steht
deshalb als eigenes Feld daneben: `fdr` misst den Gegner, `is_home` den Platz,
und das Modell gewichtet selbst.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.domain.models import Fixture, TeamStanding

# Grundstufe aus dem Tabellenplatz des Gegners. Obergrenze des Bands → Stufe.
# 18 Vereine, Bänder 3/3/6/3/3: die Ränder sind schmaler als die Mitte, weil
# sich Spitze und Keller sportlich deutlicher unterscheiden als Platz 8 von
# Platz 11.
_FDR_RANK_BANDS: tuple[tuple[int, int], ...] = (
    (3, 5),
    (6, 4),
    (12, 3),
    (15, 2),
)
# Alles darunter (Platz 16 und schlechter, oder ein Gegner ohne Tabellenzeile
# mit sehr hohem Rang) landet hier.
_FDR_WORST_RANK_LEVEL = 1

_FDR_MIN = 1
_FDR_MAX = 5
# Ab dieser Tordifferenz **pro Spiel** korrigiert Schritt 2 um eine Stufe.
_GOAL_DIFF_PER_MATCH_THRESHOLD = 1.5

# Wie viele kommende Spiele in `fdr_next3` einfließen. Drei, weil das die
# Spanne ist, über die eine Trade-Position üblicherweise gehalten wird (§3a:
# `days_held`, Slot-Ökonomie) — ein Restspielplan über zehn Spieltage sagt
# nichts mehr über einen Kauf, der in vier Tagen wieder verkauft wird.
LOOKAHEAD_MATCHES = 3


@dataclass(frozen=True, slots=True)
class TeamOutlook:
    """Was einem Verein als Nächstes bevorsteht.

    Gilt für **jeden** Spieler dieses Vereins — Kader wie Markt. Alle Felder
    dürfen `None` sein: am Saisonende gibt es kein nächstes Spiel, und ein
    Gegner ohne Tabellenzeile hat keinen Rang. `None` heißt hier wie überall im
    Projekt „unbekannt", nicht „neutral" (Plan §9); der Payload setzt dann das
    Flag `missing_data:fixtures`.
    """

    team_id: str
    next_opponent_id: str | None = None
    next_opponent_name: str | None = None
    next_opponent_rank: int | None = None
    is_home: bool | None = None
    fdr: int | None = None
    # Mittlere Schwierigkeit der nächsten `LOOKAHEAD_MATCHES` Spiele, eine
    # Dezimale. Trägt den *Restspielplan*: ein Spieler vor Bayern-Dortmund-
    # Leverkusen ist eine andere Wette als einer vor drei Kellerkindern, auch
    # wenn beide dieselbe `fdr` für das nächste Spiel haben.
    fdr_next3: float | None = None
    next_kickoff: datetime | None = None
    next_matchday: int | None = None

    @property
    def is_known(self) -> bool:
        """Liegt überhaupt eine Spielplan-Information vor?"""
        return self.next_opponent_id is not None


def fixture_difficulty(standing: TeamStanding | None) -> int | None:
    """FDR 1..5 des Gegners: **1 = leicht, 5 = schwer**.

    `None`, wenn der Gegner nicht in der Tabelle steht — dann ist jede Zahl
    geraten, und eine geratene 3 wäre vom Modell nicht von einer gemessenen zu
    unterscheiden.
    """
    if standing is None:
        return None
    level = _FDR_WORST_RANK_LEVEL
    for max_rank, band_level in _FDR_RANK_BANDS:
        if standing.rank <= max_rank:
            level = band_level
            break
    level += _goal_difference_adjustment(standing)
    return max(_FDR_MIN, min(_FDR_MAX, level))


def _goal_difference_adjustment(standing: TeamStanding) -> int:
    """±1 Stufe, sobald die Tordifferenz pro Spiel deutlich ausfällt.

    Ohne gespieltes Spiel gibt es keine Aussage: am 1. Spieltag stehen alle
    18 Vereine bei 0:0 und der Platz ist alphabetisch oder aus der Vorsaison
    vergeben. Dann korrigiert hier nichts.
    """
    if standing.matches_played <= 0:
        return 0
    per_match = standing.goal_difference / standing.matches_played
    if per_match >= _GOAL_DIFF_PER_MATCH_THRESHOLD:
        return 1
    if per_match <= -_GOAL_DIFF_PER_MATCH_THRESHOLD:
        return -1
    return 0


def build_team_outlooks(
    standings: Sequence[TeamStanding],
    fixtures: Iterable[Fixture],
    *,
    now: datetime,
    lookahead: int = LOOKAHEAD_MATCHES,
) -> dict[str, TeamOutlook]:
    """Ein `TeamOutlook` je Verein, der in Tabelle oder Spielplan vorkommt.

    „Kommend" heißt: nicht beendet (`st != 2`) **und** Anpfiff nicht in der
    Vergangenheit. Beide Bedingungen zusammen, weil sie verschiedene Lücken
    schließen: ein laufendes Spiel trägt noch `st: 0`, obwohl es als nächstes
    Spiel nichts mehr taugt, und ein abgesagtes Spiel kann in der Zukunft
    liegen, ohne je angepfiffen zu werden.

    Der Spielplan ist die führende Quelle für die Team-Menge, nicht die
    Tabelle: ein Verein ohne Tabellenzeile hat trotzdem ein nächstes Spiel, und
    dann sind Gegner und Heimrecht bekannt — nur die Schwierigkeit nicht.
    """
    by_team = {s.team_id: s for s in standings}
    upcoming = sorted(
        (f for f in fixtures if not f.is_finished and f.kickoff >= now),
        key=lambda f: (f.kickoff, f.matchday),
    )

    schedule: dict[str, list[Fixture]] = {}
    for fixture in upcoming:
        schedule.setdefault(fixture.home_team_id, []).append(fixture)
        schedule.setdefault(fixture.away_team_id, []).append(fixture)

    team_ids = set(by_team) | set(schedule)
    return {
        team_id: _outlook_for(team_id, schedule.get(team_id, ()), by_team, lookahead=lookahead)
        for team_id in team_ids
    }


def _outlook_for(
    team_id: str,
    schedule: Sequence[Fixture],
    standings: Mapping[str, TeamStanding],
    *,
    lookahead: int,
) -> TeamOutlook:
    if not schedule:
        return TeamOutlook(team_id=team_id)

    nxt = schedule[0]
    opponent_id = nxt.opponent_of(team_id)
    opponent = standings.get(opponent_id) if opponent_id else None
    return TeamOutlook(
        team_id=team_id,
        next_opponent_id=opponent_id,
        next_opponent_name=opponent.team_name if opponent else None,
        next_opponent_rank=opponent.rank if opponent else None,
        is_home=nxt.is_home_for(team_id),
        fdr=fixture_difficulty(opponent),
        fdr_next3=_mean_fdr(team_id, schedule[:lookahead], standings),
        next_kickoff=nxt.kickoff,
        next_matchday=nxt.matchday,
    )


def _mean_fdr(
    team_id: str,
    schedule: Sequence[Fixture],
    standings: Mapping[str, TeamStanding],
) -> float | None:
    """Mittlere Schwierigkeit über die übergebenen Spiele.

    Spiele gegen einen Gegner ohne Tabellenzeile fallen heraus statt als 3
    einzugehen — ein Mittelwert aus einer echten und einer geratenen Zahl wäre
    schlechter als einer aus weniger Zahlen. Bleibt nichts übrig, ist das
    Ergebnis `None`.
    """
    levels: list[int] = []
    for fixture in schedule:
        opponent_id = fixture.opponent_of(team_id) or ""
        level = fixture_difficulty(standings.get(opponent_id))
        if level is not None:
            levels.append(level)
    if not levels:
        return None
    return round(sum(levels) / len(levels), 1)


__all__ = [
    "LOOKAHEAD_MATCHES",
    "TeamOutlook",
    "build_team_outlooks",
    "fixture_difficulty",
]
