"""Konfiguration des OpenLigaDB-Clients."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OpenLigaDBConfig:
    base_url: str = "https://api.openligadb.de"
    league_shortcut: str = "bl1"
    # Saison als Startjahr — OpenLigaDB nutzt "2026" für 2026/27.
    season: str = "2026"
    request_timeout_s: float = 10.0
    user_agent: str = "kickbase-auto-manager/0.1 (+https://github.com/)"
    # In-Memory-TTL für Match-Daten. 6 h ist ein guter Kompromiss:
    # OpenLigaDB aktualisiert Ergebnisse zeitnah, aber wir wollen den Pi
    # nicht bei jedem Tick belasten. Die Cache-Größe bleibt minimal
    # (eine Saison ≈ 306 Matches → wenige kB).
    cache_ttl_s: int = 6 * 3600
    # Wie viele der letzten gespielten Matchdays fließen ins Form-Signal ein.
    form_window_matches: int = 5
