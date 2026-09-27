"""Jinja2-Environment für die Web-Schicht — zentral, damit Auto-Reload konsistent bleibt.

Hier hängen auch die Anzeige-Filter. Sie stehen bewusst in Python und nicht als
Jinja-Ausdruck im Template: `"{:,.0f}".format(x).replace(",", ".")` stand vorher
an vier Stellen, und an einer davon fehlte das Euro-Zeichen.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATE_DIR = Path(__file__).parent / "templates"

# Deutsche Beschriftungen für die Aktionen aus `TradeAction`. Im `trade_log`
# stehen die englischen Bezeichner — die bleiben auch dort, sie sind Daten.
# Übersetzt wird erst an der Oberfläche.
_ACTION_LABELS = {
    "BUY": "Kauf",
    "SELL": "Verkauf",
    "SELL_INSTANT": "Sofortverkauf",
    "LIST_ON_MARKET": "Angeboten",
    "REMOVE_FROM_MARKET": "Zurückgezogen",
    "ACCEPT_OFFER": "Gebot angenommen",
    "DECLINE_OFFER": "Gebot abgelehnt",
    "SET_LINEUP": "Aufstellung",
    "HOLD": "Abwarten",
    "BONUS": "Tagesbonus",
    "ERROR": "Fehler",
}

_MINUTE = 60
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR

_INTENT_LABELS = {
    "SQUAD_FILL": "Kader füllen",
    "PROFIT": "Gewinn mitnehmen",
    "POINTS": "Punkte holen",
    "SQUAD_TRIM": "Aussortieren",
    "DEBT_RELIEF": "Konto entlasten",
}


def euro(value: Decimal | int | float | None) -> str:
    """`1234567` → `1.234.567 €`. Deutsche Tausenderpunkte, keine Nachkommastellen.

    Kickbase rechnet in vollen Euro; Cent anzuzeigen würde eine Genauigkeit
    vortäuschen, die die Zahlen nicht haben.
    """
    if value is None:
        return "—"
    return f"{int(value):,.0f}".replace(",", ".") + " €"


def action_label(action: str | None) -> str:
    if not action:
        return "—"
    return _ACTION_LABELS.get(action, action)


def intent_label(intent: str | None) -> str:
    if not intent:
        return "—"
    return _INTENT_LABELS.get(intent, intent)


def when(value: datetime | None) -> str:
    """Zeitangabe, die so grob ist wie nötig und so genau wie sinnvoll.

    Frisches zeigt den Abstand („vor 3 Min."), weil das beim Zuschauen die
    Frage ist; alles ab einem Tag zeigt das Datum, weil „vor 62 Std." niemand
    im Kopf umrechnet.
    """
    if value is None:
        return "—"
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    delta = (datetime.now(UTC) - moment).total_seconds()
    local = moment.astimezone()
    if delta < _MINUTE:
        return "gerade eben"
    if delta < _HOUR:
        return f"vor {int(delta // _MINUTE)} Min."
    if delta < _DAY:
        return local.strftime("%H:%M")
    return local.strftime("%d.%m. %H:%M")


def age(value: datetime | None) -> str:
    """Wie alt ein gespeicherter Eintrag ist — für die Datenverwaltung."""
    if value is None:
        return "—"
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    delta = (datetime.now(UTC) - moment).total_seconds()
    if delta < _HOUR:
        return f"{max(int(delta // _MINUTE), 1)} Min. alt"
    if delta < _DAY:
        return f"{int(delta // _HOUR)} Std. alt"
    return f"{int(delta // _DAY)} Tage alt"


templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.filters["euro"] = euro
templates.env.filters["action_label"] = action_label
templates.env.filters["intent_label"] = intent_label
templates.env.filters["when"] = when
templates.env.filters["age"] = age
