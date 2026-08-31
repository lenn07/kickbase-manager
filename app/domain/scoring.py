"""Heuristik-Scoring (ADR-5, Schicht 1) — pure Funktionen, framework-frei.

Berechnet für einen Spieler einen deterministischen Score in [0, 1] aus vier
Features: Form (`average_points`), Preis-Effizienz (Punkte pro Mio Marktwert),
Marktwert-Trend (Steigung der Historie) und einem Verletzungs-Multiplikator.
Die additiven Feature-Gewichte werden intern normalisiert, damit die Skala
[0, 1] auch bei umkonfigurierten Weights stabil bleibt.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.models import MarketValuePoint, Player, PlayerStatus

_FORM_SATURATION_POINTS = 12.0
_PRICE_EFFICIENCY_SATURATION = 4.0
_PRICE_EFFICIENCY_MIN_MILLION = 0.1
_TREND_MIN_HISTORY_POINTS = 2
_TREND_HALF_RANGE = 0.10


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    form: float = 0.45
    price_efficiency: float = 0.30
    market_trend: float = 0.25

    def __post_init__(self) -> None:
        total = self.form + self.price_efficiency + self.market_trend
        if total <= 0:
            raise ValueError("ScoreWeights: mindestens ein Gewicht muss > 0 sein.")


@dataclass(frozen=True, slots=True)
class ScoreFeatures:
    form: float
    price_efficiency: float
    market_trend: float
    injury_multiplier: float


def form_score(player: Player) -> float:
    """Normalisiert `average_points` auf [0, 1]; 12 avg Punkte = 1.0."""
    return _clip01(player.average_points / _FORM_SATURATION_POINTS)


def price_efficiency_score(average_points: float, price: Decimal) -> float:
    """Punkte pro 1 Mio € Marktwert, auf [0, 1] normalisiert.

    Ein Spieler mit 4 avg Punkten pro Mio € Preis erreicht 1.0 — darüber wird
    geclippt. Sehr günstige Bank-Spieler (Preis < 100 k) bekommen 0, weil deren
    Kennzahl instabil ist und keine sinnvolle Entscheidung stützt.
    """
    price_million = float(price) / 1_000_000.0
    if price_million < _PRICE_EFFICIENCY_MIN_MILLION:
        return 0.0
    return _clip01((average_points / price_million) / _PRICE_EFFICIENCY_SATURATION)


def market_trend_score(history: list[MarketValuePoint]) -> float:
    """Relative Wertänderung zwischen frühestem und jüngstem Punkt.

    Ohne Historie geben wir einen neutralen 0.5 zurück — das darf niemanden
    weder bevorzugen noch benachteiligen. ±10 % Änderung entsprechen den
    Skalen-Enden 0 bzw. 1; alles darüber wird geklemmt.
    """
    if len(history) < _TREND_MIN_HISTORY_POINTS:
        return 0.5
    sorted_hist = sorted(history, key=lambda p: p.day)
    first = float(sorted_hist[0].value)
    last = float(sorted_hist[-1].value)
    if first <= 0:
        return 0.5
    change = (last - first) / first
    normalized = 0.5 + (change / (2 * _TREND_HALF_RANGE))
    return _clip01(normalized)


def injury_multiplier(status: PlayerStatus) -> float:
    """Weicher Multiplikator — harte Filter (z. B. NOT_IN_TEAM) macht die Engine."""
    match status:
        case PlayerStatus.FIT:
            return 1.0
        case PlayerStatus.REHAB:
            return 0.7
        case PlayerStatus.INJURED:
            return 0.3
        case PlayerStatus.RED_CARD | PlayerStatus.YELLOW_RED_CARD:
            return 0.4
        case PlayerStatus.OUT_OF_SQUAD | PlayerStatus.NOT_IN_TEAM:
            return 0.1
        case _:
            return 0.5


def compute_features(
    player: Player, price: Decimal, history: list[MarketValuePoint]
) -> ScoreFeatures:
    return ScoreFeatures(
        form=form_score(player),
        price_efficiency=price_efficiency_score(player.average_points, price),
        market_trend=market_trend_score(history),
        injury_multiplier=injury_multiplier(player.status),
    )


def compose_score(features: ScoreFeatures, weights: ScoreWeights) -> float:
    """Additive Kombination der Feature-Terme, gedämpft um Verletzungs-Malus."""
    total_weight = weights.form + weights.price_efficiency + weights.market_trend
    additive = (
        weights.form * features.form
        + weights.price_efficiency * features.price_efficiency
        + weights.market_trend * features.market_trend
    ) / total_weight
    return _clip01(additive * features.injury_multiplier)


def _clip01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
