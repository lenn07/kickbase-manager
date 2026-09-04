"""OpenLigaDB-Adapter — externe Bundesliga-Daten für das Team-Signal-Feature."""

from app.infrastructure.openligadb.client import HttpxOpenLigaDBClient
from app.infrastructure.openligadb.config import OpenLigaDBConfig

__all__ = ["HttpxOpenLigaDBClient", "OpenLigaDBConfig"]
