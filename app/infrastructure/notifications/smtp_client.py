"""SMTP-Client (aiosmtplib) — F-9 Notifications + F-12 Test-Mail-Button (ADR-10).

Der Client kapselt die drei üblichen Transport-Modi:
- **use_tls=True**   → Implicit TLS (typisch Port 465)
- **use_starttls=True** → Klartext + STARTTLS (typisch Port 587)
- keine Flags       → Klartext (nur lokale MTAs)
"""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

import aiosmtplib


class SmtpError(Exception):
    """SMTP-Konfiguration ungültig oder Zustellung fehlgeschlagen."""


class SmtpGateway(Protocol):
    async def send(self, config: SmtpConfig, subject: str, body: str) -> None: ...

    async def send_test_mail(self, config: SmtpConfig) -> None: ...


@dataclass(frozen=True, slots=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    from_addr: str
    to_addr: str
    use_tls: bool = True
    use_starttls: bool = False
    timeout_s: float = 15.0


class AiosmtplibClient:
    async def send(self, config: SmtpConfig, subject: str, body: str) -> None:
        message = _build_message(config.from_addr, config.to_addr, subject, body)
        await _dispatch(config, message)

    async def send_test_mail(self, config: SmtpConfig) -> None:
        subject = "Kickbase Auto-Manager — Test-Mail"
        body = (
            "Diese Nachricht bestätigt, dass die SMTP-Konfiguration deines "
            "Kickbase Auto-Managers funktioniert.\n\n"
            "Wenn du diese Mail erhältst, ist der Setup-Schritt abgeschlossen."
        )
        await self.send(config, subject, body)


def _build_message(from_addr: str, to_addr: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


async def _dispatch(config: SmtpConfig, message: EmailMessage) -> None:
    try:
        await aiosmtplib.send(
            message,
            hostname=config.host,
            port=config.port,
            username=config.username or None,
            password=config.password or None,
            use_tls=config.use_tls,
            start_tls=config.use_starttls,
            timeout=config.timeout_s,
        )
    except aiosmtplib.SMTPAuthenticationError as exc:
        raise SmtpError(f"SMTP-Auth fehlgeschlagen: {exc}") from exc
    except aiosmtplib.SMTPConnectError as exc:
        raise SmtpError(f"Verbindung zum SMTP-Server fehlgeschlagen: {exc}") from exc
    except aiosmtplib.SMTPException as exc:
        raise SmtpError(f"SMTP-Fehler: {exc}") from exc
    except (OSError, TimeoutError) as exc:
        raise SmtpError(f"Netzwerkfehler beim Mail-Versand: {exc}") from exc
