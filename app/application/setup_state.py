"""Setup-Fortschritt — leitet aus DB-Zustand ab, welche Wizard-Schritte noch offen sind.

Die Auto-Loop-Aktivierung (F-1) hängt davon ab, dass `is_complete` True ist.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlmodel import Session

from app.infrastructure.persistence.repositories import (
    CredentialRepository,
    LeagueRepository,
    SmtpRepository,
    UserRepository,
)


class SetupStep(StrEnum):
    KICKBASE = "kickbase"
    LEAGUE = "league"
    ANTHROPIC = "anthropic"
    SMTP = "smtp"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class SetupState:
    kickbase_ok: bool
    league_ok: bool
    anthropic_ok: bool
    smtp_ok: bool

    @property
    def is_complete(self) -> bool:
        return self.kickbase_ok and self.league_ok and self.anthropic_ok and self.smtp_ok

    @property
    def next_step(self) -> SetupStep:
        if not self.kickbase_ok:
            return SetupStep.KICKBASE
        if not self.league_ok:
            return SetupStep.LEAGUE
        if not self.anthropic_ok:
            return SetupStep.ANTHROPIC
        if not self.smtp_ok:
            return SetupStep.SMTP
        return SetupStep.DONE


def read_setup_state(session: Session) -> SetupState:
    user = UserRepository(session).get_singleton()
    if user is None or user.id is None:
        return SetupState(False, False, False, False)

    league_ok = LeagueRepository(session).active(user.id) is not None
    anthropic_ok = CredentialRepository(session).get(user_id=user.id, kind="anthropic") is not None
    smtp_row = SmtpRepository(session).get(user.id)
    smtp_ok = smtp_row is not None and smtp_row.verified_at is not None

    return SetupState(
        kickbase_ok=True,
        league_ok=league_ok,
        anthropic_ok=anthropic_ok,
        smtp_ok=smtp_ok,
    )
