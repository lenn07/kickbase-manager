# ruff: noqa: PLR0911, ASYNC240 — Ein-Schuss-Script, nicht Hot-Path.
"""Einmaliger Sanity-Check: echter Kickbase-Login mit Credentials aus .env.local.

Verwendung:
    python -m scripts.verify_credentials

Erwartet in .env.local:
    KICKBASE_TEST_EMAIL=...
    KICKBASE_TEST_PASSWORD=...

Macht genau zwei Requests:
    POST /v4/user/login
    GET  /v4/leagues/selection

Kein Kader-, Markt- oder Trade-Call. Token und Passwort werden nie ausgegeben.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from app.domain.exceptions import AuthError, KickbaseError
from app.infrastructure.kickbase.client import HttpxKickbaseClient
from app.infrastructure.kickbase.config import KickbaseClientConfig
from dotenv import load_dotenv


def _mask(value: str, keep: int = 3) -> str:
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * (len(value) - keep)


async def _run() -> int:
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.INFO)

    env_path = Path(__file__).resolve().parent.parent / ".env.local"
    if not env_path.exists():
        print(f"FEHLER: {env_path} nicht gefunden.", file=sys.stderr)
        return 2
    load_dotenv(env_path)

    email = os.environ.get("KICKBASE_TEST_EMAIL", "").strip()
    password = os.environ.get("KICKBASE_TEST_PASSWORD", "").strip()
    if not email or not password or "REPLACE_ME" in email or "REPLACE_ME" in password:
        print(
            "FEHLER: KICKBASE_TEST_EMAIL / KICKBASE_TEST_PASSWORD nicht gesetzt.", file=sys.stderr
        )
        return 2

    print(f"→ Login-Versuch für {_mask(email)}@…")
    config = KickbaseClientConfig()
    if verbose:
        print(f"  User-Agent: {config.user_agent}")
        print(f"  Base-URL:   {config.base_url}")

    async with HttpxKickbaseClient(config=config) as client:
        try:
            session = await client.login(email, password)
        except AuthError as exc:
            print(f"✗ Login fehlgeschlagen: {exc}", file=sys.stderr)
            return 1
        except KickbaseError as exc:
            print(f"✗ API-Fehler: {exc}", file=sys.stderr)
            return 1

        print("✓ Login erfolgreich")
        print(f"  User-ID:      {session.user_id}")
        print(f"  Token gültig: bis {session.token_expires_at.isoformat()}")

        try:
            leagues = await client.list_leagues()
        except KickbaseError as exc:
            print(f"✗ Ligen-Abruf fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

        if not leagues:
            print("⚠  Account hat keine Ligen — Setup-Wizard könnte später leer sein.")
            return 0

        print(f"\n✓ {len(leagues)} Liga(en) gefunden:")
        for league in leagues:
            print(f"  - [{league.id}] {league.name}")
        print("\nTipp: die League-ID brauchen wir später für Squad-/Markt-Abrufe (Phase 1.6).")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
