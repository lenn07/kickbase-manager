from __future__ import annotations

import pytest
from app.application.setup_service import SetupError, SetupService, SmtpFormInput
from app.application.setup_state import SetupStep
from app.infrastructure.crypto.vault import FernetVault
from app.infrastructure.persistence.repositories import (
    CredentialRepository,
    LeagueRepository,
    SmtpRepository,
    UserRepository,
)
from sqlmodel import Session

from tests.application.conftest import FakeKickbase, FakeLlm, FakeSmtp


def _service(
    session: Session,
    vault: FernetVault,
    *,
    kickbase: FakeKickbase | None = None,
    llm: FakeLlm | None = None,
    smtp: FakeSmtp | None = None,
) -> SetupService:
    return SetupService(
        session=session,
        vault=vault,
        kickbase=kickbase or FakeKickbase(),
        llm=llm or FakeLlm(),
        smtp=smtp or FakeSmtp(),
    )


def _smtp_form(**overrides: object) -> SmtpFormInput:
    defaults: dict[str, object] = {
        "host": "smtp.example.com",
        "port": 465,
        "username": "user",
        "password": "pw",
        "from_addr": "from@example.com",
        "to_addr": "to@example.com",
        "use_tls": True,
        "use_starttls": False,
    }
    defaults.update(overrides)
    return SmtpFormInput(**defaults)  # type: ignore[arg-type]


# -- verify_kickbase --------------------------------------------------


async def test_verify_kickbase_persists_user_leagues_and_encrypts_secrets(
    db_session: Session, vault: FernetVault
) -> None:
    kb = FakeKickbase()
    service = _service(db_session, vault, kickbase=kb)

    user = await service.verify_kickbase("user@example.com", "secret")

    assert user.id is not None
    stored = UserRepository(db_session).get_singleton()
    assert stored is not None
    assert stored.email == "user@example.com"
    assert vault.decrypt(stored.encrypted_password) == "secret"
    assert stored.kb_token is not None
    assert vault.decrypt(stored.kb_token) == "tkn-xyz"

    leagues = LeagueRepository(db_session).list_for_user(user.id)  # type: ignore[arg-type]
    assert {row.kb_league_id for row in leagues} == {"L1", "L2"}
    assert all(row.is_active is False for row in leagues)


async def test_verify_kickbase_rejects_empty_input(db_session: Session, vault: FernetVault) -> None:
    service = _service(db_session, vault)
    with pytest.raises(SetupError):
        await service.verify_kickbase("", "")


async def test_verify_kickbase_maps_auth_error_to_setup_error(
    db_session: Session, vault: FernetVault
) -> None:
    service = _service(db_session, vault, kickbase=FakeKickbase())
    with pytest.raises(SetupError) as exc:
        await service.verify_kickbase("user@example.com", "wrong")
    assert "fehlgeschlagen" in str(exc.value).lower()


# -- select_league ----------------------------------------------------


async def test_select_league_flips_active_flag(db_session: Session, vault: FernetVault) -> None:
    service = _service(db_session, vault)
    await service.verify_kickbase("user@example.com", "secret")

    row = service.select_league("L2")

    assert row.is_active is True
    leagues = service.list_leagues()
    active = [choice for choice in leagues if choice.is_active]
    assert [c.kb_league_id for c in active] == ["L2"]


async def test_select_unknown_league_raises(db_session: Session, vault: FernetVault) -> None:
    service = _service(db_session, vault)
    await service.verify_kickbase("user@example.com", "secret")
    with pytest.raises(SetupError):
        service.select_league("does-not-exist")


# -- verify_anthropic -------------------------------------------------


async def test_verify_anthropic_stores_encrypted_key(
    db_session: Session, vault: FernetVault
) -> None:
    llm = FakeLlm(valid_keys={"sk-ant-good"})
    service = _service(db_session, vault, llm=llm)
    await service.verify_kickbase("user@example.com", "secret")

    await service.verify_anthropic("sk-ant-good")

    assert llm.calls == ["sk-ant-good"]
    user = UserRepository(db_session).get_singleton()
    assert user and user.id is not None
    cred = CredentialRepository(db_session).get(user_id=user.id, kind="anthropic")
    assert cred is not None
    assert vault.decrypt(cred.encrypted_value) == "sk-ant-good"


async def test_verify_anthropic_rejects_bad_key(db_session: Session, vault: FernetVault) -> None:
    service = _service(db_session, vault, llm=FakeLlm())
    await service.verify_kickbase("user@example.com", "secret")

    with pytest.raises(SetupError):
        await service.verify_anthropic("sk-ant-bad")


async def test_verify_anthropic_requires_user(db_session: Session, vault: FernetVault) -> None:
    service = _service(db_session, vault)
    with pytest.raises(SetupError):
        await service.verify_anthropic("sk-ant-good")


# -- verify_smtp ------------------------------------------------------


async def test_verify_smtp_sends_test_mail_and_persists(
    db_session: Session, vault: FernetVault
) -> None:
    smtp = FakeSmtp()
    service = _service(db_session, vault, smtp=smtp)
    await service.verify_kickbase("user@example.com", "secret")

    row = await service.verify_smtp(_smtp_form())

    assert len(smtp.sent) == 1
    assert row.verified_at is not None
    assert vault.decrypt(row.encrypted_password) == "pw"
    stored = SmtpRepository(db_session).get(row.user_id)
    assert stored is not None
    assert stored.host == "smtp.example.com"


async def test_verify_smtp_rejects_conflicting_tls(db_session: Session, vault: FernetVault) -> None:
    service = _service(db_session, vault)
    await service.verify_kickbase("user@example.com", "secret")
    with pytest.raises(SetupError):
        await service.verify_smtp(_smtp_form(use_tls=True, use_starttls=True))


async def test_verify_smtp_reports_transport_failure(
    db_session: Session, vault: FernetVault
) -> None:
    service = _service(db_session, vault, smtp=FakeSmtp(fail=True))
    await service.verify_kickbase("user@example.com", "secret")

    with pytest.raises(SetupError):
        await service.verify_smtp(_smtp_form())


async def test_verify_smtp_rejects_missing_fields(db_session: Session, vault: FernetVault) -> None:
    service = _service(db_session, vault)
    await service.verify_kickbase("user@example.com", "secret")
    with pytest.raises(SetupError):
        await service.verify_smtp(_smtp_form(host=""))


async def test_verify_smtp_rejects_out_of_range_port(
    db_session: Session, vault: FernetVault
) -> None:
    service = _service(db_session, vault)
    await service.verify_kickbase("user@example.com", "secret")
    with pytest.raises(SetupError):
        await service.verify_smtp(_smtp_form(port=99999))


# -- SetupState progression ------------------------------------------


async def test_state_progresses_through_all_steps(db_session: Session, vault: FernetVault) -> None:
    service = _service(db_session, vault)

    assert service.state().next_step is SetupStep.KICKBASE
    assert service.state().is_complete is False

    await service.verify_kickbase("user@example.com", "secret")
    assert service.state().next_step is SetupStep.LEAGUE

    service.select_league("L1")
    assert service.state().next_step is SetupStep.ANTHROPIC

    await service.verify_anthropic("sk-ant-good")
    assert service.state().next_step is SetupStep.SMTP

    await service.verify_smtp(_smtp_form())
    state = service.state()
    assert state.is_complete is True
    assert state.next_step is SetupStep.DONE
