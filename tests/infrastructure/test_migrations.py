"""Tests der Schema-Angleichung für bestehende Datenbanken.

Der Ernstfall ist eine `kb.db`, die vor einer Modelländerung entstanden ist:
`create_all` ergänzt fehlende Spalten nicht, der Fehler zeigt sich erst zur
Laufzeit. Diese Tests bauen genau so eine Altdatenbank nach.
"""

from __future__ import annotations

from pathlib import Path

from app.infrastructure.persistence.db import init_db, make_engine
from app.infrastructure.persistence.migrations import apply_migrations
from sqlalchemy import text
from sqlalchemy.engine import Engine


def _columns(engine: Engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}


def _legacy_db(tmp_path: Path) -> Engine:
    """Eine Datenbank im Stand vor den Modelländerungen dieses Pakets."""
    engine = make_engine(tmp_path / "old.db")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE settings ("
                "  id INTEGER PRIMARY KEY,"
                "  user_id INTEGER NOT NULL,"
                "  interval_min INTEGER NOT NULL,"
                "  min_action_score FLOAT NOT NULL"
                ")"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE trade_log ("
                "  id INTEGER PRIMARY KEY,"
                "  user_id INTEGER NOT NULL,"
                "  action VARCHAR NOT NULL"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO settings (user_id, interval_min, min_action_score) VALUES (1, 90, 0.6)"
            )
        )
        conn.execute(text("INSERT INTO trade_log (user_id, action) VALUES (1, 'BUY')"))
    return engine


def test_adds_the_missing_superseded_column(tmp_path: Path) -> None:
    engine = _legacy_db(tmp_path)
    assert "superseded_at" not in _columns(engine, "trade_log")

    apply_migrations(engine)

    assert "superseded_at" in _columns(engine, "trade_log")


def test_drops_the_obsolete_threshold_column(tmp_path: Path) -> None:
    engine = _legacy_db(tmp_path)

    apply_migrations(engine)

    assert "min_action_score" not in _columns(engine, "settings")


def test_keeps_existing_rows(tmp_path: Path) -> None:
    """Die Angleichung ist verlustfrei — sie fasst nur das Schema an."""
    engine = _legacy_db(tmp_path)

    apply_migrations(engine)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT interval_min FROM settings")).scalar() == 90
        assert conn.execute(text("SELECT action FROM trade_log")).scalar() == "BUY"


def test_running_twice_changes_nothing(tmp_path: Path) -> None:
    engine = _legacy_db(tmp_path)
    apply_migrations(engine)
    before = (_columns(engine, "settings"), _columns(engine, "trade_log"))

    apply_migrations(engine)

    assert (_columns(engine, "settings"), _columns(engine, "trade_log")) == before


def test_tolerates_tables_that_do_not_exist_yet(tmp_path: Path) -> None:
    """Auf einer leeren Datei darf die Angleichung nicht stolpern."""
    engine = make_engine(tmp_path / "empty.db")
    apply_migrations(engine)  # kein Fehler


def test_a_fresh_database_never_gets_the_obsolete_column(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "fresh.db")
    init_db(engine)
    assert "min_action_score" not in _columns(engine, "settings")
    assert "superseded_at" in _columns(engine, "trade_log")
