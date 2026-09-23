"""Deterministischer Startelf-Guard — läuft vor jeder LLM-Abfrage.

Die -100-Punkte-Regel wird bewusst **nicht** dem Sprachmodell überlassen (Plan
§6/P0-4). Begründung: Ein leerer Startelf-Slot kostet 100 Punkte, ganz ohne
Gegenleistung, und ob elf Positionen besetzt sind, ist keine Ermessensfrage.
Das Modell darf entscheiden, *welcher* Spieler auf die Bank gehört — dafür gibt
es die Aktion `SET_LINEUP`. Dass überhaupt aufgestellt wird, entscheidet dieser
Guard.

Der Guard greift nur, wenn er etwas verbessert. Zwei Auslöser:

1. **Zu wenige Spieler aufgestellt** — jeder freie Slot ist 100 Punkte wert.
2. **Ein aufgestellter Spieler kann nicht spielen** (verletzt, gesperrt, nicht
   im Kader des Vereins) und ein einsatzfähiger sitzt auf der Bank.

Ist beides nicht der Fall, tut er nichts. Ein Guard, der jeden Tick schreibt,
produziert Rauschen im `trade_log` und Last gegen das Rate-Limit — und beides
ohne einen einzigen zusätzlichen Punkt.

Grenze des Guards: Bei einem Kader unter elf Spielern kann auch er die Elf
nicht voll bekommen. Er stellt dann so viele Slots wie möglich — am
2026-09-23 wären das 8 von 11, also 300 statt 1100 verschenkten Punkten.
Den Rest löst nur ein Kauf, und der ist eine LLM-Entscheidung.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.application.player_enrichment import PlayerEnrichment
from app.domain.lineup import (
    CANNOT_PLAY_STATUSES,
    FORMATIONS,
    Lineup,
    best_lineup,
    validate_lineup,
)
from app.domain.models import SquadPlayer
from app.domain.trade import TradeAction, TradeDecision, TradeIntent

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LineupProposal:
    """Was der Guard ändern würde — plus der Grund, in Klartext."""

    lineup: Lineup
    reason: str
    current_count: int


def propose_lineup_fix(
    *,
    squad: Sequence[SquadPlayer],
    current: Lineup,
    enrichment: Mapping[str, PlayerEnrichment] | None = None,
) -> LineupProposal | None:
    """Schlägt eine bessere Aufstellung vor, oder None, wenn alles in Ordnung ist.

    `current` ist die Aufstellung, die Kickbase meldet. Deren Formation wird
    als erster Kandidat behandelt: Sie ist nachweislich gültig, alle anderen
    sind es nur laut Doku. Eine Formationsänderung ist ein zusätzliches Risiko
    und lohnt nur, wenn sie mehr Slots besetzt bekommt.
    """
    if not squad:
        return None

    probabilities = {pid: e.start_probability_next for pid, e in (enrichment or {}).items()}
    proposal = best_lineup(
        squad,
        start_probabilities=probabilities,
        formation_candidates=(current.formation, *_other_formations(current.formation)),
    )

    errors = validate_lineup(proposal.player_ids, proposal.formation, squad)
    if errors:
        # Sollte nicht vorkommen — `best_lineup` baut entlang derselben Regeln.
        # Wenn doch, ist Nichtstun die sichere Antwort: eine kaputte Aufstellung
        # zu schreiben wäre schlimmer als die bestehende zu lassen.
        _log.error("Guard hat eine ungültige Aufstellung gebaut, verwerfe sie: %s", errors)
        return None

    reason = _reason_to_act(squad=squad, current=current, proposal=proposal)
    if reason is None:
        return None
    return LineupProposal(lineup=proposal, reason=reason, current_count=len(current.player_ids))


def to_decision(proposal: LineupProposal) -> TradeDecision:
    return TradeDecision(
        action=TradeAction.SET_LINEUP,
        reason=proposal.reason,
        intent=TradeIntent.POINTS,
        lineup=proposal.lineup,
    )


def _reason_to_act(
    *, squad: Sequence[SquadPlayer], current: Lineup, proposal: Lineup
) -> str | None:
    """Gibt den Grund zurück, warum geschrieben werden soll — oder None."""
    if len(proposal.player_ids) > len(current.player_ids):
        missing = len(proposal.player_ids) - len(current.player_ids)
        return (
            f"Startelf-Guard: {len(current.player_ids)} von 11 Slots besetzt, "
            f"{missing} weitere besetzbar (je 100 Punkte)."
        )

    by_id = {sp.player.id: sp for sp in squad}
    blocked = [
        pid
        for pid in current.player_ids
        if _cannot_play(by_id.get(pid)) and pid not in proposal.player_ids
    ]
    if blocked:
        return (
            f"Startelf-Guard: {len(blocked)} aufgestellte Spieler können nicht spielen "
            f"({', '.join(blocked)}) und werden durch einsatzfähige ersetzt."
        )
    return None


def _cannot_play(sp: SquadPlayer | None) -> bool:
    return sp is not None and sp.player.status in CANNOT_PLAY_STATUSES


def _other_formations(current: str) -> tuple[str, ...]:
    """Alternativen zur gemeldeten Formation.

    Nur relevant, wenn die aktuelle Formation weniger Slots besetzt bekommt als
    eine andere — etwa wenn der Kader vier Stürmer und zwei Verteidiger hat.
    """
    return tuple(f for f in FORMATIONS if f != current)


__all__ = ["LineupProposal", "propose_lineup_fix", "to_decision"]
