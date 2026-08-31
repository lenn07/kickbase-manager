"""Tests für AiosmtplibClient — patcht `aiosmtplib.send`, kein echtes SMTP."""

from __future__ import annotations

from email.message import EmailMessage
from typing import Any

import aiosmtplib
import pytest
from app.infrastructure.notifications import smtp_client as sut


def _config(**overrides: Any) -> sut.SmtpConfig:
    defaults: dict[str, Any] = {
        "host": "mail.example.com",
        "port": 587,
        "username": "bot@example.com",
        "password": "pw",
        "from_addr": "bot@example.com",
        "to_addr": "user@example.com",
        "use_tls": False,
        "use_starttls": True,
    }
    defaults.update(overrides)
    return sut.SmtpConfig(**defaults)


class _SendRecorder:
    def __init__(self, *, raise_exc: BaseException | None = None) -> None:
        self.raise_exc = raise_exc
        self.calls: list[tuple[EmailMessage, dict[str, Any]]] = []

    async def __call__(self, message: EmailMessage, **kwargs: Any) -> Any:
        self.calls.append((message, kwargs))
        if self.raise_exc is not None:
            raise self.raise_exc
        return None


async def test_send_forwards_config_to_aiosmtplib(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _SendRecorder()
    monkeypatch.setattr(sut.aiosmtplib, "send", rec)

    await sut.AiosmtplibClient().send(_config(), "Betreff", "Body-Text")

    assert len(rec.calls) == 1
    message, kwargs = rec.calls[0]
    assert message["Subject"] == "Betreff"
    assert message["From"] == "bot@example.com"
    assert message["To"] == "user@example.com"
    assert message.get_content().strip() == "Body-Text"
    assert kwargs["hostname"] == "mail.example.com"
    assert kwargs["port"] == 587
    assert kwargs["username"] == "bot@example.com"
    assert kwargs["password"] == "pw"
    assert kwargs["use_tls"] is False
    assert kwargs["start_tls"] is True
    assert kwargs["timeout"] == pytest.approx(15.0)


async def test_send_passes_none_when_credentials_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _SendRecorder()
    monkeypatch.setattr(sut.aiosmtplib, "send", rec)

    await sut.AiosmtplibClient().send(_config(username="", password=""), "s", "b")

    _, kwargs = rec.calls[0]
    # aiosmtplib erwartet None, wenn kein Auth gewünscht ist — sonst versucht
    # es SASL PLAIN mit Leerstring, was gegen Gmail sofort fehlschlägt.
    assert kwargs["username"] is None
    assert kwargs["password"] is None


async def test_send_test_mail_uses_send(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _SendRecorder()
    monkeypatch.setattr(sut.aiosmtplib, "send", rec)

    await sut.AiosmtplibClient().send_test_mail(_config())

    assert len(rec.calls) == 1
    message, _ = rec.calls[0]
    assert "Test-Mail" in message["Subject"]


async def test_auth_error_maps_to_smtp_error(monkeypatch: pytest.MonkeyPatch) -> None:
    exc = aiosmtplib.SMTPAuthenticationError(535, "bad credentials")
    monkeypatch.setattr(sut.aiosmtplib, "send", _SendRecorder(raise_exc=exc))

    with pytest.raises(sut.SmtpError, match="SMTP-Auth"):
        await sut.AiosmtplibClient().send(_config(), "s", "b")


async def test_connect_error_maps_to_smtp_error(monkeypatch: pytest.MonkeyPatch) -> None:
    exc = aiosmtplib.SMTPConnectError("server unreachable")
    monkeypatch.setattr(sut.aiosmtplib, "send", _SendRecorder(raise_exc=exc))

    with pytest.raises(sut.SmtpError, match="Verbindung"):
        await sut.AiosmtplibClient().send(_config(), "s", "b")


async def test_generic_smtp_exception_maps_to_smtp_error(monkeypatch: pytest.MonkeyPatch) -> None:
    exc = aiosmtplib.SMTPException("kaputt")
    monkeypatch.setattr(sut.aiosmtplib, "send", _SendRecorder(raise_exc=exc))

    with pytest.raises(sut.SmtpError, match="SMTP-Fehler"):
        await sut.AiosmtplibClient().send(_config(), "s", "b")


async def test_timeout_error_maps_to_smtp_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sut.aiosmtplib, "send", _SendRecorder(raise_exc=TimeoutError("slow")))

    with pytest.raises(sut.SmtpError, match="Netzwerkfehler"):
        await sut.AiosmtplibClient().send(_config(), "s", "b")


async def test_os_error_maps_to_smtp_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sut.aiosmtplib, "send", _SendRecorder(raise_exc=OSError("net down")))

    with pytest.raises(sut.SmtpError, match="Netzwerkfehler"):
        await sut.AiosmtplibClient().send(_config(), "s", "b")
