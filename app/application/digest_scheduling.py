"""Verbindet SettingsRow.digest_* mit dem Scheduler-Digest-Job (F-9).

Wird beim App-Start und nach jeder Änderung der Digest-Einstellungen im
Web-Settings-Router aufgerufen. Idempotent: mehrfaches Anwenden mit gleichem
Input ändert nichts.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.application.hold_digest_uc import DigestOutcome, SendHoldDigestUseCase
from app.infrastructure.crypto.vault import FernetVault
from app.infrastructure.metrics import get_metrics
from app.infrastructure.notifications.smtp_client import AiosmtplibClient
from app.infrastructure.persistence.repositories import SettingsRepository, UserRepository
from app.infrastructure.scheduler import KickbaseScheduler

DigestCallable = Callable[[], Awaitable[DigestOutcome]]


def apply_digest_settings(
    scheduler: KickbaseScheduler | None,
    engine: Engine,
    vault: FernetVault,
) -> None:
    if scheduler is None:
        return
    with Session(engine) as db:
        user = UserRepository(db).get_singleton()
        if user is None or user.id is None:
            scheduler.set_digest(None)
            return
        row = SettingsRepository(db).get_or_default(user.id)

    if not row.digest_enabled:
        scheduler.set_digest(None)
        return

    scheduler.set_digest(_build_digest_callback(engine, vault), hour=row.digest_hour)


def _build_digest_callback(engine: Engine, vault: FernetVault) -> DigestCallable:
    async def digest_tick() -> DigestOutcome:
        smtp = AiosmtplibClient()
        with Session(engine) as db:
            uc = SendHoldDigestUseCase(session=db, vault=vault, smtp=smtp)
            outcome = await uc.run()
        get_metrics().record_digest(outcome.outcome)
        return outcome

    return digest_tick


__all__ = ["apply_digest_settings"]
