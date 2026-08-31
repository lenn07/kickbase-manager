"""VCR-Konfiguration für Kickbase-Live-Aufnahmen.

Sorgt dafür, dass niemals sensitive Daten auf die Disk geschrieben werden:
- Passwort im Request-Body      → REDACTED_PASSWORD
- Authorization-Header          → REDACTED_TOKEN
- Set-Cookie-Header             → komplett entfernt (enthält JWT mit Nutzerdaten)
- JWT-Token in Login-Response   → REDACTED_JWT
- User-Email + User-ID          → generische Platzhalter

Response-Bodies werden vor der Persistierung dekomprimiert (VCR-Option
`decode_compressed_response=True`), damit die Redaktion im Klartext greift.

Cassettes laufen offline (`record_mode='none'` in CI).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import vcr

CASSETTE_DIR = Path(__file__).parent / "cassettes"

# Ersetzt echte User-IDs in URIs/Bodies durch einen deterministischen Platzhalter.
REAL_USER_ID = "4320433"
FAKE_USER_ID = "9999999"
REAL_LEAGUE_ID = "12392224"
FAKE_LEAGUE_ID = "1111111"

_ID_SUBSTITUTIONS: dict[str, str] = {
    REAL_USER_ID: FAKE_USER_ID,
    REAL_LEAGUE_ID: FAKE_LEAGUE_ID,
}


def _rewrite_ids(text: str) -> str:
    for real, fake in _ID_SUBSTITUTIONS.items():
        text = text.replace(real, fake)
    return text


def _redact_request(request: Any) -> Any:
    # URI: echte IDs durch Fake-IDs ersetzen
    request.uri = _rewrite_ids(request.uri)

    body = request.body
    if body is None:
        return request
    try:
        text = body.decode() if isinstance(body, bytes) else str(body)
        payload = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return request

    for key in ("pass", "password"):
        if key in payload:
            payload[key] = "REDACTED_PASSWORD"
    for key in ("em", "email"):
        if key in payload:
            payload[key] = "redacted@example.com"

    request.body = json.dumps(payload).encode()
    return request


_SENSITIVE_HEADER_PATTERNS = re.compile(
    r"^(set-cookie|cf-ray|x-request-id|x-amzn-trace-id|kkstrauth)$", re.IGNORECASE
)


def _redact_response(response: dict[str, Any]) -> dict[str, Any]:
    # 1. Header aggressiv säubern (case-insensitive)
    headers = response.get("headers", {})
    for key in list(headers.keys()):
        if _SENSITIVE_HEADER_PATTERNS.match(key):
            del headers[key]

    # 2. Body: dank decode_compressed_response=True ist es Klartext
    body_container = response.get("body", {})
    raw = body_container.get("string", b"")
    if isinstance(raw, bytes):
        try:
            text = raw.decode()
        except UnicodeDecodeError:
            return response
    else:
        text = str(raw)

    # ID-Substitution auch im Body
    text = _rewrite_ids(text)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        body_container["string"] = text.encode()
        return response

    _redact_dict(payload)
    body_container["string"] = json.dumps(payload).encode()
    return response


def _redact_dict(node: Any) -> None:
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if key in {"tkn", "token", "chttkn"} and isinstance(value, str):
                node[key] = "REDACTED_JWT"
            elif key in {"em", "email", "vemail", "emve"} and isinstance(value, str):
                node[key] = "redacted@example.com"
            elif (
                key in {"name", "n", "unm", "creator"}
                and isinstance(value, str)
                and (key == "creator" or _looks_like_user_context(node))
            ):
                node[key] = "Redacted"
            elif key in {"profile", "uim", "sfb", "efb", "pim"} and isinstance(value, str):
                # Profilbild-URLs enthalten teils identifizierende Hashes
                node[key] = "redacted/image.png"
            elif isinstance(value, dict | list):
                _redact_dict(value)
    elif isinstance(node, list):
        for item in node:
            _redact_dict(item)


def _looks_like_user_context(node: dict[str, Any]) -> bool:
    """Bewusst konservativ: nur wenn User-Marker im selben Dict — sonst würden
    wir Team- oder Liga-Namen auch redakten."""
    return bool({"email", "em", "vemail", "id", "profile", "unm", "uim"} & node.keys())


def make_vcr(cassette_name: str, record_mode: str = "none") -> vcr.VCR:
    return vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        path_transformer=vcr.VCR.ensure_suffix(".yaml"),
        serializer="yaml",
        record_mode=record_mode,
        match_on=("method", "scheme", "host", "port", "path"),
        filter_headers=[
            ("authorization", "REDACTED_TOKEN"),
            ("cookie", "REDACTED_COOKIE"),
        ],
        decode_compressed_response=True,
        before_record_request=_redact_request,
        before_record_response=_redact_response,
    )
