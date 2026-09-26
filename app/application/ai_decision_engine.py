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
    # `0.0` seit P0-5. Die Entscheidung ist kein kreativer Akt: bei gleicher
    # Lage soll dieselbe Aktion herauskommen. Sampling macht sonst jeden
    # Prompt-Merge unbeweisbar — man kann nicht unterscheiden, ob eine geänderte
    # Entscheidung an der Änderung liegt oder an der Temperatur. Genau darauf
    # fährt die Eval-Suite (§9, „Prompt-Regression durch Sampling").
    temperature: float | None = 0.0


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
                temperature=self._config.temperature,
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
    _validate_against_context(action, player_id, offer_id, price, context)

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
    price: Decimal | None,
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
        _reject_pointless_rebid(player_id, price, context)
        return

    in_squad = player_id in {sp.player.id for sp in context.squad.players}
    if action in {TradeAction.SELL, TradeAction.LIST_ON_MARKET} and not in_squad:
        raise _InvalidDecisionError(
            f"{action.value} für Spieler {player_id!r}, der nicht im Kader steht"
        )


def _reject_pointless_rebid(
    player_id: str | None, price: Decimal | None, context: DecisionContext
) -> None:
    """Blockt ein zweites Gebot, das nichts verbessert.

    Auf ein laufendes Gebot noch einmal denselben Betrag zu setzen, ändert die
    Lage nicht: Kickbase entscheidet erst beim Ablauf des Listings und nimmt
    das höchste Gebot. Ein gleich hohes Nachgebot verbraucht nur den Tick —
    und der Bot hat am 2026-09-24 genau das siebenmal hintereinander getan,
    weil er sein eigenes Gebot nicht sah (Defekt D3).

    Ein **höheres** Gebot ist dagegen legitim und geht durch: fremde Gebote
    sind unsichtbar (Kickbase zeigt sie nicht), also ist Nachlegen der einzige
    Weg, einen vermuteten Konkurrenten doch noch zu überbieten. Bis P2-13 stand
    hier `offer_count > 1` als Bedingung — dieser Zähler meint auf fremden
    Listings aber die **eigenen** Gebote, nicht die der anderen.
    """
    existing = context.open_bids.get(player_id or "")
    if existing is None:
        return
    if price is not None and price > existing.price:
        return
    raise _InvalidDecisionError(
        f"BUY auf {player_id!r} zu {price}, aber es läuft bereits ein eigenes Gebot über "
        f"{existing.price} (seit {existing.placed_at.isoformat()}). Ein gleich hohes oder "
        "niedrigeres Nachgebot ändert nichts — Kickbase entscheidet erst beim Ablauf des "
        "Listings und nimmt das höchste Gebot."
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
        "trading": _trading_block(context, now),
        "incoming_offers": _incoming_offers(context),
        "recent_actions": [_recent_action(a) for a in context.recent_actions],
        "constraints": _constraints_block(context),
    }
    return payload


_TRADING_PHASE_DEADLINE_MIN = 120
_TRADING_PHASE_PREP_MIN = 24 * 60
_MV_UPDATE_PERIOD_MIN = 24 * 60


def _trading_phase(minutes_until_matchday: int | None) -> str:
    """Welches Ziel in diesem Tick vorgeht — als Label, nicht als Rechenaufgabe.

    Die Zielhierarchie des Prompts kippt zweimal: 24 h vor dem Anpfiff vom
    Trading zur Kaderpflege, 2 h davor zur reinen Regel-Compliance. Beide
    Grenzen standen bisher nur als Prosa in §4, und das Modell musste sie aus
    `minutes_until_matchday_start` selbst herleiten — bei einer Zahl wie 23190
    ist das eine Rechnung, die schiefgehen kann, und sie entscheidet darüber,
    ob ein Trade noch legitim ist.
    """
    if minutes_until_matchday is None:
        return "unknown"
    if minutes_until_matchday <= _TRADING_PHASE_DEADLINE_MIN:
        return "deadline"
    if minutes_until_matchday <= _TRADING_PHASE_PREP_MIN:
        return "matchday_prep"
    return "trading"


def _mv_updates_between(mv_update_at: datetime | None, until: datetime | None) -> int | None:
    """Wie viele 22-Uhr-Updates noch vor `until` liegen.

    Die entscheidende Trading-Kennzahl: Marktwerte bewegen sich ausschließlich
    zu diesen Zeitpunkten. Ein Kauf, auf den kein Update mehr folgt, kann keinen
    Marktwert-Gewinn machen — egal wie steil der Trend aussieht. Das Modell
    konnte das bisher nur über eine Datumsdifferenz in ISO-Strings schätzen.
    """
    if mv_update_at is None or until is None:
        return None
    delta_min = (until - mv_update_at).total_seconds() / 60
    if delta_min < 0:
        return 0
    return int(delta_min // _MV_UPDATE_PERIOD_MIN) + 1


def _trading_block(context: DecisionContext, now: datetime) -> dict[str, Any]:
    """Der Depot-Blick: was arbeitet, was liegt brach, wie viel Zeit bleibt.

    Trading ist bei Kickbase kein Nebenprodukt, sondern die Geldquelle zwischen
    den Spieltagen — und es wird über **Kaderplätze** gespielt: 16 Plätze, von
    denen jeder leere eine Position ist, die keine Rendite bringt. Ohne diesen
    Block musste das Modell Slot-Auslastung, Buchgewinne und die Zahl der
    laufenden Trade-Positionen aus dem `squad`-Array zusammenrechnen; in der
    Praxis tat es das nicht und handelte deshalb fast nur auf Punkte-Motive.
    """
    squad_size = len(context.squad.players)
    slots_free = context.constraints.squad_room_left(squad_size)
    pnl_total = sum(
        (sp.unrealized_pnl for sp in context.squad.players if sp.unrealized_pnl is not None),
        Decimal(0),
    )
    profit_positions = sum(
        1 for rec in context.buy_history.values() if rec.intent is TradeIntent.PROFIT
    )
    _, minutes_until_matchday = _time_until(now, context.next_matchday_start, context.interval_min)
    return {
        "phase": _trading_phase(minutes_until_matchday),
        # Wie viele Marktwert-Bewegungen bis zum Anpfiff überhaupt noch kommen.
        "mv_updates_until_matchday": _mv_updates_between(
            context.mv_update_at, context.next_matchday_start
        ),
        "squad_slots_used": squad_size,
        "squad_slots_free": slots_free,
        # Summe aller Buchgewinne/-verluste im Kader. Sagt, ob das Depot
        # insgesamt im Plus steht — die Einzelwerte stehen pro Spieler.
        "unrealized_pnl_total": _int(pnl_total),
        # Wie viele Kaderspieler als Trade gekauft wurden (Intent PROFIT) und
        # deshalb auf einen Exit warten, statt auf Punkte zu spielen.
        "profit_positions": profit_positions,
        # Wie viel in diesem Tick maximal ausgegeben werden könnte, bis die
        # 33 %-Grenze reißt — offene Gebote schon abgezogen. Nur in der
        # `trading`-Phase nutzbar: bis zum Anpfiff muss das Konto zurück ins Plus.
        "spendable_before_debt_limit": _int(
            context.current_balance_after_open_bids - context.max_negative_allowed
        ),
    }


def _constraints_block(context: DecisionContext) -> dict[str, Any]:
    """Guardrails des Nutzers **und** die Liga-Limits von Kickbase.

    Vor P1-9 standen hier nur die eigenen Einstellungen; die Kickbase-Limits
    waren im Code hartkodiert (Kaderlimit 15, real 16) oder gar nicht bekannt.

    Drei der fünf Liga-Felder sind `null`, weil es sie in keiner Response
    gibt: `GET /leagues/{l}/settings` existiert nicht, und `/me` liefert nur
    `mppu` und `tpc[]`. Sie kommen trotzdem in den Payload — mit
    `missing_data`-Flag. Ein Feld, das fehlt, kann das Modell nicht von einem
    unterscheiden, das es vergessen hat zu lesen; ein `null` mit Begründung
    schon (Plan §9).
    """
    limits = context.constraints
    missing: list[str] = []
    if limits.squad_limit is None:
        missing.append("missing_data:constraints.squad_limit")
    # `club_limit: null` allein wäre zweideutig — es hieße mal „diese Liga
    # begrenzt nicht" und mal „wir wissen es nicht". Das Flag steht deshalb
    # nur im zweiten Fall, und `club_limit_is_unlimited` sagt den ersten
    # ausdrücklich.
    if not limits.club_limit_known:
        missing.append("missing_data:constraints.club_limit")
    if limits.underpay_blocked is None:
        missing.append("missing_data:constraints.underpay_blocked")
    if limits.scoring_mode is None:
        missing.append("missing_data:constraints.scoring_mode")

    squad_size = len(context.squad.players)
    return {
        # Guardrails aus den Nutzer-Einstellungen.
        "min_cash_reserve": int(context.min_cash_reserve),
        "max_trade_pct": context.max_trade_pct,
        "blacklist": list(context.blacklist),
        "interval_min": context.interval_min,
        # Liga-Regeln von Kickbase.
        "squad_limit": limits.squad_limit,
        "squad_slots_left": limits.squad_room_left(squad_size),
        "club_limit": limits.club_limit,
        "club_limit_is_unlimited": limits.club_limit_is_unlimited,
        "players_per_club": dict(limits.players_per_club),
        "underpay_blocked": limits.underpay_blocked,
        "scoring_mode": limits.scoring_mode,
        "missing_data_flags": missing,
    }


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
        # Wie viele Gebote das sind. Eine Summe allein lässt offen, ob sie aus
        # einem großen oder fünf kleinen Geboten besteht.
        "open_bids_count": len(context.open_bids),
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
        # Minuten und Startelf-Einsätze im selben Fenster. §2.4 des Plans:
        # „Minuten sind die Basis von allem" — 140 Punkte aus vier
        # Zwanzig-Minuten-Einsätzen sind eine andere Aussage als 140 aus vier
        # kompletten Spielen, und ohne diese Zahlen sehen beide gleich aus.
        "minutes_last5": enrichment.minutes_last5 if enrichment else None,
        "starts_last5": enrichment.starts_last5 if enrichment else None,
        # Auf wie vielen gespielten Spieltagen die drei Werte beruhen; 0 heißt
        # „Saison-Durchschnitt statt echter Form".
        "form_matchdays_counted": enrichment.form_matchdays_counted if enrichment else 0,
        "start_probability_next": enrichment.start_probability_next if enrichment else None,
        "start_probability_source": enrichment.start_probability_source if enrichment else None,
        "listing": _own_listing(listing) if listing else None,
        # Einstand und Buchgewinn kommen seit P1-6 von Kickbase selbst
        # (`mvgl`), nicht mehr aus dem eigenen `trade_log`. Der Unterschied ist
        # nicht kosmetisch: das Log kennt nur, was dieser Bot gekauft hat —
        # zugeloste Spieler und Käufe aus der App standen ohne Einstand da, und
        # ohne Einstand ist weder ein PROFIT-Exit noch ein Transfer-Erfolg
        # planbar (Defekt D7).
        "bought_at_price": _int_or_none(sp.buy_price),
        "unrealized_pnl": _int_or_none(sp.unrealized_pnl),
    }
    # Der Intent bleibt beim `trade_log`: *warum* gekauft wurde, weiß nur der
    # Bot selbst. Kickbase liefert den Preis, nicht die Absicht.
    if buy is not None:
        entry["bought_intent"] = buy.intent.value
        # Fallback auf den geloggten Preis, falls Kickbase `mvgl` mal weglässt.
        if entry["bought_at_price"] is None:
            entry["bought_at_price"] = _int(buy.buy_price)
        # Wie lange die Position schon liegt. Beim Trading ist der knappe
        # Rohstoff nicht das Geld, sondern der Kaderplatz: ein Trade, der seit
        # acht Tagen bei +0,4 % steht, blockiert einen von 16 Plätzen, auf dem
        # ein anderer Spieler in derselben Zeit 6 % gemacht hätte. Ohne diese
        # Zahl sieht eine alte Position aus wie eine frische.
        entry["bought_at_iso"] = _to_iso(buy.bought_at) if buy.bought_at else None
        entry["days_held"] = _days_since(buy.bought_at, context.now)
    flags = list(enrichment.missing_data_flags) if enrichment else []
    if entry["bought_at_price"] is None:
        flags.append("missing_data:bought_at_price")
    if enrichment or flags:
        entry["missing_data_flags"] = flags
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
    my_bid = context.open_bids.get(player.id)
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
        # Minuten bis zum Zuschlag. Das Modell rechnete das bisher selbst aus
        # `expires_at_iso` minus `now_iso` — und daran hängt zweierlei: ob der
        # Spieler vor dem Anpfiff überhaupt ankommt, und wie viele
        # Marktwert-Updates das Gebot noch überleben muss (§3 Overbid).
        "expires_in_min": _minutes_until(now, expires_at),
        "mv_updates_until_expiry": _mv_updates_between(context.mv_update_at, expires_at),
        # **Anzahl der eigenen Gebote** auf dieses Listing (`ofc`), nicht der
        # fremden: Kickbase zeigt die Gebote anderer Manager nirgends an
        # (help.kickbase.com, „Warum habe ich den Spieler nicht bekommen?").
        # Bis P2-13 stand das Feld als `offer_count` im Payload und das Modell
        # las es als Konkurrenz — es überbot damit systematisch sich selbst und
        # hielt umgekehrt jeden fremden Bieter für nicht vorhanden. Der Wert ist
        # nur als **Gegenprobe zum eigenen Gebot** brauchbar: ≥ 1 heißt, es
        # läuft eins, auch wenn `my_open_bid_price` null ist (Gebot über die
        # Kickbase-App, das im trade_log fehlt).
        "my_open_bid_count": mp.offer_count,
        # Habe **ich** auf diesen Spieler schon geboten, und wie viel? `null`
        # heißt nein. Ohne dieses Feld bietet das Modell jeden Tick erneut auf
        # denselben Spieler, weil ein laufendes Gebot nirgends sichtbar ist —
        # genau das ist am 2026-09-24 siebenmal passiert (Defekt D3).
        "my_open_bid_price": _int_or_none(my_bid.price) if my_bid else None,
        "my_bid_placed_at_iso": _to_iso(my_bid.placed_at) if my_bid else None,
        "injury_status": enrichment.injury_status if enrichment else "unknown",
        "market_trend_1d_pct": enrichment.market_trend_1d_pct if enrichment else None,
        "market_trend_3d_pct": enrichment.market_trend_3d_pct if enrichment else None,
        "market_trend_7d_pct": enrichment.market_trend_7d_pct if enrichment else None,
        "market_trend_30d_pct": enrichment.market_trend_30d_pct if enrichment else None,
        "mv_max_30d": enrichment.mv_max_30d if enrichment else None,
        "avg_points_last5": enrichment.avg_points_last5 if enrichment else None,
        # Minuten und Startelf-Einsätze im selben Fenster. §2.4 des Plans:
        # „Minuten sind die Basis von allem" — 140 Punkte aus vier
        # Zwanzig-Minuten-Einsätzen sind eine andere Aussage als 140 aus vier
        # kompletten Spielen, und ohne diese Zahlen sehen beide gleich aus.
        "minutes_last5": enrichment.minutes_last5 if enrichment else None,
        "starts_last5": enrichment.starts_last5 if enrichment else None,
        # Auf wie vielen gespielten Spieltagen die drei Werte beruhen; 0 heißt
        # „Saison-Durchschnitt statt echter Form".
        "form_matchdays_counted": enrichment.form_matchdays_counted if enrichment else 0,
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


def _days_since(start: datetime | None, now: datetime | None) -> int | None:
    """Ganze Tage seit `start`. `None`, sobald eine der beiden Zeiten fehlt."""
    if start is None or now is None:
        return None
    delta = now - start
    return max(0, int(delta.total_seconds() // 86400))


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


def _int_or_none(value: Decimal | int | float | None) -> int | None:
    """Wie `_int`, aber `None` bleibt `None`.

    Für Felder, bei denen „unbekannt" und „null Euro" verschiedene Aussagen
    sind — `bought_at_price` ist genau so eins: ein Einstand von 0 hieße, der
    ganze Marktwert sei Gewinn.
    """
    return None if value is None else _int(value)


__all__ = ["AiDecisionConfig", "AiDecisionEngine"]
