"""DbSessionStore — persistiert Kickbase-Session + Login-Credentials verschlüsselt.

Verantwortungs-Split:
- Repositories kennen nur Cipher-Text-Bytes.
- Store übernimmt Ver-/Entschlüsselung via `FernetVault` und stellt Domain-`Session`
  bzw. Klartext-Credentials für den Kickbase-Client bereit.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session as DbSession

from app.domain.models import Session
from app.infrastructure.crypto.vault import CryptoError, FernetVault
from app.infrastructure.persistence.repositories import UserRepository


class DbSessionStore:
    def __init__(self, db_session: DbSession, vault: FernetVault) -> None:
        self._users = UserRepository(db_session)
        self._vault = vault

    async def load_session(self) -> Session | None:
        row = self._users.get_singleton()
        if row is None or row.kb_token is None or row.kb_token_expires_at is None:
            return None
        try:
            token = self._vault.decrypt(row.kb_token)
        except CryptoError:
            return None
        return Session(
            token=token,
            token_expires_at=_as_utc(row.kb_token_expires_at),
            user_id=row.kb_user_id,
            email=row.email,
        )

    async def save_session(self, session: Session) -> None:
        row = self._users.get_singleton()
        if row is None:
            # Ohne persistente Credentials macht ein Session-Cache keinen Sinn —
            # der Setup-Flow muss zuerst die Zugangsdaten anlegen.
            raise RuntimeError("Kein User in der DB — Setup muss zuerst Credentials anlegen.")
        self._users.update_session(
            user_id=row.id,  # type: ignore[arg-type]
            kb_user_id=session.user_id or row.kb_user_id,
            kb_token=self._vault.encrypt(session.token),
            kb_token_expires_at=session.token_expires_at,
        )

    async def load_credentials(self) -> tuple[str, str] | None:
        row = self._users.get_singleton()
        if row is None:
            return None
        try:
            password = self._vault.decrypt(row.encrypted_password)
        except CryptoError:
            return None
        return (row.email, password)


def _as_utc(value: datetime) -> datetime:
    """SQLite verwirft tz-Info beim Roundtrip — wir stellen UTC wieder her."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
