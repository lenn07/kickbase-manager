"""Repositories kapseln SQLModel-Queries für die Application-Schicht.

Sie liefern **Rohzeilen** — Entschlüsselung von Cipher-Text und Mapping auf
Domain-Objekte macht die Application-Schicht (SetupService), damit die
Persistenz keine Krypto-Verantwortung trägt.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session, select

from app.infrastructure.persistence.models import (
    CredentialRow,
    LeagueRow,
    SmtpConfigRow,
    UserRow,
)


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_singleton(self) -> UserRow | None:
        return self._session.exec(select(UserRow).limit(1)).first()

    def upsert(
        self,
        *,
        email: str,
        encrypted_password: bytes,
        kb_user_id: str,
        kb_token: bytes | None,
        kb_token_expires_at: datetime | None,
    ) -> UserRow:
        existing = self._session.exec(select(UserRow).where(UserRow.email == email)).first()
        if existing is None:
            row = UserRow(
                email=email,
                encrypted_password=encrypted_password,
                kb_user_id=kb_user_id,
                kb_token=kb_token,
                kb_token_expires_at=kb_token_expires_at,
            )
            self._session.add(row)
        else:
            existing.encrypted_password = encrypted_password
            existing.kb_user_id = kb_user_id
            existing.kb_token = kb_token
            existing.kb_token_expires_at = kb_token_expires_at
            existing.updated_at = datetime.now(UTC)
            row = existing
        self._session.commit()
        self._session.refresh(row)
        return row


class CredentialRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, user_id: int, kind: str) -> CredentialRow | None:
        return self._session.exec(
            select(CredentialRow)
            .where(CredentialRow.user_id == user_id)
            .where(CredentialRow.kind == kind)
        ).first()

    def upsert(self, *, user_id: int, kind: str, encrypted_value: bytes) -> CredentialRow:
        row = self.get(user_id=user_id, kind=kind)
        if row is None:
            row = CredentialRow(user_id=user_id, kind=kind, encrypted_value=encrypted_value)
            self._session.add(row)
        else:
            row.encrypted_value = encrypted_value
            row.verified_at = datetime.now(UTC)
        self._session.commit()
        self._session.refresh(row)
        return row


class SmtpRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: int) -> SmtpConfigRow | None:
        return self._session.exec(
            select(SmtpConfigRow).where(SmtpConfigRow.user_id == user_id)
        ).first()

    def upsert(self, row: SmtpConfigRow) -> SmtpConfigRow:
        existing = self.get(row.user_id)
        if existing is None:
            self._session.add(row)
            self._session.commit()
            self._session.refresh(row)
            return row
        existing.host = row.host
        existing.port = row.port
        existing.username = row.username
        existing.encrypted_password = row.encrypted_password
        existing.from_addr = row.from_addr
        existing.to_addr = row.to_addr
        existing.use_tls = row.use_tls
        existing.use_starttls = row.use_starttls
        existing.verified_at = row.verified_at
        self._session.commit()
        self._session.refresh(existing)
        return existing


class LeagueRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_user(self, user_id: int) -> list[LeagueRow]:
        return list(self._session.exec(select(LeagueRow).where(LeagueRow.user_id == user_id)))

    def active(self, user_id: int) -> LeagueRow | None:
        return self._session.exec(
            select(LeagueRow)
            .where(LeagueRow.user_id == user_id)
            .where(LeagueRow.is_active.is_(True))  # type: ignore[union-attr]
        ).first()

    def replace(self, *, user_id: int, leagues: list[tuple[str, str]]) -> list[LeagueRow]:
        """Ersetzt die Ligen-Menge eines Users vollständig, ohne aktiven Marker zu setzen."""
        existing = self.list_for_user(user_id)
        for row in existing:
            self._session.delete(row)
        self._session.flush()
        created: list[LeagueRow] = []
        for kb_league_id, name in leagues:
            row = LeagueRow(user_id=user_id, kb_league_id=kb_league_id, name=name)
            self._session.add(row)
            created.append(row)
        self._session.commit()
        for row in created:
            self._session.refresh(row)
        return created

    def set_active(self, *, user_id: int, kb_league_id: str) -> LeagueRow:
        rows = self.list_for_user(user_id)
        target: LeagueRow | None = None
        for row in rows:
            new_active = row.kb_league_id == kb_league_id
            if row.is_active != new_active:
                row.is_active = new_active
            if new_active:
                target = row
        self._session.commit()
        if target is None:
            raise LookupError(f"Liga {kb_league_id} nicht in DB")
        self._session.refresh(target)
        return target
