"""Wertet die offenen Fragen F1-F5 des Optimizing-Plans (§8) gegen echte Daten aus.

Quellen: `tmp/inspect/*.json` (aus `scripts.inspect_endpoints`) und die
VCR-Cassettes. Das Skript **rät nicht** — es zeigt nur, was die Daten hergeben,
und benennt für jede Frage das Rest-Verfahren.

Warum als Skript und nicht als einmalige Handauswertung: F1 und F4 lassen sich
erst über mehrere Läufe zu verschiedenen Zeitpunkten beantworten (Gebot muss
eingehen, MW-Update muss durchlaufen). Das Skript schreibt deshalb ein
Verlaufsprotokoll nach `tmp/mvud_log.json`.

Verwendung:
    python -m scripts.answer_open_questions
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import yaml
from app.domain.models import PlayerStatus

_REPO = Path(__file__).resolve().parent.parent
_INSPECT_DIR = _REPO / "tmp" / "inspect"
_CASSETTE_DIR = _REPO / "tests" / "infrastructure" / "kickbase" / "cassettes"
_MVUD_LOG = _REPO / "tmp" / "mvud_log.json"
# Historische Stichprobe vom 31.08.2026 — die einzige erhaltene Quelle mit
# `prob`. Die damalige Cassette wurde in P0-0.3 durch eine aktuelle ersetzt,
# und Kickbase liefert `prob` außerhalb der Spieltagswoche nicht: ohne diese
# Datei ließe sich F2 nicht mehr belegen.
_PROB_SAMPLE = _REPO / "docs" / "samples" / "market_prob_sample_2026-08-31.json"

# Eigene User-ID: in den Live-Dumps echt, in den Cassettes durch die Fake-ID ersetzt.
_OWN_USER_IDS = frozenset({"4320433", "9999999"})


def _load_inspect(name: str) -> dict[str, Any] | None:
    path = _INSPECT_DIR / f"{name}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_cassette_market() -> tuple[dict[str, Any] | None, str | None]:
    """Market-Payload + Aufnahmezeitpunkt aus der VCR-Cassette."""
    path = _CASSETTE_DIR / "market.yaml"
    if not path.exists():
        return None, None
    try:
        raw = yaml.safe_load(path.read_text())
        interaction = raw["interactions"][0]
        headers = interaction["response"]["headers"]
        date = (headers.get("Date") or headers.get("date") or [None])[0]
        return json.loads(interaction["response"]["body"]["string"]), date
    except (OSError, KeyError, IndexError, ValueError):
        return None, None


def _headline(question: str, title: str) -> None:
    line = f"── {question} — {title} "
    print(f"\n{line}{'─' * max(0, 78 - len(line))}")


# -- F1: Offers-Array -----------------------------------------------------


def f1_offers_array() -> None:
    _headline("F1", "Wie heißt das Offers-Array im Market-Payload?")
    market = _load_inspect("market")
    if market is None:
        print("  Keine market.json — erst `python -m scripts.inspect_endpoints`.")
        return

    items = [x for x in market.get("it", []) if isinstance(x, dict)]
    with_offers = [x for x in items if int(x.get("ofc") or 0) > 0]
    own = [x for x in items if _seller_id(x) in _OWN_USER_IDS]

    print(f"  Markt-Einträge: {len(items)} · davon eigene Listings: {len(own)}")
    print(f"  Einträge mit ofc > 0: {len(with_offers)}")
    if not with_offers:
        print("  ⇒ OFFEN. Belegt ist bisher nur, wo das Array NICHT liegt:")
        print("     GET /v4/leagues/{l}/market/{pid}/offers → 405 (Allow: POST)")
        print("     GET /v4/leagues/{l}/market/{pid}        → 405 (Allow: DELETE)")
        print("     ⇒ Es kann nur im /market-Payload selbst stehen, sichtbar ab ofc > 0.")
        print("  Rest-Verfahren: eigenen Spieler listen → warten bis ofc > 0 → Skripte erneut.")
        return

    keys: Counter[str] = Counter()
    for item in with_offers:
        keys.update(item.keys())
    base = {"i", "fn", "n", "tid", "pos", "st", "mv", "mvt", "p", "ap", "ofc", "prc", "dt", "pim"}
    new_keys = sorted(set(keys) - base)
    print(f"  ⇒ Zusätzliche Keys bei ofc > 0: {new_keys or '(keine)'}")
    for item in with_offers[:2]:
        print(f"  Beispiel: {json.dumps(item, ensure_ascii=False)[:600]}")


def _seller_id(item: dict[str, Any]) -> str | None:
    seller = item.get("u")
    if isinstance(seller, dict):
        raw = seller.get("i") or seller.get("id")
        return str(raw) if raw is not None else None
    return str(seller) if seller is not None else None


# -- F2: prob-Richtung ----------------------------------------------------


def f2_prob_direction() -> None:
    _headline("F2", "Ist prob=1 die höchste oder niedrigste Startelf-Wahrscheinlichkeit?")
    live = _load_inspect("market")
    cassette, recorded = _load_cassette_market()
    sample, sample_stamp = _load_prob_sample()

    sources = (
        ("live", live, None),
        ("cassette", cassette, recorded),
        ("archiv", sample, sample_stamp),
    )
    evidence_shown = False
    for label, payload, stamp in sources:
        if payload is None:
            continue
        items = [x for x in payload.get("it", []) if isinstance(x, dict)]
        with_prob = [x for x in items if x.get("prob") is not None]
        suffix = f" ({stamp})" if stamp else ""
        print(f"  {label}{suffix}: {len(with_prob)}/{len(items)} Einträge mit `prob`")
        if with_prob:
            _print_prob_table(with_prob)
            evidence_shown = True

    if not evidence_shown:
        print(f"  ⚠ Keine Stichprobe mit `prob` gefunden — fehlt {_PROB_SAMPLE.name}?")
        return
    print("  ⇒ Fällt der Median-Marktwert monoton mit steigendem `prob`, ist 1 = sicherste")
    print("     Startelf. `prob` erscheint nur in der Spieltagswoche — außerhalb liefert")
    print("     Kickbase das Feld gar nicht, dann trägt nur die Archiv-Stichprobe.")
    print("  Rest-Verfahren: gegen die 5 Icons der App-Aufstellungsansicht gegenprüfen.")


def _load_prob_sample() -> tuple[dict[str, Any] | None, str | None]:
    """Archiv-Stichprobe mit `prob` (siehe `_PROB_SAMPLE`)."""
    if not _PROB_SAMPLE.exists():
        return None, None
    try:
        data = json.loads(_PROB_SAMPLE.read_text())
    except (OSError, json.JSONDecodeError):
        return None, None
    return data, str(data.get("_recorded_at") or "archiviert")


def _print_prob_table(items: list[dict[str, Any]]) -> None:
    buckets: dict[int, list[int]] = {}
    for item in items:
        buckets.setdefault(int(item["prob"]), []).append(int(item.get("mv") or 0))
    print(f"    {'prob':>4} {'n':>3} {'median_mv':>14}")
    for prob in sorted(buckets):
        values = sorted(buckets[prob])
        median = values[len(values) // 2]
        print(f"    {prob:>4} {len(values):>3} {median:>14,}")


# -- F3: st-Werte ---------------------------------------------------------


def f3_status_values() -> None:
    _headline("F3", "Welche `st`-Werte existieren real?")
    known = {s.value for s in PlayerStatus}
    seen: Counter[int] = Counter()
    sources: dict[int, set[str]] = {}

    for path in sorted(_INSPECT_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for value in _collect_status(data):
            seen[value] += 1
            sources.setdefault(value, set()).add(path.stem)

    cassette, _ = _load_cassette_market()
    if cassette:
        for value in _collect_status(cassette):
            seen[value] += 1
            sources.setdefault(value, set()).add("cassette:market")

    # Unabhängige zweite Stichprobe von einem anderen Tag — sonst zählt man die
    # Live-Daten faktisch doppelt, wenn die Cassette am selben Tag entstand.
    sample, _ = _load_prob_sample()
    if sample:
        for value in _collect_status(sample):
            seen[value] += 1
            sources.setdefault(value, set()).add("archiv:2026-08-31")

    if not seen:
        print("  Keine Daten — erst `python -m scripts.inspect_endpoints`.")
        return
    print(f"  PlayerStatus kennt: {sorted(known)}")
    print(f"    {'st':>4} {'Vorkommen':>10}  Quellen")
    for value in sorted(seen):
        mark = " " if value in known else "!"
        print(f"  {mark} {value:>4} {seen[value]:>10}  {', '.join(sorted(sources[value]))}")
    unknown = sorted(set(seen) - known)
    print(f"  ⇒ Unbekannte Werte in dieser Stichprobe: {unknown or '(keine)'}")
    print("  Achtung: `st` steht auch in User-/Match-Objekten und heißt dort etwas anderes;")
    print("  gewertet werden nur Objekte mit Spieler-Markern (pi/pos/mv).")


def _collect_status(node: Any) -> list[int]:
    """Sammelt `st` nur aus Objekten, die erkennbar Spieler beschreiben."""
    out: list[int] = []
    if isinstance(node, dict):
        is_player = bool({"pos", "mv", "pi"} & node.keys())
        value = node.get("st")
        if is_player and isinstance(value, int) and not isinstance(value, bool):
            out.append(value)
        for child in node.values():
            out.extend(_collect_status(child))
    elif isinstance(node, list):
        for child in node:
            out.extend(_collect_status(child))
    return out


# -- F4: mvud-Semantik ----------------------------------------------------


def f4_mvud_semantics() -> None:
    _headline("F4", "Ist `mvud` der nächste oder der letzte MW-Update-Zeitpunkt?")
    observations = _load_mvud_log()

    live = _load_inspect("market")
    if live and live.get("mvud") and live.get("_fetched_at"):
        observations = _append_observation(
            observations, str(live["_fetched_at"]), str(live["mvud"])
        )

    cassette, recorded = _load_cassette_market()
    if cassette and cassette.get("mvud") and recorded:
        with_tz = _parse_http_date(recorded)
        if with_tz:
            observations = _append_observation(observations, with_tz, str(cassette["mvud"]))

    if not observations:
        print("  Keine Beobachtungen — erst `python -m scripts.inspect_endpoints`.")
        return

    print(f"    {'abgerufen':<26} {'mvud':<26} Lage")
    verdicts: set[str] = set()
    for fetched, mvud in observations:
        hours = (_parse_iso(mvud) - _parse_iso(fetched)).total_seconds() / 3600
        verdict = "ZUKUNFT" if hours > 0 else "VERGANGENHEIT"
        verdicts.add(verdict)
        stamp = _parse_iso(fetched).replace(microsecond=0).isoformat()
        print(f"    {stamp:<26} {mvud:<26} {verdict} ({hours:+.1f} h)")

    _MVUD_LOG.parent.mkdir(parents=True, exist_ok=True)
    _MVUD_LOG.write_text(json.dumps(observations, indent=2) + "\n")

    if verdicts == {"ZUKUNFT"}:
        print(
            "  ⇒ `mvud` liegt in jeder Beobachtung in der Zukunft = **nächster** Update-Zeitpunkt."
        )
        print("     Gegenprobe für die Restunsicherheit: ein Abruf zwischen 20:00 und 24:00 UTC —")
        print("     springt `mvud` dann auf den Folgetag, ist die Deutung endgültig bestätigt.")
    elif verdicts == {"VERGANGENHEIT"}:
        print("  ⇒ `mvud` liegt stets in der Vergangenheit = **letzter** Update-Zeitpunkt.")
    else:
        print("  ⇒ Uneinheitlich — weitere Beobachtungen nötig.")
    print(f"  Protokoll: {_MVUD_LOG}")


def _load_mvud_log() -> list[list[str]]:
    if not _MVUD_LOG.exists():
        return []
    try:
        data = json.loads(_MVUD_LOG.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return [list(row) for row in data if isinstance(row, list) and len(row) == 2]  # noqa: PLR2004


def _append_observation(log: list[list[str]], fetched: str, mvud: str) -> list[list[str]]:
    row = [fetched, mvud]
    if row not in log:
        log = [*log, row]
    return sorted(log)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_http_date(value: str) -> str | None:
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return None


# -- F5: Laufzeit eigener Listings ---------------------------------------


def f5_own_listing_runtime() -> None:
    _headline("F5", "Wie lange laufen *eigene* Listings maximal?")
    market = _load_inspect("market")
    if market is None:
        print("  Keine market.json — erst `python -m scripts.inspect_endpoints`.")
        return

    items = [x for x in market.get("it", []) if isinstance(x, dict)]
    own = [x for x in items if _seller_id(x) in _OWN_USER_IDS]
    foreign = [x for x in items if _seller_id(x) not in _OWN_USER_IDS]

    exs_foreign = [int(x["exs"]) for x in foreign if x.get("exs") is not None]
    if exs_foreign:
        print(
            f"  Fremd-/Kickbase-Listings: {len(exs_foreign)}/{len(foreign)} mit `exs`, "
            f"Spanne {min(exs_foreign) / 3600:.1f} bis {max(exs_foreign) / 3600:.1f} h"
        )
    if not own:
        print("  Kein eigenes Listing aktiv — Frage bleibt offen.")
        print("  Rest-Verfahren: eigenen Spieler listen, dann dieses Skript erneut laufen.")
        return

    for item in own:
        exs = item.get("exs")
        listed_at = item.get("dt")
        runtime = f"{int(exs) / 3600:.1f} h" if exs is not None else "KEIN `exs`-Feld"
        print(f"  Eigenes Listing {item.get('i')}: gelistet {listed_at} · Restlaufzeit: {runtime}")
    if all(x.get("exs") is None for x in own):
        print("  ⇒ Eigene Listings tragen **kein** `exs` — im Gegensatz zu Kickbase-Listings.")
        print("     Deutung: sie laufen nicht automatisch ab (oder Kickbase liefert die")
        print("     Restlaufzeit nicht aus). Für die Prompt-Formulierung heißt das: ein")
        print("     eigenes Listing blockiert den Kaderplatz, bis es angenommen oder")
        print("     zurückgezogen wird — Sofortverkauf bleibt der garantierte Plan B.")
        print("  Rest-Verfahren: dasselbe Listing nach >72 h erneut prüfen (ist es noch da?).")


def main() -> int:
    if not _INSPECT_DIR.exists():
        print(f"FEHLER: {_INSPECT_DIR} fehlt — erst `python -m scripts.inspect_endpoints`.")
        return 2
    print("Offene Fragen aus docs/optimizing_plan.md §8 — Auswertung gegen echte Payloads")
    f1_offers_array()
    f2_prob_direction()
    f3_status_values()
    f4_mvud_semantics()
    f5_own_listing_runtime()
    print("\nErgebnisse in docs/api_notes.md und docs/optimizing_plan.md §8 nachtragen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
