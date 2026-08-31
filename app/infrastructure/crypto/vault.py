"""FernetVault — verschlüsselt Kickbase-Passwörter, API-Keys, SMTP-Secrets.

Master-Key-Handling:
- Bevorzugt aus `KB_SECRET_FILE` (z. B. Docker-Secret).
- Ansonsten wird beim ersten Aufruf ein neuer Key generiert und nach
  `<data_dir>/secret.key` mit Mode 0600 geschrieben (Zero-Config auf dem Pi).
- Ein einmal geschriebener Key wird nie überschrieben; Rotation ist Nutzer-Aufgabe.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_log = logging.getLogger(__name__)

# 0o077 = alle Bits für Group und Other. Ein sauberer 0600-Key hat davon 0.
_INSECURE_MODE_MASK = 0o077


class CryptoError(Exception):
    """Fehler bei Ver- oder Entschlüsselung (z. B. falscher Master-Key)."""


class FernetVault:
    def __init__(self, key: bytes) -> None:
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as exc:
            raise CryptoError(f"Ungültiger Fernet-Key: {exc}") from exc

    @classmethod
    def load_or_create(cls, *, data_dir: Path, secret_file: Path | None = None) -> FernetVault:
        key_path = secret_file or (data_dir / "secret.key")
        key = _load_key(key_path)
        if key is None:
            key = Fernet.generate_key()
            _write_key(key_path, key)
        return cls(key)

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, token: bytes) -> str:
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise CryptoError("Cipher-Text konnte nicht entschlüsselt werden") from exc


def _load_key(path: Path) -> bytes | None:
    if not path.exists():
        return None
    _warn_if_insecure(path)
    data = path.read_bytes().strip()
    return data or None


def _warn_if_insecure(path: Path) -> None:
    """Loggt eine WARN, wenn der Master-Key für Group/Other lesbar ist.

    POSIX-only: auf Windows liefert `stat().st_mode` keine sinnvollen Unix-
    Bits — dort wird der Check übersprungen.
    """
    if os.name != "posix":
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & _INSECURE_MODE_MASK:
        _log.warning(
            "Master-Key %s hat unsichere Permissions %o — empfohlen: `chmod 600 %s`.",
            path,
            mode,
            path,
        )


def _write_key(path: Path, key: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomar schreiben und restriktive Rechte setzen, bevor Content sichtbar ist.
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
