"""LLM-Kurator (ADR-5, Schicht 2) — legt Claude Sonnet über die Heuristik.

Ablauf pro Tick:

1. Heuristik liefert die geordneten Kandidaten (BUY / LIST / SELL / ACCEPT / DECLINE).
2. Fällt die Menge leer aus → sofortiges HOLD, kein LLM-Call (Kosten sparen).
3. Ein synthetischer HOLD-Kandidat wird als gleichwertige Option ergänzt
   (Aggressivitäts-Regel § 7): der Kurator soll HOLD wählen dürfen, ohne die
   Heuristik nachbauen zu müssen.
4. Der LLM bekommt Kontext + Kandidaten-Liste und **muss** über Tool-Use
   genau eine `candidate_id` zurückliefern; optional darf er den von der
   Heuristik gesetzten `intent` (SQUAD_FILL / PROFIT / POINTS / DEBT_RELIEF)
   überschreiben, wenn er einen anderen Motivations-Grund plausibler findet.
5. Bei LLM-Ausfall oder ungültiger Wahl fällt der Kurator auf die
   Heuristik-Entscheidung zurück und markiert das im `reason` — ein
   temporärer Anthropic-Ausfall darf den Auto-Loop nicht anhalten.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.application.decision_engine import DecisionContext
from app.application.heuristic_engine import HeuristicCandidate, HeuristicDecisionEngine
from app.domain.kb_rules import action_threshold_scale
from app.domain.trade import TradeAction, TradeDecision, TradeIntent
from app.infrastructure.llm.anthropic_client import LlmChatError, LlmChatGateway

_log = logging.getLogger(__name__)

_HOLD_CANDIDATE_ID = "HOLD:0"
_TOOL_NAME = "select_action"
_TOOL_DESCRIPTION = (
    "Wähle exakt eine der im Prompt angebotenen candidate_ids und begründe kurz. "
    "Optional: intent überschreiben, wenn der Motivations-Grund von der Heuristik "
    "abweicht (nur für BUY/SELL sinnvoll)."
)
_INTENT_ENUM = [i.value for i in TradeIntent]
_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidate_id": {
            "type": "string",
            "description": (
                "Eine der im Prompt angebotenen candidate_ids (z. B. 'BUY:m1', 'HOLD:0')."
            ),
        },
        "reason": {
            "type": "string",
            "description": "Kurze Begründung in ≤ 240 Zeichen.",
            "maxLength": 240,
        },
        "intent": {
            "type": "string",
            "description": (
                "Optionaler Motivations-Grund für BUY/SELL. "
                "Wenn gesetzt, überschreibt er den Heuristik-Vorschlag."
            ),
            "enum": _INTENT_ENUM,
        },
    },
    "required": ["candidate_id", "reason"],
}

_SYSTEM_PROMPT = (
    "Du bist der Auto-Manager eines einzelnen Kickbase-Bundesliga-Fantasy-Accounts. "
    "Pro Tick darfst du genau EINE Aktion aus der angebotenen Kandidaten-Liste wählen. "
    "HOLD (nichts tun) ist eine vollwertige Option und die bessere Wahl, "
    "wenn keine andere Option klaren Mehrwert (> 5 % Utility-Vorsprung vor HOLD) bringt. "
    "Priorisiere hohe Utility, aber gewichte auch Preis-Effizienz, Form und Marktwert-Trend. "
    "Ein Spieler kann bis zu +15 % über Marktwert geboten werden — nutze das, "
    "wenn du einen Schlüsselkandidaten nicht verlieren willst. "
    "Für Verkäufe gibt es zwei Varianten: LIST_ON_MARKET setzt den Spieler zum "
    "Wunschpreis (~+10 % über Marktwert) auf den Transfermarkt und wartet 24 h "
    "auf Manager-Gebote; SELL verkauft sofort an Kickbase zum Marktwert. Bevorzuge "
    "LIST_ON_MARKET, wenn Zeit da ist und der Aufschlag realistisch aussieht; "
    "wähle SELL, wenn schnelle Liquidität wichtiger ist (Schulden abbauen, "
    "kurz vor Deadline) oder ein Listing bereits abgelaufen ist. "
    "Für jeden BUY/LIST_ON_MARKET/SELL liefert die Heuristik einen intent-Vorschlag "
    "(SQUAD_FILL = Kader füllen, PROFIT = Wertsteigerung realisieren, "
    "POINTS = Punkte sammeln, DEBT_RELIEF = Schulden abbauen). "
    "Übernimm ihn normalerweise; setze `intent` nur, wenn du sicher bist, "
    "dass ein anderer Grund besser passt. "
    "Antworte ausschließlich durch Aufruf des Tools `select_action` mit einer der "
    "angebotenen candidate_ids — keine Freitexte. Erfinde keine candidate_id."
)


@dataclass(frozen=True, slots=True)
class LlmCuratorConfig:
    max_tokens: int = 512


class LlmCurator:
    """Implementiert das `DecisionEngine`-Protokoll (verträglich mit RunTickUseCase)."""

    def __init__(
        self,
        *,
        heuristic: HeuristicDecisionEngine,
        llm: LlmChatGateway,
        api_key: str,
        config: LlmCuratorConfig | None = None,
    ) -> None:
        self._heuristic = heuristic
        self._llm = llm
        self._api_key = api_key
        self._config = config or LlmCuratorConfig()

    async def decide(self, context: DecisionContext) -> TradeDecision:
        candidates = await self._heuristic.propose(context)
        if not candidates:
            return TradeDecision.hold(
                "HOLD: Heuristik hat keinen Kandidaten geliefert — LLM übersprungen."
            )

        scale = action_threshold_scale(
            now=context.now,
            next_matchday_start=context.next_matchday_start,
            interval_min=context.interval_min,
        )
        effective_threshold = context.min_action_score * scale
        hold_candidate = _make_hold_candidate(effective_threshold)
        all_candidates: tuple[HeuristicCandidate, ...] = (*candidates, hold_candidate)
        by_id = {c.id: c for c in all_candidates}

        user_message = _build_user_message(context, all_candidates)

        try:
            tool_input = await self._llm.select_action(
                api_key=self._api_key,
                system_prompt=_SYSTEM_PROMPT,
                user_message=user_message,
                tool_name=_TOOL_NAME,
                tool_description=_TOOL_DESCRIPTION,
                input_schema=_INPUT_SCHEMA,
                max_tokens=self._config.max_tokens,
            )
        except LlmChatError as exc:
            _log.warning("LLM-Call fehlgeschlagen (%s) — Fallback auf Heuristik.", exc)
            return _fallback_from_heuristic(
                candidates[0], reason_prefix=f"LLM-Fallback (Fehler: {exc})"
            )

        candidate_id = tool_input.get("candidate_id")
        llm_reason = str(tool_input.get("reason") or "").strip()
        llm_intent = _parse_intent(tool_input.get("intent"))
        if not isinstance(candidate_id, str) or candidate_id not in by_id:
            _log.warning(
                "LLM lieferte ungültige candidate_id=%r — Fallback auf Heuristik.", candidate_id
            )
            return _fallback_from_heuristic(
                candidates[0],
                reason_prefix=f"LLM-Fallback (ungültige Wahl: {candidate_id!r})",
            )

        chosen = by_id[candidate_id]
        return _decorate_with_llm_reason(chosen.decision, llm_reason, llm_intent)


def _make_hold_candidate(min_action_score: float) -> HeuristicCandidate:
    return HeuristicCandidate(
        id=_HOLD_CANDIDATE_ID,
        utility=min_action_score,
        decision=TradeDecision.hold(f"HOLD: LLM-Wahl (Schwelle {min_action_score:.2f})."),
        summary=(
            f"HOLD: nichts tun; wähle diese Option, wenn keine andere Aktion "
            f"klar über Schwelle {min_action_score:.2f} liegt."
        ),
    )


def _build_user_message(
    context: DecisionContext, candidates: tuple[HeuristicCandidate, ...]
) -> str:
    lines = [
        "Ligastatus:",
        f"- Budget spendable: {int(context.budget)}",
        f"- Kader-Größe: {len(context.squad.players)} / 15",
        f"- Team-Wert: {int(context.team_value)}",
        f"- Schwelle min_action_score: {context.min_action_score:.2f}",
        f"- max_trade_pct: {context.max_trade_pct:.2f}",
        f"- min_cash_reserve: {context.min_cash_reserve}",
        f"- Bekannte PROFIT-Käufe im Kader: {_profit_holding_count(context)}",
        "",
        "Kandidaten (candidate_id | utility | intent | summary):",
    ]
    for c in candidates:
        intent_label = c.decision.intent.value if c.decision.intent is not None else "—"
        lines.append(f"- {c.id} | utility={c.utility:.2f} | intent={intent_label} | {c.summary}")
    lines.append("")
    lines.append(
        "Wähle genau eine candidate_id über das Tool `select_action`, "
        "begründe kurz und setze `intent` nur, wenn du den Heuristik-Vorschlag "
        "überschreiben willst."
    )
    return "\n".join(lines)


def _profit_holding_count(context: DecisionContext) -> int:
    if not context.buy_history:
        return 0
    return sum(1 for rec in context.buy_history.values() if rec.intent is TradeIntent.PROFIT)


def _parse_intent(raw: object) -> TradeIntent | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return TradeIntent(raw)
    except ValueError:
        _log.info("LLM lieferte unbekannten Intent %r — ignoriere.", raw)
        return None


def _fallback_from_heuristic(best: HeuristicCandidate, *, reason_prefix: str) -> TradeDecision:
    original = best.decision
    reason = f"{reason_prefix}. Heuristik-Wahl: {original.reason}"
    if original.action is TradeAction.HOLD:
        return TradeDecision.hold(reason)
    return TradeDecision(
        action=original.action,
        reason=reason,
        player_id=original.player_id,
        player_name=original.player_name,
        price=original.price,
        offer_id=original.offer_id,
        intent=original.intent,
    )


def _decorate_with_llm_reason(
    decision: TradeDecision, llm_reason: str, llm_intent: TradeIntent | None
) -> TradeDecision:
    if not llm_reason and llm_intent is None:
        return decision
    intent = llm_intent or decision.intent
    reason_parts: list[str] = []
    if llm_reason:
        reason_parts.append(f"LLM: {llm_reason}")
    if llm_intent is not None and llm_intent is not decision.intent:
        old = decision.intent.value if decision.intent is not None else "—"
        reason_parts.append(f"Intent-Override: {old} → {llm_intent.value}")
    reason_parts.append(f"Heuristik: {decision.reason}")
    combined = " | ".join(reason_parts)
    if decision.action is TradeAction.HOLD:
        return TradeDecision.hold(combined)
    return TradeDecision(
        action=decision.action,
        reason=combined,
        player_id=decision.player_id,
        player_name=decision.player_name,
        price=decision.price,
        offer_id=decision.offer_id,
        intent=intent,
    )


__all__ = ["LlmCurator", "LlmCuratorConfig"]
