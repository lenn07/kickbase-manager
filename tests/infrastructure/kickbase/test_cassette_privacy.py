"""Wacht darüber, dass keine personenbezogenen Daten in den Cassettes landen.

Cassettes liegen im Repo. Sie enthalten Antworten der echten Liga — also auch
Klarnamen, Profilbild-URLs und User-IDs **anderer Mitspieler**. Die Redaktion
in `vcr_config.py` fängt das ab; dieser Test stellt sicher, dass sie greift und
nicht bei einem neuen Endpunkt still durchrutscht (das war der Fall bei
`/ranking` und dem `u`-Objekt im Market-Payload, siehe P0-0.3).

Der Test prüft die **Redaktionsfunktion** gegen synthetische Payloads *und* die
tatsächlich eingecheckten Cassettes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.infrastructure.kickbase.vcr_config import (
    CASSETTE_DIR,
    FAKE_LEAGUE_ID,
    FAKE_USER_ID,
    REAL_LEAGUE_ID,
    REAL_USER_ID,
    _redact_dict,
)


def _redacted(payload: dict[str, Any]) -> dict[str, Any]:
    copy = json.loads(json.dumps(payload))
    _redact_dict(copy)
    return copy


def test_ranking_entries_lose_name_and_id() -> None:
    """`/ranking` liefert `us[]` mit Klarnamen und IDs fremder Manager."""
    payload = {
        "us": [
            {"i": "3377833", "n": "Echter Name", "uim": "content/file/abc.png", "sp": 4091},
            {"i": "4711000", "n": "Noch Einer", "uim": "content/file/def.png", "sp": 3900},
        ]
    }
    result = _redacted(payload)

    for entry in result["us"]:
        assert entry["n"] == "Redacted"
        assert entry["uim"] == "redacted/image.png"
        assert entry["i"] not in {"3377833", "4711000"}
        assert entry["i"].isdigit()
    # Nicht-personenbezogene Felder bleiben unangetastet.
    assert result["us"][0]["sp"] == 4091
    # Verschiedene Personen bekommen verschiedene Pseudonyme.
    assert result["us"][0]["i"] != result["us"][1]["i"]


def test_own_id_keeps_its_fake_placeholder() -> None:
    """Die eigene ID ist beim Aufruf von `_redact_dict` schon ersetzt (`_rewrite_ids`
    läuft vorher über den Body) und darf nicht erneut pseudonymisiert werden —
    sonst zeigen die Tests plötzlich auf eine Zufalls-ID statt auf FAKE_USER_ID."""
    payload = {"u": {"i": FAKE_USER_ID, "em": "x@y.de", "n": "Ich"}}
    assert _redacted(payload)["u"]["i"] == FAKE_USER_ID


def test_pseudonymous_ids_are_stable() -> None:
    """Dieselbe Person muss über alle Cassettes dieselbe Ersatz-ID bekommen."""
    payload = {"us": [{"i": "3377833", "n": "X", "uim": "a.png"}]}
    assert _redacted(payload)["us"][0]["i"] == _redacted(payload)["us"][0]["i"]


def test_market_seller_object_is_redacted() -> None:
    """Das `u`-Objekt im Market-Item ist der Verkäufer — ein echter Mensch."""
    payload = {
        "it": [
            {
                "i": "1809",
                "fn": "Marius",
                "n": "Wolf",
                "pos": 3,
                "mv": 8811078,
                "u": {"i": "4320433", "n": "Mein Name", "uim": "content/file/x.png"},
            }
        ]
    }
    result = _redacted(payload)["it"][0]

    assert result["u"]["n"] == "Redacted"
    assert result["u"]["uim"] == "redacted/image.png"
    # Spielernamen sind öffentlich und bleiben — Contract-Tests prüfen darauf.
    assert result["fn"] == "Marius"
    assert result["n"] == "Wolf"


def test_player_objects_keep_their_names() -> None:
    """Regression: die Manager-Heuristik darf keine Spieler erfassen."""
    payload = {"it": [{"pi": "1991", "pn": "Upamecano", "pos": 2, "mv": 33697577, "st": 0}]}
    assert _redacted(payload)["it"][0]["pn"] == "Upamecano"


def test_transfer_partner_name_is_redacted() -> None:
    payload = {"u": "4320433", "unm": "Ich", "it": [{"pi": "3176", "othnm": "Der Andere"}]}
    result = _redacted(payload)
    assert result["unm"] == "Redacted"
    assert result["it"][0]["othnm"] == "Redacted"


def test_tokens_and_emails_never_survive() -> None:
    payload = {"tkn": "eyJhbGciOi...", "u": {"i": "1", "em": "me@example.com", "n": "Ich"}}
    result = _redacted(payload)
    assert result["tkn"] == "REDACTED_JWT"
    assert result["u"]["em"] == "redacted@example.com"


# -- Prüfung der eingecheckten Cassettes ---------------------------------


def _cassette_files() -> list[Path]:
    return sorted(CASSETTE_DIR.glob("*.yaml"))


@pytest.mark.parametrize("cassette", _cassette_files(), ids=lambda p: p.name)
def test_checked_in_cassette_has_no_real_ids(cassette: Path) -> None:
    text = cassette.read_text()
    assert REAL_USER_ID not in text, f"{cassette.name} enthält die echte User-ID"
    assert REAL_LEAGUE_ID not in text, f"{cassette.name} enthält die echte Liga-ID"


@pytest.mark.parametrize("cassette", _cassette_files(), ids=lambda p: p.name)
def test_checked_in_cassette_has_no_credentials(cassette: Path) -> None:
    text = cassette.read_text().lower()
    for needle in ("password", "bearer ey", "set-cookie"):
        assert needle not in text or "redacted" in text, (
            f"{cassette.name} enthält möglicherweise ein Geheimnis: {needle}"
        )


def test_fake_ids_differ_from_real_ones() -> None:
    """Schutz vor einem Copy-Paste-Fehler in vcr_config."""
    assert FAKE_USER_ID != REAL_USER_ID
    assert FAKE_LEAGUE_ID != REAL_LEAGUE_ID
