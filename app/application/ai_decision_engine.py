"""AI-Only-Entscheidungs-Engine (Master-Prompt-Modus).

Ablauf pro Tick:

1. `DecisionContext` wird 1:1 in das USER-JSON serialisiert, das der
   Master-Prompt spezifiziert (`docs/master_prompt.md`, Abschnitt „USER").
2. Der Master-Prompt-System-Text wird via `AnthropicClient.submit_decision`
   als Cache-Prefix an Claude Sonnet geschickt — das LLM entscheidet Aktion,
   Preis, Timing und Intent eigenständig.
3. Das Tool-Use-Ergebnis (`submit_decision`-Tool-Input) wird geparst,
   validiert und in eine `TradeDecision` gemappt. `SELL_LIST` /
   `SELL_INSTANT` aus dem Master-Prompt entsprechen den Domain-Aktionen
   `LIST_ON_MARKET` / `SELL`.
4. Bei LLM-Fehlern oder ungültigem Output fällt die Engine auf HOLD mit
   ausführlichem Grund zurück — kein Heuristik-Fallback (der Master-Prompt
   ist explizit AI-Only).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.application.decision_engine import DecisionContext, RecentAction
from app.application.master_prompt_loader import (
    RULES_LAST_VERIFIED,
    MasterPromptError,
    get_cached_system_prompt,
)
from app.domain.lineup import FORMATIONS, LINEUP_SIZE, Lineup
from app.domain.models import (
    MarketOffer,
    MarketPlayer,
    Position,
    SquadPlayer,
)
from app.domain.trade import TradeAction, TradeDecision, TradeIntent
from app.infrastructure.llm.anthropic_client import LlmChatError, LlmChatGateway

_log = logging.getLogger(__name__)


_TOOL_NAME = "submit_decision"
_TOOL_DESCRIPTION = (
    "Melde deine einzige Aktion für diesen Tick als strukturiertes Objekt. "
    "Alle Regeln aus dem System-Prompt gelten. Keine Freitexte, kein Chat."
)

_PROMPT_ACTIONS = (
    "BUY",
    "SELL_LIST",
    "SELL_INSTANT",
    "ACCEPT_OFFER",
    "DECLINE_OFFER",
    "SET_LINEUP",
    "HOLD",
)
_PROMPT_INTENTS = ("SQUAD_FILL", "PROFIT", "POINTS", "DEBT_RELIEF", "NONE")

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(_PROMPT_ACTIONS)},
        "player_id": {
            "type": "string",
            "description": (
                "Kickbase-Spieler-ID für BUY/SELL_LIST/SELL_INSTANT/ACCEPT/DECLINE; sonst leer."
            ),
        },
        "offer_id": {
            "type": "string",
            "description": (
                "ID des angenommenen/abgelehnten Gebots (nur ACCEPT_OFFER/DECLINE_OFFER)."
            ),
        },
        "price": {"type": "integer", "minimum": 0},
        "intent": {"type": "string", "enum": list(_PROMPT_INTENTS)},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reason_short": {"type": "string", "maxLength": 140},
        "reason_long": {"type": "string"},
        "expected_outcome": {
            "type": "object",
            "properties": {
                "points_delta_next_matchday": {"type": "integer"},
                "profit_estimate": {"type": "integer"},
                "balance_after_action": {"type": "integer"},
                "balance_after_open_bids": {"type": "integer"},
            },
            "required": [
                "points_delta_next_matchday",
                "profit_estimate",
                "balance_after_action",
                "balance_after_open_bids",
            ],
        },
        "risk_flags": {"type": "array", "items": {"type": "string"}},
        "lineup": {
            "type": "object",
            "description": (
                "Nur bei SET_LINEUP: gewünschte Aufstellung. `formation` muss eine der "
                "Formationen aus `lineup.allowed_formations` sein, `player_ids` enthält die "
                "Startelf in Slot-Reihenfolge (Torwart zuerst). Höchstens 11 IDs, alle aus dem "
                "eigenen Kader. Der Code prüft das und verwirft ungültige Aufstellungen."
            ),
            "properties": {
                "formation": {"type": "string"},
                "player_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["formation", "player_ids"],
        },
    },
    "required": [
        "action",
        "price",
        "intent",
        "confidence",
        "reason_short",
        "reason_long",
        "expected_outcome",
        "risk_flags",
    ],
}


# Master-Prompt → Domain-Enum-Mapping.
_ACTION_MAP: dict[str, TradeAction] = {
    "BUY": TradeAction.BUY,
    "SELL_LIST": TradeAction.LIST_ON_MARKET,
    "SELL_INSTANT": TradeAction.SELL,
    "ACCEPT_OFFER": TradeAction.ACCEPT_OFFER,
    "DECLINE_OFFER": TradeAction.DECLINE_OFFER,
    "SET_LINEUP": TradeAction.SET_LINEUP,
    "HOLD": TradeAction.HOLD,
}


_POSITION_LABELS: dict[Position, str] = {
    Position.GOALKEEPER: "TW",
    Position.DEFENDER: "DEF",
    Position.MIDFIELDER: "MID",
    Position.FORWARD: "STK",
}


@dataclass(frozen=True, slots=True)
class AiDecisionConfig:
    max_tokens: int = 1024


class AiDecisionEngine:
    """AI-Only-Entscheidungs-Engine — implementiert das `DecisionEngine`-Protokoll."""

    def __init__(
        self,
        *,
        llm: LlmChatGateway,
        api_key: str,
        config: AiDecisionConfig | None = None,
    ) -> None:
        self._llm = llm
        self._api_key = api_key
        self._config = config or AiDecisionConfig()

    async def decide(self, context: DecisionContext) -> TradeDecision:
        try:
            system_prompt = get_cached_system_prompt()
        except MasterPromptError as exc:
            _log.error("Master-Prompt nicht ladbar: %s — Tick als HOLD.", exc)
            return TradeDecision.hold(f"AI-Only-Modus inaktiv: {exc}")

        user_message = json.dumps(
            _build_user_payload(context),
            ensure_ascii=False,
            separators=(",", ":"),
        )

        try:
            tool_input = await self._llm.submit_decision(
                api_key=self._api_key,
                system_prompt=system_prompt,
                user_message=user_message,
                tool_name=_TOOL_NAME,
                tool_description=_TOOL_DESCRIPTION,
                input_schema=_INPUT_SCHEMA,
                max_tokens=self._config.max_tokens,
            )
        except LlmChatError as exc:
            _log.warning("Anthropic-Call fehlgeschlagen (%s) — HOLD.", exc)
            return TradeDecision.hold(f"AI-Only-Fallback (LLM-Fehler): {exc}")

        try:
            return _parse_decision(tool_input, context)
        except _InvalidDecisionError as exc:
            _log.warning("LLM-Response ungültig (%s) — HOLD. Payload: %r", exc, tool_input)
            return TradeDecision.hold(f"AI-Only-Fallback (ungültige Antwort): {exc}")


class _InvalidDecisionError(ValueError):
    """LLM-Antwort verletzt die Konsistenz-Erwartungen des Domain-Modells."""


def _parse_decision(tool_input: dict[str, Any], context: DecisionContext) -> TradeDecision:
    raw_action = tool_input.get("action")
    if not isinstance(raw_action, str) or raw_action not in _ACTION_MAP:
        raise _InvalidDecisionError(f"unbekannte Aktion: {raw_action!r}")
    action = _ACTION_MAP[raw_action]

    player_id = _clean_optional_str(tool_input.get("player_id"))
    offer_id = _clean_optional_str(tool_input.get("offer_id"))
    price = _coerce_price(tool_input.get("price"))
    intent = _parse_intent(tool_input.get("intent"))
    reason = _compose_reason(tool_input)

    _validate_action_shape(action, player_id, offer_id, price)
    _validate_against_context(action, player_id, offer_id, context)

    if action is TradeAction.HOLD:
        return TradeDecision.hold(reason)

    player_name = _lookup_player_name(context, player_id)
    return TradeDecision(
        action=action,
        reason=reason,
        player_id=player_id,
        player_name=player_name,
        price=price if action in {TradeAction.BUY, TradeAction.LIST_ON_MARKET} else None,
        offer_id=offer_id,
        intent=intent,
        lineup=_parse_lineup(tool_input) if action is TradeAction.SET_LINEUP else None,
    )


def _parse_lineup(tool_input: dict[str, Any]) -> Lineup:
    """Liest den `lineup`-Block. Wirft, wenn er fehlt oder unbrauchbar ist.

    Inhaltliche Prüfung (Formation gültig, IDs im Kader, Positionen passen)
    macht der Executor — hier geht es nur um die Form. Ein `SET_LINEUP` ohne
    Aufstellung ist ein verlorener Tick und muss als solcher auffallen.
    """
    raw = tool_input.get("lineup")
    if not isinstance(raw, dict):
        raise _InvalidDecisionError("SET_LINEUP ohne `lineup`-Block")
    formation = _clean_optional_str(raw.get("formation"))
    if not formation:
        raise _InvalidDecisionError("SET_LINEUP ohne Formation")
    raw_ids = raw.get("player_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise _InvalidDecisionError("SET_LINEUP ohne Spieler-IDs")
    player_ids = tuple(str(pid).strip() for pid in raw_ids if str(pid).strip())
    if not player_ids:
        raise _InvalidDecisionError("SET_LINEUP mit ausschließlich leeren Spieler-IDs")
    return Lineup(formation=formation, player_ids=player_ids)


_PRICE_REQUIRED = {TradeAction.BUY, TradeAction.LIST_ON_MARKET}
_PLAYER_REQUIRED = {
    TradeAction.BUY,
    TradeAction.LIST_ON_MARKET,
    TradeAction.SELL,
    TradeAction.ACCEPT_OFFER,
    TradeAction.DECLINE_OFFER,
}
_OFFER_REQUIRED = {TradeAction.ACCEPT_OFFER, TradeAction.DECLINE_OFFER}
# `SET_LINEUP` betrifft die ganze Elf, nicht einen Spieler — `player_id` und
# `price` bleiben leer, geprüft wird stattdessen der `lineup`-Block.


def _validate_action_shape(
    action: TradeAction,
    player_id: str | None,
    offer_id: str | None,
    price: Decimal | None,
) -> None:
    if action in _PLAYER_REQUIRED and not player_id:
        raise _InvalidDecisionError(f"{action.value} ohne player_id")
    if action in _PRICE_REQUIRED and (price is None or price <= 0):
        raise _InvalidDecisionError(f"{action.value} ohne gültigen Preis")
    if action in _OFFER_REQUIRED and not offer_id:
        raise _InvalidDecisionError(f"{action.value} ohne offer_id")


def _validate_against_context(
    action: TradeAction,
    player_id: str | None,
    offer_id: str | None,
    context: DecisionContext,
) -> None:
    """Prüft die vom Modell genannten IDs gegen den Kontext, den es bekommen hat.

    Ein Sprachmodell kann eine plausible ID erfinden. Ohne diese Prüfung geht
    sie an Kickbase — `ACCEPT_OFFER` mit einer erfundenen `offer_id` ist der
    teuerste Fall: der Tick ist verbraucht, der Fehler steht als Executor-Error
    im Log, und niemand weiß, ob das Gebot nun angenommen wurde.

    Solange der Feldname des Gebots-Arrays offen ist (Plan §8/F1), ist
    `incoming_offers` **immer** leer. Damit sperrt diese Prüfung
    `ACCEPT_OFFER`/`DECLINE_OFFER` automatisch — das ist die Absicherung, die
    der Plan bei P0-2 als „vorher nicht scharf schalten" beschreibt, hier als
    Code statt als Merkzettel. Sobald echte Gebote im Payload stehen, öffnet
    sie sich von selbst.
    """
    if action in _OFFER_REQUIRED:
        known = {offer.id for mp in context.market for offer in mp.offers}
        if offer_id not in known:
            raise _InvalidDecisionError(
                f"{action.value} mit unbekannter offer_id {offer_id!r} — im Kontext standen "
                f"{len(known)} Gebote. Ein Gebot, das der Bot nicht gesehen hat, darf er nicht "
                "annehmen oder ablehnen."
            )
        return

    if action is TradeAction.BUY:
        if player_id not in {mp.player.id for mp in context.market}:
            raise _InvalidDecisionError(f"BUY auf Spieler {player_id!r}, der nicht am Markt ist")
        return

    in_squad = player_id in {sp.player.id for sp in context.squad.players}
    if action in {TradeAction.SELL, TradeAction.LIST_ON_MARKET} and not in_squad:
        raise _InvalidDecisionError(
            f"{action.value} für Spieler {player_id!r}, der nicht im Kader steht"
        )


def _compose_reason(tool_input: dict[str, Any]) -> str:
    short = str(tool_input.get("reason_short") or "").strip()
    long = str(tool_input.get("reason_long") or "").strip()
    if short and long:
        return f"{short} | {long}"
    return short or long or "keine Begründung geliefert"


def _parse_intent(raw: object) -> TradeIntent | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    if raw == "NONE":
        return None
    try:
        return TradeIntent(raw)
    except ValueError:
        _log.info("LLM lieferte unbekannten Intent %r — ignoriere.", raw)
        return None


def _coerce_price(raw: object) -> Decimal | None:
    # bool erbt von int → gesondert ausfiltern, sonst würde `True` als 1 gelten.
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return Decimal(int(raw))
    if not isinstance(raw, str):
        return None
    cleaned = raw.replace("_", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except (ValueError, ArithmeticError):
        return None


def _clean_optional_str(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


def _lookup_player_name(context: DecisionContext, player_id: str | None) -> str | None:
    if not player_id:
        return None
    for sp in context.squad.players:
        if sp.player.id == player_id:
            return _full_name(sp)
    for mp in context.market:
        if mp.player.id == player_id:
            return _full_name(mp)
    return None


def _full_name(holder: SquadPlayer | MarketPlayer) -> str:
    player = holder.player
    parts = [p for p in (player.first_name, player.last_name) if p]
    return " ".join(parts).strip() or player.id


# -- USER-JSON-Serializer ------------------------------------------------


def _build_user_payload(context: DecisionContext) -> dict[str, Any]:
    now = context.now or datetime.now(UTC)
    next_start = context.next_matchday_start
    ticks_until, minutes_until = _time_until(now, next_start, context.interval_min)

    starting_xi_count = sum(1 for sp in context.squad.players if _is_starting_xi(sp))
    payload: dict[str, Any] = {
        "now_iso": _to_iso(now),
        "next_matchday_start_iso": _to_iso(next_start) if next_start else None,
        "ticks_until_matchday_start": ticks_until,
        "minutes_until_matchday_start": minutes_until,
        "mv_update_at_iso": _to_iso(context.mv_update_at) if context.mv_update_at else None,
        "minutes_until_mv_update": _minutes_until(now, context.mv_update_at),
        "rules_last_verified": RULES_LAST_VERIFIED.isoformat(),
        "budget": _budget_block(context),
        "squad": [_squad_entry(sp, context) for sp in context.squad.players],
        "squad_size": len(context.squad.players),
        "starting_xi_count": starting_xi_count,
        "market": [_market_entry(mp, context, now) for mp in context.market],
        "lineup": _lineup_block(context, now),
        "incoming_offers": _incoming_offers(context),
        "recent_actions": [_recent_action(a) for a in context.recent_actions],
        "constraints": {
            "min_cash_reserve": int(context.min_cash_reserve),
            "max_trade_pct": context.max_trade_pct,
            "blacklist": list(context.blacklist),
            "interval_min": context.interval_min,
        },
    }
    return payload


def _lineup_block(context: DecisionContext, now: datetime) -> dict[str, Any]:
    """Alles, was das Modell für eine gültige `SET_LINEUP`-Aktion braucht.

    Ohne `allowed_formations` müsste es die Positionskontingente raten; ohne
    `empty_slots` kann es nicht abschätzen, was Nichtstun kostet — 100 Punkte
    pro Slot, also der teuerste Posten im ganzen Payload.
    """
    lineup = context.lineup
    placed = len(lineup.player_ids) if lineup else 0
    deadline = context.lineup_deadline
    return {
        "formation": lineup.formation if lineup else None,
        "player_ids": list(lineup.player_ids) if lineup else [],
        "placed_count": placed,
        "empty_slots": max(0, LINEUP_SIZE - placed),
        "points_at_risk": max(0, LINEUP_SIZE - placed) * 100,
        "allowed_formations": sorted(FORMATIONS),
        "deadline_iso": _to_iso(deadline) if deadline else None,
        "minutes_until_deadline": _minutes_until(now, deadline),
    }


def _budget_block(context: DecisionContext) -> dict[str, Any]:
    return {
        "cash": _int(context.budget),
        "team_value": _int(context.team_value),
        "open_bids_total": _int(context.open_bids_total),
        "max_negative_allowed": _int(context.max_negative_allowed),
        "current_balance_after_open_bids": _int(context.current_balance_after_open_bids),
    }


def _squad_entry(sp: SquadPlayer, context: DecisionContext) -> dict[str, Any]:
    player = sp.player
    enrichment = context.enrichment.get(player.id)
    buy = context.buy_history.get(player.id)
    listing = context.own_listings.get(player.id)
    entry: dict[str, Any] = {
        "player_id": player.id,
        "name": _full_name(sp),
        "position": _POSITION_LABELS.get(player.position, "MID"),
        "team_id": player.team_id,
        "market_value": _int(player.market_value),
        # `null` statt 0, wenn Kickbase keine Punktedaten liefert — 0 hieße
        # „hat gespielt und nichts gebracht", und das ist etwas anderes.
        "average_points_season": (
            round(player.average_points, 2) if player.average_points is not None else None
        ),
        "total_points_season": player.total_points,
        "lineup_order": sp.lineup_order,
        "in_starting_xi": _is_starting_xi(sp),
        "injury_status": enrichment.injury_status if enrichment else "unknown",
        "market_trend_1d_pct": enrichment.market_trend_1d_pct if enrichment else None,
        "market_trend_3d_pct": enrichment.market_trend_3d_pct if enrichment else None,
        "market_trend_7d_pct": enrichment.market_trend_7d_pct if enrichment else None,
        "market_trend_30d_pct": enrichment.market_trend_30d_pct if enrichment else None,
        "mv_max_30d": enrichment.mv_max_30d if enrichment else None,
        "avg_points_last5": enrichment.avg_points_last5 if enrichment else None,
        "start_probability_next": enrichment.start_probability_next if enrichment else None,
        "start_probability_source": enrichment.start_probability_source if enrichment else None,
        "listing": _own_listing(listing) if listing else None,
    }
    if buy is not None:
        entry["bought_at_price"] = _int(buy.buy_price)
        entry["bought_intent"] = buy.intent.value
    if enrichment:
        entry["missing_data_flags"] = list(enrichment.missing_data_flags)
    return entry


_STARTING_XI_MAX_LO = 10  # Kickbase-`lo` 0..10 = die 11 Startelf-Slots.


def _is_starting_xi(sp: SquadPlayer) -> bool:
    # Bank/Reserve bekommt entweder einen höheren `lo`-Wert oder None.
    return sp.lineup_order is not None and 0 <= sp.lineup_order <= _STARTING_XI_MAX_LO


def _own_listing(listing: Any) -> dict[str, Any]:
    return {
        "price": _int(listing.listing_price),
        "listed_at_iso": _to_iso(listing.listed_at) if listing.listed_at else None,
        "expires_at_iso": _to_iso(listing.expires_at) if listing.expires_at else None,
        "has_offers": bool(listing.has_offers),
        "offer_count": int(listing.offer_count),
    }


def _market_entry(mp: MarketPlayer, context: DecisionContext, now: datetime) -> dict[str, Any]:
    player = mp.player
    enrichment = context.enrichment.get(player.id)
    expires_at = mp.expires_at(now)
    entry: dict[str, Any] = {
        "player_id": player.id,
        "name": _full_name(mp),
        "position": _POSITION_LABELS.get(player.position, "MID"),
        "team_id": player.team_id,
        "market_value": _int(player.market_value),
        "listed_price": _int(mp.price),
        "expires_at_iso": _to_iso(expires_at) if expires_at else None,
        "listed_by": _listed_by(mp, context),
        "seller_id": mp.seller_id,
        # Konkurrenz auf diesem Listing: je höher, desto eher braucht ein
        # eigenes Gebot einen Aufschlag (Grundlage für P2-13).
        "offer_count": mp.offer_count,
        "injury_status": enrichment.injury_status if enrichment else "unknown",
        "market_trend_1d_pct": enrichment.market_trend_1d_pct if enrichment else None,
        "market_trend_3d_pct": enrichment.market_trend_3d_pct if enrichment else None,
        "market_trend_7d_pct": enrichment.market_trend_7d_pct if enrichment else None,
        "market_trend_30d_pct": enrichment.market_trend_30d_pct if enrichment else None,
        "mv_max_30d": enrichment.mv_max_30d if enrichment else None,
        "avg_points_last5": enrichment.avg_points_last5 if enrichment else None,
        "start_probability_next": enrichment.start_probability_next if enrichment else None,
        "start_probability_source": enrichment.start_probability_source if enrichment else None,
        "is_new_on_market": mp.is_new,
        "listed_at_iso": _to_iso(mp.listed_at) if mp.listed_at else None,
    }
    if enrichment:
        entry["missing_data_flags"] = list(enrichment.missing_data_flags)
    return entry


def _listed_by(mp: MarketPlayer, context: DecisionContext) -> str:
    if mp.seller_id is None:
        return "kickbase"
    if mp.seller_id == context.squad.manager_id:
        return "self"
    return "user"


def _incoming_offers(context: DecisionContext) -> list[dict[str, Any]]:
    offers: list[dict[str, Any]] = []
    manager_id = context.squad.manager_id
    for mp in context.market:
        if mp.seller_id != manager_id or not mp.offers:
            continue
        for offer in mp.offers:
            offers.append(_offer_entry(mp, offer))
    return offers


def _offer_entry(mp: MarketPlayer, offer: MarketOffer) -> dict[str, Any]:
    expires_in_min = None
    if offer.valid_until is not None:
        delta = offer.valid_until - datetime.now(UTC)
        expires_in_min = max(0, int(delta.total_seconds() // 60))
    return {
        "offer_id": offer.id,
        "player_id": mp.player.id,
        "offered_by_user_id": offer.user_id,
        "offered_by_name": offer.user_name,
        "price": _int(offer.price),
        "expires_in_min": expires_in_min,
    }


def _recent_action(action: RecentAction) -> dict[str, Any]:
    return {
        "ts_iso": _to_iso(action.ts),
        "action": action.action.value,
        "player_id": action.player_id,
        "price": _int(action.price) if action.price is not None else None,
        "intent": action.intent.value if action.intent is not None else None,
        "executed": action.executed,
    }


def _minutes_until(now: datetime, target: datetime | None) -> int | None:
    """Minuten bis zum Zieltermin; None, wenn keiner bekannt ist.

    Negative Werte bleiben negativ (Termin liegt zurück) — das LLM soll den
    Unterschied zwischen „gleich" und „vorbei" sehen können.
    """
    if target is None:
        return None
    return int((target - now).total_seconds() // 60)


def _time_until(
    now: datetime, target: datetime | None, interval_min: int
) -> tuple[int | None, int | None]:
    if target is None:
        return None, None
    delta_min = int((target - now).total_seconds() // 60)
    if delta_min <= 0:
        return 0, delta_min
    ticks = delta_min // max(interval_min, 1)
    return ticks, delta_min


def _to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _int(value: Decimal | int | float | None) -> int:
    if value is None:
        return 0
    if isinstance(value, Decimal):
        return int(value)
    return int(value)


__all__ = ["AiDecisionConfig", "AiDecisionEngine"]
