"""Orchestrierung der drei Setup-Schritte (F-1) + Liga-Auswahl (F-2).

Der Service ist der einzige Ort, an dem Klartext-Credentials in Kontakt mit
`FernetVault` kommen. Repositories bekommen ausschließlich Cipher-Text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session

from app.application.setup_state import SetupState, read_setup_state
from app.domain.exceptions import KickbaseError
from app.domain.gateways import KickbaseGateway
from app.infrastructure.crypto.vault import FernetVault
from app.infrastructure.llm.anthropic_client import LlmGateway, LlmVerificationError
from app.infrastructure.notifications.smtp_client import SmtpConfig, SmtpError, SmtpGateway
from app.infrastructure.persistence.models import LeagueRow, SmtpConfigRow, UserRow
from app.infrastructure.persistence.repositories import (
    CredentialRepository,
    LeagueRepository,
    SmtpRepository,
    UserRepository,
)


@dataclass(frozen=True, slots=True)
class LeagueChoice:
    kb_league_id: str
    name: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class SmtpFormInput:
    host: str
    port: int
    username: str
    password: str
    from_addr: str
    to_addr: str
    use_tls: bool
    use_starttls: bool


_MIN_PORT = 1
_MAX_PORT = 65535


class SetupError(Exception):
    """Fachlicher Fehler beim Setup — wird von der Web-Schicht als Formfeld-Fehler gerendert."""


class SetupService:
    def __init__(
        self,
        *,
        session: Session,
        vault: FernetVault,
        kickbase: KickbaseGateway,
        llm: LlmGateway,
        smtp: SmtpGateway,
    ) -> None:
        self._session = session
        self._vault = vault
        self._kickbase = kickbase
        self._llm = llm
        self._smtp = smtp
        self._users = UserRepository(session)
        self._creds = CredentialRepository(session)
        self._smtp_repo = SmtpRepository(session)
        self._leagues = LeagueRepository(session)

    def state(self) -> SetupState:
        return read_setup_state(self._session)

    def current_email(self) -> str:
        user = self._users.get_singleton()
        return user.email if user else ""

    # -- Schritt 1: Kickbase-Login + Ligen laden ----------------------

    async def verify_kickbase(self, email: str, password: str) -> UserRow:
        email = email.strip()
        if not email or not password:
            raise SetupError("E-Mail und Passwort sind Pflicht.")

        try:
            session = await self._kickbase.login(email, password)
            leagues = await self._kickbase.list_leagues()
        except KickbaseError as exc:
            raise SetupError(f"Kickbase-Login fehlgeschlagen: {exc}") from exc

        user = self._users.upsert_credentials(
            email=email, encrypted_password=self._vault.encrypt(password)
        )
        assert user.id is not None
        self._users.update_session(
            user_id=user.id,
            kb_user_id=session.user_id,
            kb_token=self._vault.encrypt(session.token),
            kb_token_expires_at=session.token_expires_at,
        )
        self._leagues.replace(
            user_id=user.id,
            leagues=[(league.id, league.name) for league in leagues],
        )
        return user

    # -- Schritt 2: Liga wählen ---------------------------------------

    def list_leagues(self) -> list[LeagueChoice]:
        user = self._require_user()
        assert user.id is not None
        rows = self._leagues.list_for_user(user.id)
        return [
            LeagueChoice(kb_league_id=row.kb_league_id, name=row.name, is_active=row.is_active)
            for row in rows
        ]

    def select_league(self, kb_league_id: str) -> LeagueRow:
        user = self._require_user()
        assert user.id is not None
        try:
            return self._leagues.set_active(user_id=user.id, kb_league_id=kb_league_id)
        except LookupError as exc:
            raise SetupError("Unbekannte Liga.") from exc

    # -- Schritt 3: Anthropic-Key verifizieren -------------------------

    async def verify_anthropic(self, api_key: str) -> None:
        user = self._require_user()
        assert user.id is not None
        try:
            await self._llm.verify_key(api_key)
        except LlmVerificationError as exc:
            raise SetupError(str(exc)) from exc
        self._creds.upsert(
            user_id=user.id,
            kind="anthropic",
            encrypted_value=self._vault.encrypt(api_key),
        )

    # -- Schritt 4: SMTP verifizieren ---------------------------------

    async def verify_smtp(self, form: SmtpFormInput) -> SmtpConfigRow:
        user = self._require_user()
        assert user.id is not None

        self._validate_smtp_form(form)

        config = SmtpConfig(
            host=form.host.strip(),
            port=form.port,
            username=form.username.strip(),
            password=form.password,
            from_addr=form.from_addr.strip(),
            to_addr=form.to_addr.strip(),
            use_tls=form.use_tls,
            use_starttls=form.use_starttls,
        )

        try:
            await self._smtp.send_test_mail(config)
        except SmtpError as exc:
            raise SetupError(str(exc)) from exc

        row = SmtpConfigRow(
            user_id=user.id,
            host=config.host,
            port=config.port,
            username=config.username,
            encrypted_password=self._vault.encrypt(config.password),
            from_addr=config.from_addr,
            to_addr=config.to_addr,
            use_tls=config.use_tls,
            use_starttls=config.use_starttls,
            verified_at=datetime.now(UTC),
        )
        return self._smtp_repo.upsert(row)

    # -- Helpers ------------------------------------------------------

    def _require_user(self) -> UserRow:
        user = self._users.get_singleton()
        if user is None:
            raise SetupError("Bitte zuerst mit Kickbase anmelden.")
        return user

    @staticmethod
    def _validate_smtp_form(form: SmtpFormInput) -> None:
        missing = [
            field
            for field, value in (
                ("Host", form.host),
                ("Absender", form.from_addr),
                ("Empfänger", form.to_addr),
            )
            if not value or not value.strip()
        ]
        if missing:
            raise SetupError(f"Pflichtfelder fehlen: {', '.join(missing)}")
        if not (_MIN_PORT <= form.port <= _MAX_PORT):
            raise SetupError(f"Port muss zwischen {_MIN_PORT} und {_MAX_PORT} liegen.")
        if form.use_tls and form.use_starttls:
            raise SetupError("TLS und STARTTLS gleichzeitig ist nicht möglich.")
