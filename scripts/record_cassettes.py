# ruff: noqa: ASYNC240 — Ein-Schuss-Aufnahme-Script.
"""Nimmt VCR-Cassettes für die Read-only-Kickbase-Endpoints auf.

Führt echte API-Requests aus. Alle sensiblen Daten (Passwort, JWT-Token,
Email, eigene User-ID sowie Klarnamen/IDs **fremder Manager**) werden vor dem
Persistieren ersetzt — siehe `tests/infrastructure/kickbase/vcr_config.py`.

Zwei Gruppen:
- **Client-Cassettes**: über `HttpxKickbaseClient`, damit die Contract-Tests den
  echten Parse-Pfad prüfen.
- **Roh-Cassettes** (P0-0.3): Endpunkte, für die es noch keine Client-Methode
  gibt. Sie werden jetzt aufgenommen, damit P0-4 (Aufstellung), P1-8 (Form),
  P1-9 (Limits) und P2-11/12 (Spielplan, Ligakontext) offline entwickelt werden
  können, ohne erneut live zu gehen.

⚠️ Für P0-2 wird eine Aufnahme **mit einem eingegangenen Gebot** gebraucht
(`ofc > 0` im Market-Payload). Das Skript meldet am Ende, ob der Fall dabei war.

Verwendung:
    python -m scripts.record_cassettes

Erwartet in .env.local:
    KICKBASE_TEST_EMAIL, KICKBASE_TEST_PASSWORD
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import yaml
from app.infrastructure.kickbase.client import HttpxKickbaseClient
from app.infrastructure.kickbase.config import KickbaseClientConfig
from dotenv import load_dotenv
from tests.infrastructure.kickbase.vcr_config import CASSETTE_DIR, make_vcr

# Endpunkte ohne Client-Methode: (Cassette-Name, Pfad-Template).
_RAW_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("lineup", "/v4/leagues/{l}/lineup"),
    ("lineup_overview", "/v4/leagues/{l}/lineup/overview"),
    ("league_squad", "/v4/leagues/{l}/squad"),
    ("ranking", "/v4/leagues/{l}/ranking"),
    ("manager_transfer", "/v4/leagues/{l}/managers/{m}/transfer"),
    ("competition_table", "/v4/competitions/1/table"),
    ("player_detail", "/v4/leagues/{l}/players/{p}"),
    ("player_performance", "/v4/leagues/{l}/players/{p}/performance"),
)


def _fast_config() -> KickbaseClientConfig:
    """Der Rate-Limiter des Clients bleibt aktiv — nur der Jitter entfällt."""
    return KickbaseClientConfig()


async def _record_raw(token: str, name: str, path: str) -> None:
    """Nimmt einen Endpunkt ohne Client-Methode als eigene Cassette auf."""
    config = KickbaseClientConfig()
    with make_vcr(name, record_mode="new_episodes").use_cassette(f"{name}.yaml"):
        async with httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.request_timeout_s,
            headers={
                "User-Agent": config.user_agent,
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
        ) as http:
            resp = await http.get(path)
            status = "✓" if resp.status_code < 400 else "✗"  # noqa: PLR2004 — HTTP-Fehlerschwelle
            print(f"  {status} {name}.yaml ({resp.status_code}, {len(resp.content)} bytes)")


def _report_offer_coverage() -> None:
    """P0-2 braucht eine Aufnahme mit `ofc > 0` — sagen, ob sie dabei war."""
    path = CASSETTE_DIR / "market.yaml"
    if not path.exists():
        return
    try:
        raw = yaml.safe_load(path.read_text())
        body = raw["interactions"][0]["response"]["body"]["string"]
        items = json.loads(body).get("it", [])
    except (OSError, KeyError, IndexError, ValueError):
        return

    with_offers = [x for x in items if isinstance(x, dict) and int(x.get("ofc") or 0) > 0]
    if with_offers:
        print(f"\n  * market.yaml: {len(with_offers)} Listing(s) mit Geboten — P0-2 kann loslegen.")
        return
    print("\n  ! market.yaml enthält kein Listing mit ofc > 0.")
    print("    P0-2 (Offers-Array, §8/F1) braucht eine Aufnahme mit einem echten Gebot:")
    print("    eigenen Spieler listen, warten bis in der App ein Gebot eingeht, erneut aufnehmen.")


async def _record_client_cassettes(client: HttpxKickbaseClient, user_id: str, token: str) -> bool:
    """Nimmt alle Cassettes auf. False, wenn keine Liga existiert."""
    with make_vcr("leagues_selection", record_mode="new_episodes").use_cassette(
        "leagues_selection.yaml"
    ):
        leagues = await client.list_leagues()
        print(f"  ✓ leagues_selection.yaml ({len(leagues)} Liga(en))")
    if not leagues:
        print("⚠  Keine Ligen — Rest wird übersprungen.")
        return False
    league = leagues[0]

    with make_vcr("league_me", record_mode="new_episodes").use_cassette("league_me.yaml"):
        league_me = await client.get_league_me(league.id)
        print(f"  ✓ league_me.yaml (Budget {league_me.budget})")

    with make_vcr("squad", record_mode="new_episodes").use_cassette("squad.yaml"):
        squad = await client.get_squad(league.id, user_id)
        print(f"  ✓ squad.yaml ({len(squad.players)} Spieler)")

    with make_vcr("market", record_mode="new_episodes").use_cassette("market.yaml"):
        market = await client.get_market(league.id)
        print(f"  ✓ market.yaml ({len(market)} Angebote)")

    with make_vcr("matchdays", record_mode="new_episodes").use_cassette("matchdays.yaml"):
        matchdays = await client.list_matchdays()
        print(f"  ✓ matchdays.yaml ({len(matchdays)} Spieltage)")

    player_id = squad.players[0].player.id if squad.players else None
    if player_id:
        with make_vcr("market_value", record_mode="new_episodes").use_cassette("market_value.yaml"):
            history = await client.get_market_value_history(league.id, player_id, days=7)
            print(f"  ✓ market_value.yaml ({len(history)} Punkte für Spieler {player_id})")
    else:
        print("⚠  Squad leer — market_value.yaml übersprungen.")

    print("\n  Roh-Endpunkte (noch ohne Client-Methode):")
    for name, template in _RAW_ENDPOINTS:
        if "{p}" in template and not player_id:
            print(f"  - {name} übersprungen (keine Spieler-ID)")
            continue
        path = (
            template.replace("{l}", league.id)
            .replace("{m}", user_id)
            .replace("{p}", player_id or "")
        )
        await _record_raw(token, name, path)
    return True


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
    with make_vcr("login", record_mode="new_episodes").use_cassette("login.yaml"):
        async with HttpxKickbaseClient(config=_fast_config()) as client:
            session = await client.login(email, password)
            print(f"  ✓ login.yaml (User-ID {session.user_id})")

    # 2-N: Read-only-Endpoints — jeweils in eigener Cassette
    async with HttpxKickbaseClient(config=_fast_config()) as client:
        session = await client.login(email, password)
        if not await _record_client_cassettes(client, session.user_id, session.token):
            return 0

    _report_offer_coverage()
    print("\n✓ Alle Cassettes geschrieben.")
    print("  Prüfen: pytest tests/infrastructure/kickbase/test_cassette_privacy.py")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_record()))
