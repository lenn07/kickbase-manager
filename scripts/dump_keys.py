"""Zeigt für jede Datei in tmp/inspect/ die vorkommenden Keys + Beispielwerte.

Gegenstück zu `scripts.inspect_endpoints` (Optimizing-Plan §6, P0-0.1): das
Discovery-Skript speichert Roh-JSON, dieses Skript macht daraus eine lesbare
Feldübersicht — inklusive verschachtelter Listen (`it[].ph[].mp`).

`profile_payload()` ist bewusst als Bibliotheksfunktion gebaut: der
Contract-Drift-Checker (P0-0.5) nutzt dieselbe Key-Ableitung.

Verwendung:
    python -m scripts.dump_keys                 # alle Dateien
    python -m scripts.dump_keys market squad    # nur diese
    python -m scripts.dump_keys --json          # maschinenlesbar
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Interne Metafelder, die inspect_endpoints selbst hinzufügt.
_META_KEYS = frozenset({"_status", "_path", "_non_json_body"})

_MAX_DEPTH = 4
_SAMPLE_CHARS = 60


@dataclass
class KeyProfile:
    """Wie oft ein Key in den Objekten an seinem Pfad vorkommt, mit Beispiel."""

    path: str
    present: int = 0
    total: int = 0
    types: set[str] = field(default_factory=set)
    sample: Any = None

    @property
    def coverage(self) -> str:
        return f"{self.present}/{self.total}"

    @property
    def always(self) -> bool:
        return self.present == self.total


def profile_payload(data: Any, *, max_depth: int = _MAX_DEPTH) -> dict[str, KeyProfile]:
    """Flaches Key-Profil eines JSON-Payloads, Pfade in Punkt/`[]`-Notation."""
    profiles: dict[str, KeyProfile] = {}
    _walk(data, prefix="", profiles=profiles, depth=0, max_depth=max_depth)
    return profiles


def _walk(
    node: Any, *, prefix: str, profiles: dict[str, KeyProfile], depth: int, max_depth: int
) -> None:
    if depth > max_depth:
        return
    if isinstance(node, list):
        objects = [x for x in node if isinstance(x, dict)]
        if not objects:
            return
        _profile_objects(
            objects, prefix=f"{prefix}[]", profiles=profiles, depth=depth, max_depth=max_depth
        )
        return
    if isinstance(node, dict):
        _profile_objects([node], prefix=prefix, profiles=profiles, depth=depth, max_depth=max_depth)


def _profile_objects(
    objects: list[dict[str, Any]],
    *,
    prefix: str,
    profiles: dict[str, KeyProfile],
    depth: int,
    max_depth: int,
) -> None:
    all_keys: list[str] = []
    for obj in objects:
        for key in obj:
            if key not in all_keys:
                all_keys.append(key)

    for key in all_keys:
        if key in _META_KEYS:
            continue
        path = f"{prefix}.{key}" if prefix else key
        profile = profiles.setdefault(path, KeyProfile(path=path))
        profile.total += len(objects)
        for obj in objects:
            if key not in obj:
                continue
            value = obj[key]
            profile.present += 1
            profile.types.add(_type_name(value))
            if profile.sample is None and value not in (None, "", [], {}):
                profile.sample = value
        nested = [obj[key] for obj in objects if isinstance(obj.get(key), dict | list)]
        for value in nested:
            _walk(value, prefix=path, profiles=profiles, depth=depth + 1, max_depth=max_depth)


# bool vor int, weil bool von int erbt.
_TYPE_NAMES: tuple[tuple[type, str], ...] = (
    (bool, "bool"),
    (int, "int"),
    (float, "float"),
    (str, "str"),
    (list, "list"),
)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    for cls, name in _TYPE_NAMES:
        if isinstance(value, cls):
            return name
    return "obj"


def _short(value: Any) -> str:
    if value is None:
        return "—"
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= _SAMPLE_CHARS else text[: _SAMPLE_CHARS - 1] + "…"


def _print_file(path: Path) -> None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"\n### {path.name} — nicht lesbar: {exc}")
        return

    status = data.get("_status") if isinstance(data, dict) else None
    header = f"\n### {path.stem}" + (f"  (HTTP {status})" if status is not None else "")
    print(header)
    print("-" * len(header.strip()))

    profiles = profile_payload(data)
    if not profiles:
        print("  (keine Objekt-Keys)")
        return
    width = max(len(p) for p in profiles)
    for key in sorted(profiles):
        profile = profiles[key]
        flag = " " if profile.always else "?"
        types = "|".join(sorted(profile.types))
        print(
            f"  {flag} {key:<{width}}  {profile.coverage:>7}  {types:<10}  {_short(profile.sample)}"
        )


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    names = [a for a in argv if not a.startswith("--")]

    in_dir = Path(__file__).resolve().parent.parent / "tmp" / "inspect"
    if not in_dir.exists():
        print(
            f"FEHLER: {in_dir} fehlt — erst `python -m scripts.inspect_endpoints`.", file=sys.stderr
        )
        return 2

    files = sorted(in_dir.glob("*.json"))
    if names:
        wanted = {n.removesuffix(".json") for n in names}
        files = [f for f in files if f.stem in wanted]
    if not files:
        print("Keine passenden Dateien gefunden.", file=sys.stderr)
        return 1

    if as_json:
        out = {f.stem: sorted(profile_payload(json.loads(f.read_text()))) for f in files}
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    print("Legende: '?' = Key fehlt in mindestens einem Objekt.")
    print("Spalten: Pfad · vorhanden/gesamt · Typen · Beispielwert")
    for file in files:
        _print_file(file)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
