# ruff: noqa: ASYNC240 — Einmaliger Inspektions-Script.
"""Loggt die Roh-Responses aller Read-only-Endpoints als JSON in tmp/inspect/.

Vor der DTO-Anpassung nutzen wir das, um die tatsächlichen v4-Feldnamen
in Squad/Market/Matchdays/MarketValue zu sehen. Alle sensiblen Werte
(Email, Token) werden vor dem Schreiben durch Platzhalter ersetzt.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


def _redact(node: Any) -> Any:
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in {"tkn", "token"} and isinstance(v, str):
                out[k] = "REDACTED_JWT"
            elif k in {"em", "email"} and isinstance(v, str):
                out[k] = "redacted@example.com"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(node, list):
        return [_redact(x) for x in node]
    return node


async def _run() -> int:
    env_path = Path(__file__).resolve().parent.parent / ".env.local"
    load_dotenv(env_path)
    email = os.environ.get("KICKBASE_TEST_EMAIL", "").strip()
    password = os.environ.get("KICKBASE_TEST_PASSWORD", "").strip()
    if not email or not password:
        print("FEHLER: Credentials fehlen in .env.local", file=sys.stderr)
        return 2

    out_dir = Path(__file__).resolve().parent.parent / "tmp" / "inspect"
    out_dir.mkdir(parents=True, exist_ok=True)

    ua = "Kickbase/4.5.0 (iPhone; iOS 17.5.1; Scale/3.00)"
    async with httpx.AsyncClient(
        base_url="https://api.kickbase.com",
        headers={"User-Agent": ua, "Accept": "application/json"},
        timeout=15.0,
    ) as http:
        login_resp = await http.post("/v4/user/login", json={"em": email, "pass": password})
        login_json = login_resp.json()
        token = login_json.get("tkn") or login_json.get("token")
        user_id = login_json.get("u", {}).get("i") or login_json.get("u", {}).get("id")
        if not token or not user_id:
            print(f"FEHLER: Login-Response unerwartet: {list(login_json.keys())}", file=sys.stderr)
            return 1

        (out_dir / "login.json").write_text(
            json.dumps(_redact(login_json), indent=2, ensure_ascii=False)
        )
        print(f"  ✓ login.json ({len(json.dumps(login_json))} bytes)")

        auth = {"Authorization": f"Bearer {token}"}

        leagues_resp = await http.get("/v4/leagues/selection", headers=auth)
        leagues_json = leagues_resp.json()
        (out_dir / "leagues_selection.json").write_text(
            json.dumps(_redact(leagues_json), indent=2, ensure_ascii=False)
        )
        print("  ✓ leagues_selection.json")
        leagues = leagues_json.get("it") or leagues_json.get("leagues") or []
        if not leagues:
            print("Keine Ligen — Ende.", file=sys.stderr)
            return 1
        league_id = leagues[0].get("i") or leagues[0].get("id")

        for path, name in [
            (f"/v4/leagues/{league_id}/me", "league_me"),
            (f"/v4/leagues/{league_id}/managers/{user_id}/squad", "squad"),
            (f"/v4/leagues/{league_id}/market", "market"),
            ("/v4/competitions/1/matchdays", "matchdays"),
        ]:
            resp = await http.get(path, headers=auth)
            data = resp.json() if resp.content else {}
            (out_dir / f"{name}.json").write_text(
                json.dumps(_redact(data), indent=2, ensure_ascii=False)
            )
            print(f"  ✓ {name}.json ({resp.status_code}, {len(resp.content)} bytes)")

        # Player-ID für market value aus Squad extrahieren
        squad_json = json.loads((out_dir / "squad.json").read_text())
        squad_items = squad_json.get("it") or squad_json.get("players") or []
        if squad_items:
            first = squad_items[0]
            pid = first.get("pi") or first.get("i") or first.get("id")
            if pid:
                resp = await http.get(
                    f"/v4/leagues/{league_id}/players/{pid}/marketvalue/7",
                    headers=auth,
                )
                (out_dir / "market_value.json").write_text(
                    json.dumps(_redact(resp.json() if resp.content else {}), indent=2)
                )
                print(f"  ✓ market_value.json (player {pid})")

    print(f"\nAlle Roh-Responses liegen in: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
