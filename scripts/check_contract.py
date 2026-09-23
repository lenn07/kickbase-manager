"""Meldet, wenn Kickbase Felder umbenennt, entfernt oder hinzufügt.

Vergleicht die Key-Profile der zuletzt erhobenen Payloads gegen die Baseline in
`docs/contract_baseline.json`. Kein CI-Job — der Live-Pfad braucht Credentials.
**Monatlich manuell laufen lassen** (Optimizing-Plan §6, P0-0.5):

    python -m scripts.inspect_endpoints      # frische Payloads nach tmp/inspect/
    python -m scripts.check_contract         # Drift-Bericht
    python -m scripts.check_contract --update  # Baseline bewusst nachziehen

Ohne Credentials geht auch die Cassette-Variante — sie prüft denselben Vertrag
gegen die eingecheckten Aufnahmen:

    python -m scripts.check_contract --source cassettes

Exit-Code 1, sobald ein Feld fehlt, das die Baseline als immer vorhanden führt.

**Warum `optional` in der Baseline existiert:** manche Felder schwanken legitim.
`it[].prob` (Startelf-Wahrscheinlichkeit) liefert Kickbase nur in der
Spieltagswoche — ohne Ausnahmeliste würde der Checker das monatlich als Drift
melden, und man gewöhnt sich an rote Läufe. Genau dann ist er wertlos.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from scripts.dump_keys import profile_payload

_REPO = Path(__file__).resolve().parent.parent
_BASELINE_PATH = _REPO / "docs" / "contract_baseline.json"
_INSPECT_DIR = _REPO / "tmp" / "inspect"
_CASSETTE_DIR = _REPO / "tests" / "infrastructure" / "kickbase" / "cassettes"

# Endpunkte ohne prüfbaren Vertrag: `league_settings` antwortet mit HTTP 500,
# `lineup_selection` liefert ein leeres `it`.
_SKIP = frozenset({"league_settings", "lineup_selection"})

# Cassette und Discovery-Dump heißen für denselben Endpunkt unterschiedlich.
_CASSETTE_ALIASES = {"market_value": "player_marketvalue"}


@dataclass
class Drift:
    """Was sich gegenüber der Baseline geändert hat."""

    missing: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    weakened: list[str] = field(default_factory=list)
    tolerated: list[str] = field(default_factory=list)

    @property
    def is_breaking(self) -> bool:
        return bool(self.missing)

    @property
    def is_empty(self) -> bool:
        return not (self.missing or self.added or self.weakened)


def _load_payloads(source: str) -> dict[str, dict[str, Any]]:
    if source == "cassettes":
        return _load_cassette_payloads()
    return _load_inspect_payloads()


def _load_inspect_payloads() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(_INSPECT_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out[path.stem] = data
    return out


def _load_cassette_payloads() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(_CASSETTE_DIR.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text())
            body = raw["interactions"][0]["response"]["body"]["string"]
            data = json.loads(body)
        except (OSError, KeyError, IndexError, ValueError):
            continue
        if isinstance(data, dict):
            out[_CASSETTE_ALIASES.get(path.stem, path.stem)] = data
    return out


def _profile(payload: dict[str, Any]) -> tuple[set[str], set[str]]:
    """(immer vorhandene Keys, manchmal vorhandene Keys)."""
    profiles = profile_payload(payload)
    always = {p for p, info in profiles.items() if info.always}
    sometimes = set(profiles) - always
    return always, sometimes


def _build_baseline(
    payloads: dict[str, dict[str, Any]], previous: dict[str, Any]
) -> dict[str, Any]:
    """Neue Baseline, aber die gepflegten `optional`-Einträge bleiben erhalten."""
    old_endpoints = previous.get("endpoints", {})
    endpoints: dict[str, Any] = {}
    for name, payload in sorted(payloads.items()):
        if name in _SKIP:
            continue
        always, sometimes = _profile(payload)
        endpoints[name] = {
            "always": sorted(always),
            "sometimes": sorted(sometimes),
            "optional": old_endpoints.get(name, {}).get("optional", {}),
        }
    return {
        "_meta": {
            "updated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "note": (
                "Erwartete Key-Profile der Kickbase-v4-Payloads. "
                "`always` = in jedem Objekt an diesem Pfad vorhanden, `sometimes` = nicht in "
                "allen, `optional` = darf komplett fehlen (Pfad -> Begruendung). "
                "Gepflegt von scripts/check_contract.py --update."
            ),
        },
        "endpoints": endpoints,
    }


def _diff(name: str, payload: dict[str, Any], expected: dict[str, Any]) -> Drift:
    always, sometimes = _profile(payload)
    present = always | sometimes
    optional = expected.get("optional", {})
    expected_always = set(expected.get("always", []))
    expected_sometimes = set(expected.get("sometimes", []))

    drift = Drift()
    for key in sorted((expected_always | expected_sometimes) - present):
        if key in optional:
            drift.tolerated.append(f"{key} — {optional[key]}")
        else:
            drift.missing.append(key)
    drift.added = sorted(present - expected_always - expected_sometimes - set(optional))
    drift.weakened = sorted(expected_always & sometimes)
    del name
    return drift


def _report(name: str, drift: Drift) -> None:
    if drift.is_empty and not drift.tolerated:
        return
    print(f"\n### {name}")
    for key in drift.missing:
        print(f"  ✗ FEHLT     {key}")
    for key in drift.weakened:
        print(f"  ! SCHWÄCHER {key}  (war in jedem Objekt, jetzt nicht mehr)")
    for key in drift.added:
        print(f"  + NEU       {key}")
    for entry in drift.tolerated:
        print(f"  ~ toleriert {entry}")


def main(argv: list[str]) -> int:
    update = "--update" in argv
    source = "cassettes" if "--source" in argv and "cassettes" in argv else "inspect"

    payloads = _load_payloads(source)
    if not payloads:
        where = _CASSETTE_DIR if source == "cassettes" else _INSPECT_DIR
        print(f"FEHLER: keine Payloads in {where}.", file=sys.stderr)
        if source != "cassettes":
            print("Erst `python -m scripts.inspect_endpoints` laufen lassen.", file=sys.stderr)
        return 2

    previous: dict[str, Any] = {}
    if _BASELINE_PATH.exists():
        previous = json.loads(_BASELINE_PATH.read_text())

    if update or not previous:
        baseline = _build_baseline(payloads, previous)
        _BASELINE_PATH.write_text(json.dumps(baseline, indent=2, ensure_ascii=False) + "\n")
        action = "aktualisiert" if previous else "erstmalig angelegt"
        print(f"Baseline {action}: {_BASELINE_PATH}")
        print(f"  {len(baseline['endpoints'])} Endpunkte erfasst.")
        return 0

    print(f"Contract-Check gegen {_BASELINE_PATH.name} (Quelle: {source})")
    print(f"Baseline vom {previous.get('_meta', {}).get('updated_at', '?')}")

    expected_endpoints = previous.get("endpoints", {})
    breaking = False
    unseen = sorted(set(expected_endpoints) - set(payloads) - _SKIP)
    for name in sorted(payloads):
        if name in _SKIP:
            continue
        expected = expected_endpoints.get(name)
        if expected is None:
            print(f"\n### {name}\n  + NEUER ENDPUNKT (nicht in der Baseline)")
            continue
        drift = _diff(name, payloads[name], expected)
        _report(name, drift)
        breaking = breaking or drift.is_breaking

    if unseen:
        print(f"\n! Nicht erhoben: {', '.join(unseen)}")
    if breaking:
        print("\n✗ Vertrag verletzt — mindestens ein erwartetes Feld fehlt.")
        print("  Prüfen: hat Kickbase umbenannt, oder ist das Feld saisonal?")
        print("  Saisonal ⇒ in der Baseline unter `optional` eintragen (mit Begründung).")
        return 1
    print("\n✓ Keine vertragsverletzende Abweichung.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
