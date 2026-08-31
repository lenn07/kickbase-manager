"""Tests für den DbSessionStore — Roundtrip via In-Memory-SQLite + Vault."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.domain.gateways import SessionStore
from app.domain.models import Session as KbSession
from app.infrastructure.crypto.vault import FernetVault

# Import registriert Tabellen bei SQLModel.metadata.
from app.infrastructure.persistence import models as _models  # noqa: F401
from app.infrastructure.persistence.repositories import UserRepository
from app.infrastructure.persistence.session_store import DbSessionStore
from sqlmodel import Session, SQLModel, create_engine


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def vault(tmp_path: Path) -> FernetVault:
    return FernetVault.load_or_create(data_dir=tmp_path)


def _seed_user(db_session: Session, vault: FernetVault) -> None:
    UserRepository(db_session).upsert_credentials(
        email="user@example.com", encrypted_password=vault.encrypt("secret")
    )


def test_conforms_to_session_store_protocol(db_session: Session, vault: FernetVault) -> None:
    store = DbSessionStore(db_session, vault)
    assert isinstance(store, SessionStore)


async def test_load_returns_none_when_no_user(db_session: Session, vault: FernetVault) -> None:
    store = DbSessionStore(db_session, vault)
    assert await store.load_session() is None
    assert await store.load_credentials() is None


async def test_load_returns_none_when_user_has_no_token(
    db_session: Session, vault: FernetVault
) -> None:
    _seed_user(db_session, vault)
    store = DbSessionStore(db_session, vault)
    assert await store.load_session() is None


async def test_save_then_load_session_roundtrip(db_session: Session, vault: FernetVault) -> None:
    _seed_user(db_session, vault)
    store = DbSessionStore(db_session, vault)
    expiry = datetime.now(UTC) + timedelta(hours=1)
    original = KbSession(
        token="jwt.foo.bar",
        token_expires_at=expiry,
        user_id="kb-42",
        email="user@example.com",
    )

    await store.save_session(original)
    loaded = await store.load_session()

    assert loaded is not None
    assert loaded.token == "jwt.foo.bar"
    assert loaded.user_id == "kb-42"
    assert loaded.email == "user@example.com"
    # SQLite verwirft tz-Info → Store stellt UTC wieder her.
    assert loaded.token_expires_at.tzinfo is not None
    assert abs((loaded.token_expires_at - expiry).total_seconds()) < 1


async def test_save_session_without_user_raises(db_session: Session, vault: FernetVault) -> None:
    store = DbSessionStore(db_session, vault)
    session = KbSession(
        token="x",
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        user_id="u",
        email="a@b.de",
    )
    with pytest.raises(RuntimeError):
        await store.save_session(session)


async def test_load_credentials_decrypts_password(db_session: Session, vault: FernetVault) -> None:
    _seed_user(db_session, vault)
    store = DbSessionStore(db_session, vault)

    creds = await store.load_credentials()

    assert creds == ("user@example.com", "secret")


async def test_load_session_returns_none_on_bad_cipher(tmp_path: Path, db_session: Session) -> None:
    """Falls der Master-Key rotiert wurde, kann der Store das alte Token nicht entschlüsseln."""
    write_vault = FernetVault.load_or_create(data_dir=tmp_path / "keys-a")
    read_vault = FernetVault.load_or_create(data_dir=tmp_path / "keys-b")
    _seed_user(db_session, write_vault)
    write_store = DbSessionStore(db_session, write_vault)
    await write_store.save_session(
        KbSession(
            token="secret-token",
            token_expires_at=datetime.now(UTC) + timedelta(hours=1),
            user_id="u",
            email="user@example.com",
        )
    )

    read_store = DbSessionStore(db_session, read_vault)
    assert await read_store.load_session() is None
    assert await read_store.load_credentials() is None
