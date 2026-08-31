# ruff: noqa: ASYNC240 — Ein-Schuss-Aufnahme-Script.
"""Nimmt einmalig VCR-Cassettes für die Read-only-Kickbase-Endpoints auf.

Führt echte API-Requests aus. Alle sensiblen Daten (Passwort, JWT-Token,
Email, User-ID) werden vor dem Persistieren durch REDACTED-Platzhalter
ersetzt (siehe tests/infrastructure/kickbase/vcr_config.py).

Verwendung:
    python -m scripts.record_cassettes

Erwartet in .env.local:
    KICKBASE_TEST_EMAIL, KICKBASE_TEST_PASSWORD
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from app.infrastructure.kickbase.client import HttpxKickbaseClient
from dotenv import load_dotenv
from tests.infrastructure.kickbase.vcr_config import CASSETTE_DIR, make_vcr


async def _record() -> int:
    env_path = Path(__file__).resolve().parent.parent / ".env.local"
    if not env_path.exists():
        print(f"FEHLER: {env_path} nicht gefunden.", file=sys.stderr)
        return 2
    load_dotenv(env_path)

    email = os.environ.get("KICKBASE_TEST_EMAIL", "").strip()
    password = os.environ.get("KICKBASE_TEST_PASSWORD", "").strip()
    if not email or not password:
        print("FEHLER: Credentials fehlen in .env.local", file=sys.stderr)
        return 2

    CASSETTE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"→ Cassettes werden geschrieben nach: {CASSETTE_DIR}\n")

    # 1. Login-Cassette
    vcr_login = make_vcr("login", record_mode="new_episodes")
    with vcr_login.use_cassette("login.yaml"):
        async with HttpxKickbaseClient() as client:
            session = await client.login(email, password)
            print(f"  ✓ login.yaml (User-ID {session.user_id})")

    # 2-N: Read-only-Endpoints — jeweils in eigener Cassette
    async with HttpxKickbaseClient() as client:
        session = await client.login(email, password)
        user_id = session.user_id

        vcr_leagues = make_vcr("leagues_selection", record_mode="new_episodes")
        with vcr_leagues.use_cassette("leagues_selection.yaml"):
            leagues = await client.list_leagues()
            print(f"  ✓ leagues_selection.yaml ({len(leagues)} Liga(en))")

        if not leagues:
            print("⚠  Keine Ligen — Squad/Market werden übersprungen.")
            return 0
        league = leagues[0]

        vcr_squad = make_vcr("squad", record_mode="new_episodes")
        with vcr_squad.use_cassette("squad.yaml"):
            squad = await client.get_squad(league.id, user_id)
            print(f"  ✓ squad.yaml ({len(squad.players)} Spieler)")

        vcr_market = make_vcr("market", record_mode="new_episodes")
        with vcr_market.use_cassette("market.yaml"):
            market = await client.get_market(league.id)
            print(f"  ✓ market.yaml ({len(market)} Angebote)")

        vcr_md = make_vcr("matchdays", record_mode="new_episodes")
        with vcr_md.use_cassette("matchdays.yaml"):
            matchdays = await client.list_matchdays()
            print(f"  ✓ matchdays.yaml ({len(matchdays)} Spieltage)")

        if squad.players:
            player_id = squad.players[0].player.id
            vcr_mv = make_vcr("market_value", record_mode="new_episodes")
            with vcr_mv.use_cassette("market_value.yaml"):
                history = await client.get_market_value_history(league.id, player_id, days=7)
                print(f"  ✓ market_value.yaml ({len(history)} Datenpunkte für Spieler {player_id})")
        else:
            print("⚠  Squad leer — market_value.yaml übersprungen.")

    print("\n✓ Alle Cassettes geschrieben.")
    print("  Prüfe: grep -rE '(REDACTED|redacted@)' tests/infrastructure/kickbase/cassettes/")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_record()))
