# ruff: noqa: ASYNC240 — Einmaliger Inspektions-Script.
"""Loggt die Roh-Responses aller Read-only-Endpoints als JSON in tmp/inspect/.

Grundlage für den Optimizing-Plan (§6, P0-0.1): bevor DTOs erweitert werden,
brauchen wir die tatsächlichen v4-Feldnamen aus echten Payloads. Alle sensiblen
Werte (Email, Token, Klarnamen, Profilbild-URLs) werden vor dem Schreiben durch
Platzhalter ersetzt.

⚠️ Ausschließlich Read-only-Endpoints. `/v4/bonus/collect` ist bewusst **nicht**
enthalten — der GET sammelt vermutlich tatsächlich den Tagesbonus ein.

Verwendung:
    python -m scripts.inspect_endpoints

Danach:
    python -m scripts.dump_keys
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

# Kickbase bannt bei zu schnellen Serien. Das Skript ist ein Einmal-Lauf, ein
# fester Abstand reicht als Schutz (der Produktivpfad nutzt AsyncRateLimiter).
_THROTTLE_S = 1.2

# Ab hier gilt eine Response als Fehler (für die Konsolen-Markierung).
_HTTP_ERROR = 400

_USER_AGENT = "Kickbase/4.5.0 (iPhone; iOS 17.5.1; Scale/3.00)"

_TOKEN_KEYS = frozenset({"tkn", "token", "chttkn"})
_EMAIL_KEYS = frozenset({"em", "email", "vemail", "emve"})
_IMAGE_KEYS = frozenset({"profile", "uim", "sfb", "efb", "pim"})
# Marker, an denen ein Dict als User-Kontext erkannt wird — dann wird `n`/`name`
# als Klarname behandelt. `i` ist bewusst dabei: Ranking- und Seller-Objekte
# tragen die User-ID als `i`, nicht als `id`.
_USER_CONTEXT_MARKERS = frozenset({"em", "email", "vemail", "id", "i", "profile", "uim", "unm"})
_NAME_KEYS = frozenset({"name", "unm", "creator", "othnm"})


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Ein Read-only-Endpoint samt Zieldatei. `path` darf {l}/{m}/{p} enthalten."""

    name: str
    path: str


# Reihenfolge = Aufrufreihenfolge. `{l}` Liga, `{m}` Manager (= eigene User-ID),
# `{p}` ein Spieler aus dem eigenen Kader.
_ENDPOINTS: tuple[Endpoint, ...] = (
    # --- bereits vor P0-0.1 erfasst ---
    Endpoint("league_me", "/v4/leagues/{l}/me"),
    Endpoint("squad", "/v4/leagues/{l}/managers/{m}/squad"),
    Endpoint("market", "/v4/leagues/{l}/market"),
    Endpoint("matchdays", "/v4/competitions/1/matchdays"),
    # --- neu in P0-0.1 ---
    Endpoint("lineup", "/v4/leagues/{l}/lineup"),
    Endpoint("lineup_overview", "/v4/leagues/{l}/lineup/overview"),
    Endpoint("lineup_selection", "/v4/leagues/{l}/lineup/selection"),
    # Antwortet mit HTTP 500 {"err":2,"errMsg":"NotFound"} — bleibt in der Liste,
    # damit der Contract-Checker meldet, falls Kickbase ihn nachliefert.
    Endpoint("league_settings", "/v4/leagues/{l}/settings"),
    Endpoint("ranking", "/v4/leagues/{l}/ranking"),
    Endpoint("league_squad", "/v4/leagues/{l}/squad"),
    Endpoint("manager_transfer", "/v4/leagues/{l}/managers/{m}/transfer"),
    Endpoint("competition_table", "/v4/competitions/1/table"),
    Endpoint("player_detail", "/v4/leagues/{l}/players/{p}"),
    Endpoint("player_performance", "/v4/leagues/{l}/players/{p}/performance"),
    Endpoint("player_marketvalue", "/v4/leagues/{l}/players/{p}/marketvalue/365"),
)


def _redact(node: Any, *, in_user_context: bool = False) -> Any:
    if isinstance(node, dict):
        user_ctx = in_user_context or bool(_USER_CONTEXT_MARKERS & node.keys())
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in _TOKEN_KEYS and isinstance(value, str):
                out[key] = "REDACTED_JWT"
            elif key in _EMAIL_KEYS and isinstance(value, str):
                out[key] = "redacted@example.com"
            elif key in _IMAGE_KEYS and isinstance(value, str):
                out[key] = "redacted/image.png"
            elif isinstance(value, str) and _is_person_name(key, user_ctx=user_ctx):
                out[key] = "Redacted"
            else:
                out[key] = _redact(value, in_user_context=user_ctx)
        return out
    if isinstance(node, list):
        return [_redact(x, in_user_context=in_user_context) for x in node]
    return node


def _is_person_name(key: str, *, user_ctx: bool) -> bool:
    """`n` ist je nach Kontext Spieler-, Team- *oder* Klarname — nur im
    User-Kontext redigieren, sonst verlieren wir Team- und Liganamen."""
    return key in _NAME_KEYS or (key == "n" and user_ctx)


def _write(out_dir: Path, name: str, payload: Any) -> None:
    (out_dir / f"{name}.json").write_text(
        json.dumps(_redact(payload), indent=2, ensure_ascii=False) + "\n"
    )


async def _run() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    load_dotenv(repo_root / ".env.local")
    email = os.environ.get("KICKBASE_TEST_EMAIL", "").strip()
    password = os.environ.get("KICKBASE_TEST_PASSWORD", "").strip()
    if not email or not password:
        print("FEHLER: Credentials fehlen in .env.local", file=sys.stderr)
        return 2

    out_dir = repo_root / "tmp" / "inspect"
    out_dir.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(
        base_url="https://api.kickbase.com",
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        timeout=15.0,
    ) as http:
        login_resp = await http.post("/v4/user/login", json={"em": email, "pass": password})
        login_json = login_resp.json()
        token = login_json.get("tkn") or login_json.get("token")
        user_id = login_json.get("u", {}).get("i") or login_json.get("u", {}).get("id")
        if not token or not user_id:
            print(f"FEHLER: Login-Response unerwartet: {list(login_json.keys())}", file=sys.stderr)
            return 1

        _write(out_dir, "login", login_json)
        print("  ✓ login.json")
        auth = {"Authorization": f"Bearer {token}"}

        await asyncio.sleep(_THROTTLE_S)
        leagues_resp = await http.get("/v4/leagues/selection", headers=auth)
        leagues_json = leagues_resp.json()
        _write(out_dir, "leagues_selection", leagues_json)
        print("  ✓ leagues_selection.json")

        leagues = leagues_json.get("it") or leagues_json.get("leagues") or []
        if not leagues:
            print("Keine Ligen — Ende.", file=sys.stderr)
            return 1
        league_id = str(leagues[0].get("i") or leagues[0].get("id"))

        player_id = await _first_squad_player_id(http, auth, league_id, user_id, out_dir)
        placeholders = {"{l}": league_id, "{m}": str(user_id), "{p}": player_id or ""}

        for endpoint in _ENDPOINTS:
            await _fetch_endpoint(http, auth, endpoint, placeholders, out_dir)

        _report_offers(out_dir)

    print(f"\nAlle Roh-Responses liegen in: {out_dir}")
    print("Nächster Schritt: python -m scripts.dump_keys")
    return 0


async def _fetch_endpoint(
    http: httpx.AsyncClient,
    auth: dict[str, str],
    endpoint: Endpoint,
    placeholders: dict[str, str],
    out_dir: Path,
) -> None:
    path = endpoint.path
    if "{p}" in path and not placeholders["{p}"]:
        print(f"  - {endpoint.name} übersprungen (keine Spieler-ID)")
        return
    for token_key, value in placeholders.items():
        path = path.replace(token_key, value)

    await asyncio.sleep(_THROTTLE_S)
    try:
        resp = await http.get(path, headers=auth)
    except httpx.RequestError as exc:
        print(f"  ✗ {endpoint.name}: Netzwerkfehler {exc}", file=sys.stderr)
        return

    data: Any = {}
    if resp.content:
        try:
            data = resp.json()
        except ValueError:
            data = {"_non_json_body": resp.text[:2000]}
    _write(
        out_dir,
        endpoint.name,
        {
            "_status": resp.status_code,
            "_path": path,
            "_fetched_at": datetime.now(UTC).isoformat(),
            **_as_dict(data),
        },
    )
    mark = "✓" if resp.status_code < _HTTP_ERROR else "✗"
    print(f"  {mark} {endpoint.name}.json ({resp.status_code}, {len(resp.content)} bytes)")


def _report_offers(out_dir: Path) -> None:
    """Schreibt Markt-Items mit Geboten in `offers_found.json` (offene Frage F1).

    Ein GET auf `/market/{pid}/offers` beantwortet Kickbase mit 405 (nur POST),
    ebenso `/market/{pid}` (nur DELETE) — das Gebots-Array kann also nur im
    `/market`-Payload selbst stecken. Sichtbar wird es erst, wenn `ofc > 0` ist.
    """
    market_file = out_dir / "market.json"
    if not market_file.exists():
        return
    try:
        items = json.loads(market_file.read_text()).get("it", [])
    except (OSError, json.JSONDecodeError):
        return

    with_offers = [x for x in items if isinstance(x, dict) and int(x.get("ofc") or 0) > 0]
    if not with_offers:
        print("\n  * Kein Markt-Eintrag mit ofc > 0 — F1 (Offers-Array) bleibt offen.")
        print("    Verfahren: eigenen Spieler listen, warten bis ofc > 0, Skript erneut laufen.")
        return

    _write(out_dir, "offers_found", {"it": with_offers})
    keys = sorted({k for x in with_offers for k in x})
    print(f"\n  ★ {len(with_offers)} Eintrag/Einträge mit ofc > 0 → offers_found.json")
    print(f"    Keys: {', '.join(keys)}")


def _as_dict(data: Any) -> dict[str, Any]:
    """Responses sind fast immer Objekte; Listen werden unter `it` eingehängt."""
    if isinstance(data, dict):
        return data
    return {"it": data}


async def _first_squad_player_id(
    http: httpx.AsyncClient,
    auth: dict[str, str],
    league_id: str,
    user_id: str,
    out_dir: Path,
) -> str | None:
    """Holt den Kader vorab, weil `{p}`-Endpoints eine echte Spieler-ID brauchen."""
    await asyncio.sleep(_THROTTLE_S)
    resp = await http.get(f"/v4/leagues/{league_id}/managers/{user_id}/squad", headers=auth)
    if resp.status_code >= _HTTP_ERROR or not resp.content:
        print(f"  ⚠ Kader nicht ladbar ({resp.status_code}) — {{p}}-Endpoints entfallen")
        return None
    data = resp.json()
    _write(out_dir, "squad", data)
    items = data.get("it") or data.get("players") or []
    if not items:
        return None
    first = items[0]
    pid = first.get("pi") or first.get("i") or first.get("id")
    return str(pid) if pid else None


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
