"""VCR-Konfiguration für Kickbase-Live-Aufnahmen.

Sorgt dafür, dass niemals sensitive Daten auf die Disk geschrieben werden:
- Passwort im Request-Body      → REDACTED_PASSWORD
- Authorization-Header          → REDACTED_TOKEN
- Set-Cookie-Header             → komplett entfernt (enthält JWT mit Nutzerdaten)
- JWT-Token in Login-Response   → REDACTED_JWT
- User-Email + eigene User-ID   → generische Platzhalter
- Klarnamen, Profilbilder und IDs **fremder Manager** → Pseudonyme

Der letzte Punkt kam mit P0-0.3 dazu: `/ranking` (`us[]`), `/market` (`u`) und
`/managers/{m}/transfer` (`othnm`) tragen personenbezogene Daten anderer
Mitspieler, und diese Cassettes liegen im Repo. Die alte Heuristik erkannte
solche Objekte nicht — sie suchte nach `id`, Kickbase schreibt aber `i`.

Die Unterscheidung User- vs. Spieler-Objekt läuft über Marker: ein Dict mit
`uim`/`unm`/`profile`/`em` ist ein Manager, eines mit `pos`/`mv`/`pi`/`pn` ein
Spieler. Spielernamen bleiben im Klartext — sie sind öffentlich und die
Contract-Tests prüfen darauf.

Response-Bodies werden vor der Persistierung dekomprimiert (VCR-Option
`decode_compressed_response=True`), damit die Redaktion im Klartext greift.

Cassettes laufen offline (`record_mode='none'` in CI).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import vcr
import yaml

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
_FAKE_IDS = frozenset(_ID_SUBSTITUTIONS.values())


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


# Marker, an denen ein Dict als *Manager*-Objekt erkannt wird …
_USER_MARKERS = frozenset({"em", "email", "vemail", "uim", "unm", "profile"})
# … und als *Spieler*-Objekt. Spieler-Marker gewinnen: Spielernamen sind öffentlich.
_PLAYER_MARKERS = frozenset({"pos", "mv", "pi", "pn", "mvt", "prc"})

_TOKEN_KEYS = frozenset({"tkn", "token", "chttkn"})
_EMAIL_KEYS = frozenset({"em", "email", "vemail", "emve"})
# Bild-URLs tragen identifizierende Hashes.
_IMAGE_KEYS = frozenset({"profile", "uim", "sfb", "efb", "pim", "prfu", "ua"})
# Namensfelder, die unabhängig vom Kontext immer eine Person meinen.
_NAME_KEYS = frozenset({"unm", "creator", "othnm"})
_ID_KEYS = frozenset({"i", "id", "u"})


def _redact_dict(node: Any) -> None:
    if isinstance(node, dict):
        is_user = bool(_USER_MARKERS & node.keys()) and not (_PLAYER_MARKERS & node.keys())
        for key, value in list(node.items()):
            if key in _TOKEN_KEYS and isinstance(value, str):
                node[key] = "REDACTED_JWT"
            elif key in _EMAIL_KEYS and isinstance(value, str):
                node[key] = "redacted@example.com"
            elif key in _IMAGE_KEYS and isinstance(value, str):
                node[key] = "redacted/image.png"
            elif isinstance(value, str) and _is_person_name(key, is_user=is_user):
                node[key] = "Redacted"
            elif is_user and key in _ID_KEYS and isinstance(value, str):
                node[key] = _pseudonymous_id(value)
            elif isinstance(value, dict | list):
                _redact_dict(value)
    elif isinstance(node, list):
        for item in node:
            _redact_dict(item)


def _is_person_name(key: str, *, is_user: bool) -> bool:
    """`n` ist je nach Objekt Spieler-, Team- oder Managername — nur im
    Manager-Kontext redigieren, sonst gingen Team- und Liganamen verloren."""
    return key in _NAME_KEYS or (is_user and key in {"name", "n"})


def _pseudonymous_id(value: str) -> str:
    """Stabile Ersatz-ID für fremde Manager.

    Deterministisch, damit dieselbe Person über alle Cassettes hinweg dieselbe
    ID behält (Tests dürfen darauf verweisen), aber nicht zurückrechenbar. Die
    eigene ID läuft weiter über `_ID_SUBSTITUTIONS`, damit bestehende Tests mit
    `FAKE_USER_ID` unverändert bleiben.
    """
    if value in _ID_SUBSTITUTIONS:
        return _ID_SUBSTITUTIONS[value]
    # `_rewrite_ids` läuft vorher über den ganzen Body — eigene IDs stehen hier
    # also bereits als Platzhalter da und dürfen nicht nochmal ersetzt werden.
    if value in _FAKE_IDS or not value.isdigit():
        return value
    digest = hashlib.sha256(f"kb-manager:{value}".encode()).hexdigest()
    return str(8_000_000 + int(digest[:8], 16) % 1_000_000)


def load_cassette_payload(name: str, *, index: int = 0) -> dict[str, Any]:
    """Response-Body einer Cassette als dict — ohne HTTP, ohne VCR-Replay.

    Für Tests, die nur die *Daten* brauchen (Payload-Snapshot, Contract-Profile)
    statt den Client-Pfad. `index` wählt die Interaktion, falls eine Cassette
    mehrere enthält.
    """
    path = CASSETTE_DIR / f"{name}.yaml"
    raw = yaml.safe_load(path.read_text())
    body = raw["interactions"][index]["response"]["body"]["string"]
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise TypeError(f"{name}.yaml liefert kein JSON-Objekt, sondern {type(payload).__name__}")
    return payload


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
