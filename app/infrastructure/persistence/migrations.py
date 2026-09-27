"""Idempotente Schema-Angleichungen für bestehende `kb.db`-Dateien.

`SQLModel.metadata.create_all()` legt **fehlende Tabellen** an, aber keine
fehlenden Spalten. Eine Datenbank, die vor einer Modelländerung entstanden ist,
läuft danach also in `no such column` — und zwar erst beim ersten Zugriff zur
Laufzeit, nicht beim Start.

Alembic wäre für eine Single-User-SQLite-App mit einer Handvoll Tabellen zu
viel Apparat. Stattdessen: eine Liste kleiner Schritte, jeder prüft selbst, ob
er schon gewirkt hat. Sie laufen bei jedem Start, in Reihenfolge, und ein
bereits angewandter Schritt tut nichts.

**Beim Hinzufügen eines Schrittes:** nur additive oder verlustfreie Operationen.
Ein Schritt, der Daten wegwirft, gehört in die Datenverwaltung (`/daten`), wo
der Nutzer ihn auslöst — nicht in einen Start-Hook, der ohne Rückfrage läuft.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

_log = logging.getLogger(__name__)


def apply_migrations(engine: Engine) -> None:
    """Bringt ein bestehendes Schema auf den Stand der aktuellen Modelle."""
    with engine.begin() as conn:
        # trade_log.superseded_at — die Markierung „von der Realität überholt"
        # (Datenverwaltung). Nachträglich ergänzt, deshalb nullable ohne Default:
        # alle Altzeilen gelten als gültig, was genau der Zustand vor der
        # Einführung ist.
        _add_column(conn, "trade_log", "superseded_at", "DATETIME")

        # settings.min_action_score — Schwellwert der entfernten Heuristik-Engine.
        # Die Spalte ist NOT NULL ohne Server-Default; bliebe sie stehen, würde
        # das erste INSERT einer neuen Settings-Zeile daran scheitern.
        _drop_column(conn, "settings", "min_action_score")


def _columns(conn, table: str) -> set[str]:  # type: ignore[no-untyped-def]
    if not _table_exists(conn, table):
        return set()
    rows = conn.execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in rows}


def _table_exists(conn, table: str) -> bool:  # type: ignore[no-untyped-def]
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table},
    ).first()
    return row is not None


def _add_column(conn, table: str, column: str, ddl_type: str) -> None:  # type: ignore[no-untyped-def]
    if not _table_exists(conn, table) or column in _columns(conn, table):
        return
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
    _log.info("Schema-Update: Spalte %s.%s ergänzt.", table, column)


def _drop_column(conn, table: str, column: str) -> None:  # type: ignore[no-untyped-def]
    """Entfernt eine Spalte, wenn sie noch existiert.

    `DROP COLUMN` kann SQLite seit 3.35 (2021). Scheitert es trotzdem — etwa
    weil ein Index auf der Spalte liegt —, bleibt sie stehen und wir loggen:
    eine überzählige Spalte ist ein Schönheitsfehler, ein abgebrochener Start
    wäre ein Ausfall.
    """
    if column not in _columns(conn, table):
        return
    try:
        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
    except Exception as exc:
        _log.warning("Spalte %s.%s konnte nicht entfernt werden: %s", table, column, exc)
        return
    _log.info("Schema-Update: Spalte %s.%s entfernt.", table, column)
