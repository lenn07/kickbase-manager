"""Aufstellungs-Regeln — die teuerste Regel des Spiels, deterministisch.

Jede unbesetzte Startelf-Position kostet **-100 Punkte** (Plan §2.2.2). Das ist
mehr, als die meisten Spieler an einem guten Spieltag einbringen, und es ist
der einzige Verlust im Spiel, den man durch blosses Nichtstun erleidet.

Deshalb liegt diese Logik im Domain-Layer und nicht im Prompt: ein Sprachmodell
darf entscheiden, *welcher* Spieler auf die Bank gehört; ob elf Positionen
besetzt sind, ist keine Ermessensfrage.

**Formationen.** Kickbase gibt eine feste Liste taktischer Systeme vor, immer
mit genau einem Torwart und zehn Feldspielern. Verifiziert ist bislang nur
`3-5-2` (aus `lineup/overview.t` der Cassette); die übrigen stammen aus den
öffentlichen Kickbase-Systemen und sind **nicht gegen die API geprüft** — ein
`set_lineup` mit einer davon kann abgelehnt werden. Der sichere Weg ist
deshalb, die von Kickbase gemeldete Formation beizubehalten und nur die
Besetzung zu ändern; andere Systeme sind Kandidaten, keine Vorgabe.

**Unvollständige Kader.** Der Kader kann kleiner als elf sein — real am
2026-09-23: acht Spieler, drei leere Slots, -300 Punkte am nächsten Spieltag.
Genau dann ist das Aufstellen am wertvollsten, und genau dann gibt es keine
vollständige Formation. `validate_lineup` verlangt deshalb **höchstens** elf
und eine Verteilung, die die Formation nicht *überschreitet* — nicht „exakt
elf". Eine Regel, die nur bei vollem Kader greift, hilft nie, wenn es zählt.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.models import PlayerStatus, Position, SquadPlayer

#: Status, mit denen ein Spieler am nächsten Spieltag sicher nicht aufläuft.
#: Sie stehen im Kaderdatensatz selbst und sind damit **immer** verfügbar,
#: anders als die Startelf-Prognose: die kommt aus einer Quellenkette und fällt
#: bei einem API-Fehler komplett aus (`RunTickUseCase._enrich_players` liefert
#: dann ein leeres Mapping). Ohne diese Untergrenze stellte der Guard genau
#: dann gesperrte Spieler auf, wenn die Anreicherung fehlgeschlagen ist.
CANNOT_PLAY_STATUSES = frozenset(
    {
        PlayerStatus.INJURED,
        PlayerStatus.REHAB,
        PlayerStatus.RED_CARD,
        PlayerStatus.YELLOW_RED_CARD,
        PlayerStatus.NOT_IN_TEAM,
    }
)

#: Slots je Formation: (Abwehr, Mittelfeld, Sturm). Torwart ist immer genau 1.
FORMATIONS: dict[str, tuple[int, int, int]] = {
    "3-4-3": (3, 4, 3),
    "3-5-2": (3, 5, 2),  # einzige gegen die API verifizierte Formation
    "3-6-1": (3, 6, 1),
    "4-2-4": (4, 2, 4),
    "4-3-3": (4, 3, 3),
    "4-4-2": (4, 4, 2),
    "4-5-1": (4, 5, 1),
    "5-2-3": (5, 2, 3),
    "5-3-2": (5, 3, 2),
    "5-4-1": (5, 4, 1),
}

DEFAULT_FORMATION = "3-5-2"

#: Kickbase-`lo` 0..10 sind die elf Startelf-Slots.
LINEUP_SIZE = 11

_GOALKEEPERS = 1


@dataclass(frozen=True, slots=True)
class Lineup:
    """Eine Aufstellung: Formation plus die Spieler-IDs in Slot-Reihenfolge."""

    formation: str
    player_ids: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        return len(self.player_ids) == LINEUP_SIZE


def slots_for(formation: str) -> dict[Position, int] | None:
    """Positions-Kontingent einer Formation, oder None bei unbekannter Formation."""
    counts = FORMATIONS.get(formation)
    if counts is None:
        return None
    defenders, midfielders, forwards = counts
    return {
        Position.GOALKEEPER: _GOALKEEPERS,
        Position.DEFENDER: defenders,
        Position.MIDFIELDER: midfielders,
        Position.FORWARD: forwards,
    }


def validate_lineup(
    player_ids: Sequence[str],
    formation: str,
    squad: Sequence[SquadPlayer],
) -> list[str]:
    """Prüft eine Aufstellung gegen Formation und Kader. Leere Liste = in Ordnung.

    Gibt **alle** Verstöße zurück, nicht nur den ersten: wer eine ungültige
    Aufstellung debuggt, will nicht zehnmal nacheinander je einen Fehler sehen.

    Bewusst erlaubt: **weniger** als elf Spieler. Ein Kader mit acht Spielern
    kann keine volle Elf stellen, und dann ist eine Teilaufstellung das Beste,
    was geht — jeder besetzte Slot ist 100 Punkte wert. Verboten bleibt alles,
    was Kickbase ablehnen würde oder was Punkte verschenkt: zu viele Spieler,
    Duplikate, Fremd-IDs, überbesetzte Positionen.
    """
    errors: list[str] = []

    slots = slots_for(formation)
    if slots is None:
        errors.append(
            f"Unbekannte Formation {formation!r} — erlaubt: {', '.join(sorted(FORMATIONS))}"
        )

    if len(player_ids) > LINEUP_SIZE:
        errors.append(
            f"{len(player_ids)} Spieler aufgestellt, erlaubt sind höchstens {LINEUP_SIZE}"
        )

    duplicates = sorted({pid for pid in player_ids if list(player_ids).count(pid) > 1})
    if duplicates:
        errors.append(f"Spieler mehrfach aufgestellt: {', '.join(duplicates)}")

    by_id = {sp.player.id: sp for sp in squad}
    foreign = [pid for pid in dict.fromkeys(player_ids) if pid not in by_id]
    if foreign:
        errors.append(f"Nicht im Kader: {', '.join(foreign)}")

    if slots is not None:
        counts: dict[Position, int] = {}
        for pid in dict.fromkeys(player_ids):
            sp = by_id.get(pid)
            if sp is None:
                continue
            counts[sp.player.position] = counts.get(sp.player.position, 0) + 1
        for position, allowed in slots.items():
            placed = counts.get(position, 0)
            if placed > allowed:
                errors.append(
                    f"{position.name}: {placed} aufgestellt, {formation} erlaubt {allowed}"
                )

    return errors


def best_lineup(
    squad: Sequence[SquadPlayer],
    *,
    start_probabilities: dict[str, float | None] | None = None,
    formation_candidates: Sequence[str] = (),
) -> Lineup:
    """Stellt die punktbeste zulässige Elf, greedy über Startelf-Chance x Punkte.

    Der Score ist bewusst einfach: `P(Startelf) x erwartete Punkte`. Ein Spieler,
    der nicht aufläuft, bringt 0 — die Startelf-Chance ist deshalb kein Bonus,
    sondern ein Faktor. Fehlt sie (Quellenkette hat nichts geliefert), wird
    konservativ mit 0.5 gewichtet; fehlen die Punkte, zählt der Spieler als 0,
    steht aber weiter zur Verfügung: ein Neuzugang ohne Historie ist immer noch
    hundert Punkte besser als ein leerer Slot.

    Gesperrte und verletzte Spieler sortieren sich nach hinten, statt hart
    gefiltert zu werden — bei einem Kader, der sonst nicht voll wird, ist auch
    ein Zweifelsfall besser als eine Lücke (0 Punkte statt -100). Maßgeblich
    ist dafür der **Status**, nicht die Prognose: die kann bei einem API-Fehler
    komplett fehlen, der Status steht immer im Kaderdatensatz.

    Unter den Kandidaten gewinnt die Formation, die **am meisten Slots besetzt
    bekommt**; bei Gleichstand die mit dem höheren Gesamtscore.
    """
    candidates = [f for f in formation_candidates if f in FORMATIONS] or list(FORMATIONS)
    probabilities = start_probabilities or {}

    scored = sorted(
        squad,
        key=lambda sp: _score(sp, probabilities.get(sp.player.id)),
        reverse=True,
    )

    best: Lineup | None = None
    best_key = (-1, float("-inf"))
    for formation in candidates:
        picked = _fill(scored, formation)
        key = (len(picked), sum(_score(sp, probabilities.get(sp.player.id)) for sp in picked))
        if key > best_key:
            best_key = key
            best = Lineup(formation=formation, player_ids=tuple(sp.player.id for sp in picked))

    assert best is not None  # `candidates` ist nie leer
    return best


def _fill(scored: Sequence[SquadPlayer], formation: str) -> list[SquadPlayer]:
    """Füllt die Slots einer Formation aus der bereits sortierten Kaderliste."""
    slots = slots_for(formation)
    assert slots is not None  # Aufrufer filtert auf bekannte Formationen
    remaining = dict(slots)
    picked: list[SquadPlayer] = []
    for sp in scored:
        if remaining.get(sp.player.position, 0) <= 0:
            continue
        remaining[sp.player.position] -= 1
        picked.append(sp)
    return picked


def _score(sp: SquadPlayer, start_probability: float | None) -> float:
    if sp.player.status in CANNOT_PLAY_STATUSES:
        # Gesperrt oder verletzt heißt 0 Punkte — unabhängig davon, was die
        # Prognose sagt oder ob überhaupt eine vorliegt. Der Spieler bleibt
        # trotzdem wählbar und füllt notfalls einen sonst leeren Slot.
        return 0.0
    # Ohne Prognose: 0.5 — weder als Stammspieler noch als Reservist behandeln.
    probability = 0.5 if start_probability is None else start_probability
    points = sp.player.average_points or 0.0
    return probability * points


__all__ = [
    "CANNOT_PLAY_STATUSES",
    "DEFAULT_FORMATION",
    "FORMATIONS",
    "LINEUP_SIZE",
    "Lineup",
    "best_lineup",
    "slots_for",
    "validate_lineup",
]
