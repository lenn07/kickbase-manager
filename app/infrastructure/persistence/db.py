"""SQLite-Engine-Fabrik + Session-Dependency.

WAL-Mode ist auf SD-Karten schonender (ADR-11) und verhindert Reader/Writer-Locking.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

# Wichtig: Import registriert die Tabellen-Klassen bei SQLModel.metadata.
from app.infrastructure.persistence import models as _models  # noqa: F401


def make_engine(db_path: Path | str, *, echo: bool = False) -> Engine:
    url = _to_sqlite_url(db_path)
    # check_same_thread=False, damit FastAPI-Threads dieselbe Connection nutzen können.
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, echo=echo, connect_args=connect_args)
    if url.startswith("sqlite") and not url.endswith(":memory:"):
        _enable_wal(engine)
    return engine


def init_db(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)


def session_scope(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def _to_sqlite_url(db_path: Path | str) -> str:
    if isinstance(db_path, str) and db_path.startswith("sqlite"):
        return db_path
    return f"sqlite:///{Path(db_path)}"


def _enable_wal(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
