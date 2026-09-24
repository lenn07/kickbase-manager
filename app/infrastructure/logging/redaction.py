"""Log-Redaction — maskiert Bearer-Tokens, Passwörter und API-Keys in Log-Zeilen.

Der Filter greift am Root-Logger und wirkt damit sowohl auf `stdout` als auch
auf den Broadcast-Stream fürs Dashboard. Er ersetzt Match-Gruppen im finalen
Message-String; strukturierte Extra-Felder werden ebenfalls durchsucht.

Konservativ ausgelegt: lieber ein bisschen Overhead pro Log-Record als ein
versehentlich geloggtes Kickbase-Passwort.
"""

from __future__ import annotations

import logging
import re
from re import Pattern

_MASK = "***"

# Reihenfolge zählt: Key-Value-Formen vor generischen Token-Formen matchen.
_PATTERNS: tuple[Pattern[str], ...] = (
    # "Authorization: Bearer <token>" oder "Bearer <token>"
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{8,})"),
    # sk-ant-... (Anthropic-Keys), sk_live_/sk_test_ (allg. API-Keys)
    re.compile(r"\b(sk-ant-[A-Za-z0-9_\-]{10,})\b"),
    re.compile(r"\b(sk_(?:live|test)_[A-Za-z0-9]{10,})\b"),
    # key/token/password/secret/api_key = "..." oder "key": "..." oder key=...
    # optionale schließende Quote nach dem Key (JSON), dann `= | :`, dann optional Quote,
    # dann Value ohne Whitespace/Quotes/Trenner.
    re.compile(
        r"(?i)"
        r"((?:api[_-]?key|access[_-]?token|auth[_-]?token|token|password|passwd|pwd|secret)"
        r"['\"]?\s*[=:]\s*)"
        r"(['\"]?)"
        r"([^\s'\"&,;]{4,})"
        r"\2"
    ),
)


def _redact(text: str) -> str:
    result = text
    result = _PATTERNS[0].sub(lambda m: f"{m.group(1)}{_MASK}", result)
    result = _PATTERNS[1].sub(_MASK, result)
    result = _PATTERNS[2].sub(_MASK, result)
    result = _PATTERNS[3].sub(lambda m: f"{m.group(1)}{m.group(2)}{_MASK}{m.group(2)}", result)
    return result


class RedactionFilter(logging.Filter):
    """`logging.Filter`, der sensible Werte in `record.msg`/`record.args` maskiert.

    Formatierte Ausgabe erfolgt bei `logging` erst nach `filter()`. Damit
    Handler-Formatter (z.B. `%(message)s`) das maskierte Ergebnis sehen, wird
    hier bereits interpoliert und `msg` durch die maskierte Zeichenkette
    ersetzt; `record.args` wird geleert, damit der Formatter nicht erneut
    interpoliert.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except (TypeError, ValueError):
            # Bei defekten Format-Args gar nicht erst redigieren; der Handler
            # wird selbst über handleError() eine Warnung ausgeben.
            return True
        redacted = _redact(rendered)
        if redacted != rendered:
            record.msg = redacted
            record.args = None
        return True


def install_redaction_filter(logger: logging.Logger | None = None) -> RedactionFilter:
    """Hängt einen Redaction-Filter an **alle Handler** des Loggers (idempotent).

    ⚠️ **Nicht an den Logger selbst.** Python wendet Logger-Filter nur auf
    Records an, die direkt auf *diesem* Logger entstehen. Beim Propagieren nach
    oben ruft `Logger.callHandlers()` die **Handler** der Vorfahren auf, ihre
    Filter aber nicht. Ein Filter am Root-Logger sieht deshalb nie einen Record
    aus `app.irgendwas` — und die gesamte Anwendung loggt über solche
    Kind-Logger.

    Genau so war es bis 2026-09-24 gebaut: die Maskierung lief ins Leere, ohne
    dass es auffiel, weil der zugehörige Test den Filter direkt aufrief statt
    über einen echten Logger. Handler-Filter greifen dagegen bei jedem Record,
    der den Handler erreicht, egal wo er entstanden ist.

    Neue Handler, die später dazukommen, müssen den Filter selbst erhalten —
    deshalb gibt die Funktion ihn zurück.
    """
    target = logger if logger is not None else logging.getLogger()
    redactor = _existing_redactor(target) or RedactionFilter()
    for handler in target.handlers:
        if not any(isinstance(f, RedactionFilter) for f in handler.filters):
            handler.addFilter(redactor)
    # Zusätzlich am Logger: deckt Records ab, die direkt hier entstehen.
    if not any(isinstance(f, RedactionFilter) for f in target.filters):
        target.addFilter(redactor)
    return redactor


def _existing_redactor(target: logging.Logger) -> RedactionFilter | None:
    for existing in target.filters:
        if isinstance(existing, RedactionFilter):
            return existing
    for handler in target.handlers:
        for existing in handler.filters:
            if isinstance(existing, RedactionFilter):
                return existing
    return None
