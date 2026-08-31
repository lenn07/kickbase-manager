"""SendHoldDigestUseCase — bündelt HOLD-Ticks zur Tages-Digest-Mail (F-9).

Der Cron-Job feuert einmal pro Tag zur konfigurierten `digest_hour`. Der UC
- prüft `digest_enabled` (Toggle darf jederzeit greifen, ohne Reschedule),
- sammelt alle unbenachrichtigten HOLD-Ticks (`notified_at IS NULL`),
- sendet eine Sammel-Mail via SMTP,
- markiert die Ticks als benachrichtigt — nur wenn die Mail wirklich rausging.

Ohne SMTP-Konfiguration oder ohne pending HOLDs bleibt der UC still.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session

from app.application.setup_state import read_setup_state
from app.infrastructure.crypto.vault import CryptoError, FernetVault
from app.infrastructure.notifications.smtp_client import SmtpConfig, SmtpError, SmtpGateway
from app.infrastructure.persistence.models import SmtpConfigRow, TradeLogRow
from app.infrastructure.persistence.repositories import (
    SettingsRepository,
    SmtpRepository,
    TradeLogRepository,
    UserRepository,
)

_log = logging.getLogger(__name__)

_DigestOutcomeLiteral = str
# Mögliche Werte für `DigestOutcome.outcome`:
# "sent", "empty", "disabled", "skipped_setup", "smtp_missing", "smtp_error".


@dataclass(frozen=True, slots=True)
class DigestOutcome:
    outcome: _DigestOutcomeLiteral
    hold_count: int = 0
    error: str | None = None


class SendHoldDigestUseCase:
    def __init__(
        self,
        *,
        session: Session,
        vault: FernetVault,
        smtp: SmtpGateway,
    ) -> None:
        self._session = session
        self._vault = vault
        self._smtp = smtp
        self._users = UserRepository(session)
        self._settings_repo = SettingsRepository(session)
        self._smtp_repo = SmtpRepository(session)
        self._trades = TradeLogRepository(session)

    async def run(self) -> DigestOutcome:
        state = read_setup_state(self._session)
        if not state.is_complete:
            return DigestOutcome(outcome="skipped_setup")

        user = self._users.get_singleton()
        assert user is not None and user.id is not None

        settings = self._settings_repo.get_or_default(user.id)
        if not settings.digest_enabled:
            return DigestOutcome(outcome="disabled")

        pending = self._trades.list_pending_holds(user.id)
        if not pending:
            return DigestOutcome(outcome="empty")

        config = self._load_smtp(user.id)
        if config is None:
            _log.info("HOLD-Digest übersprungen: kein SMTP konfiguriert.")
            return DigestOutcome(outcome="smtp_missing", hold_count=len(pending))

        subject = f"[Kickbase] HOLD-Digest ({len(pending)} Ticks)"
        body = _render_body(pending)

        try:
            await self._smtp.send(config, subject, body)
        except SmtpError as exc:
            _log.warning("HOLD-Digest-Mail fehlgeschlagen: %s", exc)
            return DigestOutcome(outcome="smtp_error", hold_count=len(pending), error=str(exc))

        self._trades.mark_notified(pending, ts=datetime.now(UTC))
        return DigestOutcome(outcome="sent", hold_count=len(pending))

    def _load_smtp(self, user_id: int) -> SmtpConfig | None:
        row: SmtpConfigRow | None = self._smtp_repo.get(user_id)
        if row is None:
            return None
        try:
            password = self._vault.decrypt(row.encrypted_password)
        except CryptoError:
            _log.warning("SMTP-Passwort konnte nicht entschlüsselt werden — Digest übersprungen.")
            return None
        return SmtpConfig(
            host=row.host,
            port=row.port,
            username=row.username,
            password=password,
            from_addr=row.from_addr,
            to_addr=row.to_addr,
            use_tls=row.use_tls,
            use_starttls=row.use_starttls,
        )


def _render_body(rows: list[TradeLogRow]) -> str:
    lines = [
        f"Zusammenfassung: {len(rows)} HOLD-Ticks seit dem letzten Digest.",
        "",
    ]
    for row in rows:
        ts = row.ts.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        reason = row.reason_text or "—"
        lines.append(f"- {ts}: {reason}")
    lines.append("")
    lines.append("Diese Mail wird nur einmal pro HOLD-Serie versendet.")
    return "\n".join(lines)


__all__ = ["DigestOutcome", "SendHoldDigestUseCase"]
