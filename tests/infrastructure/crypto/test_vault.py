from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from app.infrastructure.crypto.vault import CryptoError, FernetVault


def test_roundtrip(tmp_path: Path) -> None:
    vault = FernetVault.load_or_create(data_dir=tmp_path)
    token = vault.encrypt("hunter2!äöü")
    assert vault.decrypt(token) == "hunter2!äöü"


def test_generates_key_file_with_600_mode(tmp_path: Path) -> None:
    FernetVault.load_or_create(data_dir=tmp_path)
    key_file = tmp_path / "secret.key"
    assert key_file.exists()
    mode = key_file.stat().st_mode & 0o777
    assert mode == 0o600


def test_reuses_existing_key(tmp_path: Path) -> None:
    v1 = FernetVault.load_or_create(data_dir=tmp_path)
    token = v1.encrypt("secret")
    v2 = FernetVault.load_or_create(data_dir=tmp_path)
    assert v2.decrypt(token) == "secret"


def test_respects_custom_secret_file(tmp_path: Path) -> None:
    custom = tmp_path / "custom.key"
    v1 = FernetVault.load_or_create(data_dir=tmp_path, secret_file=custom)
    assert custom.exists()
    assert not (tmp_path / "secret.key").exists()
    token = v1.encrypt("x")
    v2 = FernetVault.load_or_create(data_dir=tmp_path, secret_file=custom)
    assert v2.decrypt(token) == "x"


def test_invalid_key_raises() -> None:
    with pytest.raises(CryptoError):
        FernetVault(b"nope")


def test_decrypt_with_wrong_key_raises(tmp_path: Path) -> None:
    v1 = FernetVault.load_or_create(data_dir=tmp_path / "a")
    v2 = FernetVault.load_or_create(data_dir=tmp_path / "b")
    token = v1.encrypt("only-a-can-read")
    with pytest.raises(CryptoError):
        v2.decrypt(token)


@pytest.mark.skipif(os.name != "posix", reason="Permission-Bits nur auf POSIX aussagekräftig.")
def test_load_warns_on_insecure_permissions(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    FernetVault.load_or_create(data_dir=tmp_path)
    key_file = tmp_path / "secret.key"
    key_file.chmod(0o644)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.infrastructure.crypto.vault"):
        FernetVault.load_or_create(data_dir=tmp_path)

    assert any("unsichere Permissions" in rec.message for rec in caplog.records)


@pytest.mark.skipif(os.name != "posix", reason="Permission-Bits nur auf POSIX aussagekräftig.")
def test_load_stays_silent_on_600_permissions(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    FernetVault.load_or_create(data_dir=tmp_path)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.infrastructure.crypto.vault"):
        FernetVault.load_or_create(data_dir=tmp_path)

    assert not any("unsichere Permissions" in rec.message for rec in caplog.records)
