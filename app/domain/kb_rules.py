"""Kickbase-Regel-Modifikatoren für die Entscheidungs-Engine.

Reine Funktionen, framework-frei. Bilden die offiziellen Kickbase-Regeln aus
`help.kickbase.com` als weiche Utility-Anpassungen ab — keine harten Blockaden.

Regeln:
- **Konto-Minus zum Anpfiff**: negativer Kontostand bei Spieltagsstart → 0 Punkte
  für den Spieltag. Aktionen, die einen negativen Kontostand entschärfen,
  bekommen Bonus; Käufe, die weiter ins Minus treiben, Malus.
- **33 %-Regel**: Erlaubtes Minus = 33 % * (Mannschaftswert + Kontostand).
  Auch offene BUY-Gebote zählen — Kickbase blockt Gebote sonst.
- **Startelf 11**: -100 Punkte pro unbesetzter Position. Verkäufe, die den
  Squad unter 11 verkaufbare Spieler treiben, bekommen starken Malus.
- **Positions-Mindestbesetzung**: Kickbase-Formationen brauchen 1 GK, ≥3
  DEF, ≥3 MID, ≥1 ATT — ohne diese Mindestwerte lässt sich keine gültige
  Aufstellung setzen, jede Lücke gibt -100 Pkt. Verkäufe, die eine Position
  unter die Mindestschwelle drücken, werden hart bestraft; Käufe, die eine
  untervertretene Position auffüllen, bekommen einen kleinen Bonus.
- **GK-Overstock**: Nur ein Torwart spielt gleichzeitig. Ein zweiter GK-Kauf
  bringt zunächst nichts — außer als PROFIT-Karte für späteren Weiterverkauf.
  Die Regel dämpft solche Käufe, damit nur klare PROFIT-Kandidaten durchgehen.
- **Deadline**: Änderungen nach Spieltagsstart greifen erst am nächsten
  Spieltag → Aktionen kurz vor Deadline sind zeitkritisch (Bonus für
  Debt-Relief, leichter Malus für spekulative Käufe).

Der Modifikator wird als `RuleAdjustment.delta ∈ [-1, +1]` additiv auf die
Basis-Utility angewandt; die Engine klippt anschließend auf [0, 1].
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.domain.models import Position

_MIN_STARTING_XI = 11
_MAX_SQUAD_SIZE = 15
_MINIMUM_HEADROOM_RATIO = 0.33
_DEADLINE_LOCK_WINDOW = timedelta(hours=2)
_CALM_TICK_COUNT = 10
_URGENCY_FLOOR = 0.5

# Mindestanzahl pro Position, damit sich eine gültige Kickbase-Formation
# aufstellen lässt (z. B. 5-3-2, 3-4-3, 4-5-1 → immer ≥1 GK, ≥3 DEF, ≥3 MID,
# ≥1 ATT). Fehlt in einer Position der Bedarf, gibt Kickbase -100 Pkt./Loch.
POSITION_MINIMUMS: Mapping[Position, int] = {
    Position.GOALKEEPER: 1,
    Position.DEFENDER: 3,
    Position.MIDFIELDER: 3,
    Position.FORWARD: 1,
}

_POSITION_LABELS: Mapping[Position, str] = {
    Position.GOALKEEPER: "GK",
    Position.DEFENDER: "DEF",
    Position.MIDFIELDER: "MID",
    Position.FORWARD: "ATT",
}

# Nur ein Torwart steht gleichzeitig in der Startelf — ein zweiter GK bringt
# als Ersatz nichts, außer man kauft ihn als PROFIT-Karte. Der Malus dämpft
# solche Käufe, ohne sie hart zu blockieren.
_GK_OVERSTOCK_MALUS = -0.2
_POSITION_NEED_BONUS = 0.1
# Bonus für den Verkauf eines Spielers auf überbesetzter Position — je stärker
# überbesetzt, desto besser räumt man Kader-Platz für sinnvollere Zugänge.
_POSITION_SURPLUS_BASE_BONUS = 0.05
_POSITION_SURPLUS_STEP_BONUS = 0.05
_POSITION_SURPLUS_MAX_BONUS = 0.20


@dataclass(frozen=True, slots=True)
class RuleAdjustment:
    """Signierter Utility-Delta plus Begründungs-Fragmente für den Log-Reason."""

    delta: float
    reasons: tuple[str, ...] = ()

    @staticmethod
    def neutral() -> RuleAdjustment:
        return RuleAdjustment(delta=0.0, reasons=())

    def merged(self, other: RuleAdjustment) -> RuleAdjustment:
        return RuleAdjustment(
            delta=self.delta + other.delta,
            reasons=self.reasons + other.reasons,
        )


def negative_budget_limit(team_value: Decimal, budget: Decimal) -> Decimal:
    """Maximales erlaubtes Minus laut 33 %-Regel.

    Kickbase: „33 % deines Mannschaftswerts plus den Betrag, der sich auf
    deinem Konto befindet". Bei negativem Kontostand wird der Mannschaftswert
    effektiv verkleinert (Beispiel im Hilfe-Artikel: TV 100 M, Kto -10 M →
    (100 - 10) * 33 % = 30 M erlaubtes Minus).
    """
    effective_base = team_value + min(budget, Decimal(0))
    if effective_base <= 0:
        return Decimal(0)
    return effective_base * Decimal(str(_MINIMUM_HEADROOM_RATIO))


def evaluate_buy(
    *,
    budget: Decimal,
    team_value: Decimal,
    open_bids_total: Decimal,
    buy_price: Decimal,
    squad_size: int,
    now: datetime | None = None,
    next_matchday_start: datetime | None = None,
    squad_positions: Mapping[Position, int] | None = None,
    bought_position: Position | None = None,
) -> RuleAdjustment:
    """Bewertet einen BUY gegen Konto-, Squad-, Positions- und Deadline-Regeln."""

    adjustment = RuleAdjustment.neutral()

    # 33 %-Regel: neuer Kontostand darf `-limit` nicht unterschreiten. Offene
    # Gebote werden mitgerechnet, weil Kickbase sonst blockt.
    projected = budget - open_bids_total - buy_price
    limit = negative_budget_limit(team_value, budget)
    if projected < -limit:
        overshoot = float(-projected - limit)
        limit_ref = float(limit) if limit > 0 else float(buy_price)
        # Skala: 100 % Überschreitung → volle Blockade (-1.0).
        malus = min(1.0, overshoot / max(limit_ref, 1.0))
        adjustment = adjustment.merged(
            RuleAdjustment(
                delta=-malus,
                reasons=(f"33 %-Regel: Kauf bricht Minus-Limit um {int(overshoot):,}.",),
            )
        )

    # Squad-Cap: Kickbase erlaubt max. 15 Spieler.
    if squad_size + 1 > _MAX_SQUAD_SIZE:
        adjustment = adjustment.merged(
            RuleAdjustment(
                delta=-1.0,
                reasons=(f"Squad-Cap: schon {squad_size}/{_MAX_SQUAD_SIZE} Spieler.",),
            )
        )

    # Positions-Regeln: GK-Overstock dämpfen (nur 1 GK spielt gleichzeitig),
    # untervertretene Positionen dagegen belohnen. Ohne Positions-Kontext
    # (ältere Aufrufe / Tests) bleibt der Modifikator inaktiv.
    if squad_positions is not None and bought_position is not None:
        current = squad_positions.get(bought_position, 0)
        label = _POSITION_LABELS[bought_position]
        minimum = POSITION_MINIMUMS[bought_position]
        if bought_position is Position.GOALKEEPER and current >= 1:
            adjustment = adjustment.merged(
                RuleAdjustment(
                    delta=_GK_OVERSTOCK_MALUS,
                    reasons=(
                        f"GK-Overstock: schon {current} Torwart im Kader — nur klare "
                        f"PROFIT-Kandidaten lohnen als 2. GK (später wieder verkaufen).",
                    ),
                )
            )
        elif current < minimum:
            adjustment = adjustment.merged(
                RuleAdjustment(
                    delta=_POSITION_NEED_BONUS,
                    reasons=(
                        f"Positions-Bedarf: nur {current}/{minimum} {label} — Kauf füllt "
                        f"Startelf-Lücke (verhindert -100 Pkt.).",
                    ),
                )
            )

    # Deadline-Fenster: nahe Spieltagsstart sind Käufe risikoreich, weil
    # Änderungen erst nächsten Spieltag greifen und Preise volatil sind.
    if _within_deadline_window(now, next_matchday_start):
        adjustment = adjustment.merged(
            RuleAdjustment(
                delta=-0.1,
                reasons=("Deadline-Fenster: Kauf wirkt erst nächsten Spieltag.",),
            )
        )

    return adjustment


def evaluate_sell(
    *,
    budget: Decimal,
    sell_price: Decimal,
    squad_size: int,
    now: datetime | None = None,
    next_matchday_start: datetime | None = None,
    squad_positions: Mapping[Position, int] | None = None,
    sold_position: Position | None = None,
) -> RuleAdjustment:
    """Bewertet einen SELL gegen Konto-, Squad-, Positions- und Deadline-Regeln."""

    adjustment = RuleAdjustment.neutral()

    # Positions-Loch: erste Prüfung, weil ein unbesetzter GK/DEF/MID/ATT-Slot
    # in der Startelf jeden anderen Bonus zunichtemacht (-100 Pkt./Loch). Wird
    # nur ausgewertet, wenn der Aufrufer Positions-Kontext liefert.
    if squad_positions is not None and sold_position is not None:
        current = squad_positions.get(sold_position, 0)
        remaining = current - 1
        minimum = POSITION_MINIMUMS[sold_position]
        label = _POSITION_LABELS[sold_position]
        if remaining < minimum:
            adjustment = adjustment.merged(
                RuleAdjustment(
                    delta=-1.0,
                    reasons=(
                        f"Positions-Loch: nach Verkauf nur {max(remaining, 0)}/{minimum} "
                        f"{label} — Startelf unvollständig, -100 Pkt./Loch.",
                    ),
                )
            )
        else:
            # Bonus greift, sobald die Position aktuell überbesetzt ist —
            # nicht erst, wenn nach dem Verkauf noch Überschuss bleibt.
            # Rationale: bei 2 GK spielt trotzdem nur einer, der zweite ist
            # Kader-Ballast, den man loswerden oder als PROFIT-Karte
            # verkaufen sollte (Kickbase erlaubt max. 15 Slots insgesamt).
            surplus = current - minimum
            if surplus > 0:
                bonus = min(
                    _POSITION_SURPLUS_MAX_BONUS,
                    _POSITION_SURPLUS_BASE_BONUS + (surplus - 1) * _POSITION_SURPLUS_STEP_BONUS,
                )
                adjustment = adjustment.merged(
                    RuleAdjustment(
                        delta=bonus,
                        reasons=(
                            f"Positions-Überschuss: {current} {label} im Kader "
                            f"(Mindest {minimum}) — Verkauf entlastet Slot, ohne "
                            f"Startelf zu gefährden.",
                        ),
                    )
                )

    # Debt-Relief: Konto war negativ, Verkauf hebt es zurück ins Plus → hoher Bonus.
    if budget < 0:
        projected = budget + sell_price
        if projected >= 0:
            adjustment = adjustment.merged(
                RuleAdjustment(
                    delta=0.35,
                    reasons=("Debt-Relief: Verkauf hebt Konto zurück ins Plus.",),
                )
            )
        else:
            adjustment = adjustment.merged(
                RuleAdjustment(
                    delta=0.15,
                    reasons=("Debt-Relief: Verkauf reduziert negatives Konto.",),
                )
            )

    # Startelf-Reserve: unter 11 verkaufbaren Spielern → -100 Pkt./fehlende Position.
    new_size = squad_size - 1
    if new_size < _MIN_STARTING_XI:
        adjustment = adjustment.merged(
            RuleAdjustment(
                delta=-1.0,
                reasons=(f"Startelf: Verkauf lässt nur {new_size} Spieler — -100 Pkt./Loch.",),
            )
        )
    elif new_size == _MIN_STARTING_XI:
        adjustment = adjustment.merged(
            RuleAdjustment(
                delta=-0.3,
                reasons=("Startelf: kein Ausfall-Puffer mehr nach Verkauf.",),
            )
        )
    elif new_size == _MIN_STARTING_XI + 1:
        adjustment = adjustment.merged(
            RuleAdjustment(
                delta=-0.1,
                reasons=("Startelf: nur noch 1 Reservist nach Verkauf.",),
            )
        )

    # Deadline-Fenster: Debt-Relief ist hier besonders zeitkritisch.
    if _within_deadline_window(now, next_matchday_start) and budget < 0:
        adjustment = adjustment.merged(
            RuleAdjustment(
                delta=0.15,
                reasons=("Deadline-Fenster: Debt-Relief vor Anpfiff dringend.",),
            )
        )

    return adjustment


def evaluate_accept_offer(
    *,
    budget: Decimal,
    offer_price: Decimal,
    squad_size: int,
    now: datetime | None = None,
    next_matchday_start: datetime | None = None,
    squad_positions: Mapping[Position, int] | None = None,
    sold_position: Position | None = None,
) -> RuleAdjustment:
    """Angenommenes Angebot verhält sich squad-technisch wie ein SELL."""

    return evaluate_sell(
        budget=budget,
        sell_price=offer_price,
        squad_size=squad_size,
        now=now,
        next_matchday_start=next_matchday_start,
        squad_positions=squad_positions,
        sold_position=sold_position,
    )


def action_threshold_scale(
    *,
    now: datetime | None,
    next_matchday_start: datetime | None,
    interval_min: int,
    calm_tick_count: int = _CALM_TICK_COUNT,
    urgency_floor: float = _URGENCY_FLOOR,
) -> float:
    """Skaliert `min_action_score` nach verbleibenden Ticks bis Anpfiff.

    Bei `calm_tick_count` oder mehr verbleibenden Scheduler-Läufen bleibt die
    Schwelle unverändert (Faktor 1.0). Je weniger Ticks noch reinpassen, desto
    weiter sinkt die Schwelle linear bis `urgency_floor` (Default 0.5 → letzter
    Tick vor Anpfiff nutzt nur noch die halbe Basis-Schwelle). Ohne bekanntes
    Deadline-/Intervall-Signal bleibt der Faktor bei 1.0 — die Engine verhält
    sich dann wie bisher.

    Nach Spieltagsstart (`remaining ≤ 0`) greift bewusst kein Effekt: laut
    Kickbase-Regel wirken Aktionen erst am nächsten Spieltag, dann sollen die
    Ticks im nächsten Zyklus wieder von oben zählen.
    """
    if now is None or next_matchday_start is None or interval_min <= 0:
        return 1.0
    remaining_seconds = (next_matchday_start - now).total_seconds()
    if remaining_seconds <= 0:
        return 1.0
    remaining_ticks = remaining_seconds / (interval_min * 60)
    if remaining_ticks >= calm_tick_count:
        return 1.0
    ratio = remaining_ticks / calm_tick_count
    return urgency_floor + (1.0 - urgency_floor) * ratio


def apply_delta(base_utility: float, delta: float) -> float:
    """Additive Modifikation mit Klippen auf [0, 1]."""

    result = base_utility + delta
    if result < 0.0:
        return 0.0
    if result > 1.0:
        return 1.0
    return result


def _within_deadline_window(now: datetime | None, next_matchday_start: datetime | None) -> bool:
    if now is None or next_matchday_start is None:
        return False
    remaining = next_matchday_start - now
    return timedelta(0) <= remaining <= _DEADLINE_LOCK_WINDOW


__all__ = [
    "POSITION_MINIMUMS",
    "RuleAdjustment",
    "action_threshold_scale",
    "apply_delta",
    "evaluate_accept_offer",
    "evaluate_buy",
    "evaluate_sell",
    "negative_budget_limit",
]
