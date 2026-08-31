"""Tests für SendHoldDigestUseCase (F-9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.hold_digest_uc import SendHoldDigestUseCase
from app.infrastructure.persistence.models import (
    CredentialRow,
    LeagueRow,
    SettingsRow,
    SmtpConfigRow,
    TradeLogRow,
    UserRow,
)
from sqlmodel import Session

from tests.application.conftest import FakeSmtp


def _seed_complete_setup(
    db: Session,
    vault,  # type: ignore[no-untyped-def]
    *,
    digest_enabled: bool = True,
    with_smtp: bool = True,
) -> UserRow:
    user = UserRow(
        email="a@b.de",
        encrypted_password=vault.encrypt("pw"),
        kb_user_id="u1",
        kb_token=vault.encrypt("tkn"),
        kb_token_expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    assert user.id is not None

    db.add(LeagueRow(user_id=user.id, kb_league_id="L1", name="Liga", is_active=True))
    db.add(SettingsRow(user_id=user.id, digest_enabled=digest_enabled, digest_hour=20))
    db.add(
        CredentialRow(
            user_id=user.id,
            kind="anthropic",
            encrypted_value=vault.encrypt("sk-ant-good"),
        )
    )
    if with_smtp:
        db.add(
            SmtpConfigRow(
                user_id=user.id,
                host="mail",
                port=587,
                username="u",
                encrypted_password=vault.encrypt("smtp-pw"),
                from_addr="a@b.de",
                to_addr="a@b.de",
                use_tls=False,
                use_starttls=True,
                verified_at=datetime.now(UTC),
            )
        )
    db.commit()
    return user


def _add_hold(db: Session, user_id: int, *, reason: str, notified: bool = False) -> TradeLogRow:
    row = TradeLogRow(
        user_id=user_id,
        action="HOLD",
        reason_text=reason,
        notified_at=datetime.now(UTC) if notified else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


async def test_digest_sends_pending_holds_and_marks_them(db_session: Session, vault) -> None:  # type: ignore[no-untyped-def]
    user = _seed_complete_setup(db_session, vault)
    assert user.id is not None
    _add_hold(db_session, user.id, reason="HOLD: alles ruhig")
    _add_hold(db_session, user.id, reason="HOLD: keine Kandidaten")

    smtp = FakeSmtp()
    outcome = await SendHoldDigestUseCase(session=db_session, vault=vault, smtp=smtp).run()

    assert outcome.outcome == "sent"
    assert outcome.hold_count == 2
    assert len(smtp.sent) == 1
    _, subject, body = smtp.sent[0]
    assert "HOLD-Digest" in subject
    assert "HOLD: alles ruhig" in body
    assert "HOLD: keine Kandidaten" in body

    remaining = [r for r in db_session.query(TradeLogRow).all() if r.notified_at is None]
    assert remaining == []


async def test_digest_is_no_op_without_pending_holds(db_session: Session, vault) -> None:  # type: ignore[no-untyped-def]
    _seed_complete_setup(db_session, vault)
    smtp = FakeSmtp()
    outcome = await SendHoldDigestUseCase(session=db_session, vault=vault, smtp=smtp).run()

    assert outcome.outcome == "empty"
    assert outcome.hold_count == 0
    assert smtp.sent == []


async def test_digest_skips_when_disabled(db_session: Session, vault) -> None:  # type: ignore[no-untyped-def]
    user = _seed_complete_setup(db_session, vault, digest_enabled=False)
    assert user.id is not None
    _add_hold(db_session, user.id, reason="HOLD: aber Digest aus")

    smtp = FakeSmtp()
    outcome = await SendHoldDigestUseCase(session=db_session, vault=vault, smtp=smtp).run()

    assert outcome.outcome == "disabled"
    assert smtp.sent == []


async def test_digest_reports_missing_smtp_after_delete(db_session: Session, vault) -> None:  # type: ignore[no-untyped-def]
    user = _seed_complete_setup(db_session, vault)
    assert user.id is not None
    _add_hold(db_session, user.id, reason="HOLD")

    # Nach vollständigem Setup wird die SMTP-Row nachträglich entfernt — der
    # Digest-UC muss den Grenzfall abfangen, ohne den Tick zu crashen.
    smtp_row = db_session.query(SmtpConfigRow).filter(SmtpConfigRow.user_id == user.id).one()
    smtp_row.verified_at = None
    db_session.commit()
    # Setup-Check schlägt jetzt Alarm, weil verified_at NULL ist.
    smtp = FakeSmtp()
    outcome = await SendHoldDigestUseCase(session=db_session, vault=vault, smtp=smtp).run()
    assert outcome.outcome == "skipped_setup"


async def test_digest_leaves_rows_unmarked_on_smtp_error(db_session: Session, vault) -> None:  # type: ignore[no-untyped-def]
    user = _seed_complete_setup(db_session, vault)
    assert user.id is not None
    _add_hold(db_session, user.id, reason="HOLD")

    smtp = FakeSmtp(fail=True)
    outcome = await SendHoldDigestUseCase(session=db_session, vault=vault, smtp=smtp).run()

    assert outcome.outcome == "smtp_error"
    assert outcome.error is not None
    pending = [r for r in db_session.query(TradeLogRow).all() if r.notified_at is None]
    assert len(pending) == 1


async def test_digest_ignores_already_notified_holds(db_session: Session, vault) -> None:  # type: ignore[no-untyped-def]
    user = _seed_complete_setup(db_session, vault)
    assert user.id is not None
    _add_hold(db_session, user.id, reason="alt", notified=True)
    _add_hold(db_session, user.id, reason="neu")

    smtp = FakeSmtp()
    outcome = await SendHoldDigestUseCase(session=db_session, vault=vault, smtp=smtp).run()

    assert outcome.outcome == "sent"
    assert outcome.hold_count == 1
    assert "neu" in smtp.sent[0][2]
    assert "alt" not in smtp.sent[0][2]


async def test_digest_skips_before_setup_complete(db_session: Session, vault) -> None:  # type: ignore[no-untyped-def]
    # Kein User seed → Setup unvollständig.
    smtp = FakeSmtp()
    outcome = await SendHoldDigestUseCase(session=db_session, vault=vault, smtp=smtp).run()
    assert outcome.outcome == "skipped_setup"
