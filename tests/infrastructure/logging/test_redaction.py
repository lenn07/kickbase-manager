"""Tests für den Log-Redaction-Filter (Phase 7)."""

from __future__ import annotations

import logging

import pytest
from app.infrastructure.logging.redaction import RedactionFilter, install_redaction_filter


@pytest.fixture()
def redactor() -> RedactionFilter:
    return RedactionFilter()


def _record(msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args or None,
        exc_info=None,
    )


def test_maskiert_bearer_token(redactor: RedactionFilter) -> None:
    record = _record("Authorization: Bearer abc123.def456-XYZ")
    redactor.filter(record)
    assert "abc123" not in record.getMessage()
    assert record.getMessage() == "Authorization: Bearer ***"


def test_maskiert_bearer_ohne_header_prefix(redactor: RedactionFilter) -> None:
    record = _record("token=Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
    redactor.filter(record)
    assert "eyJ" not in record.getMessage()


def test_maskiert_anthropic_key(redactor: RedactionFilter) -> None:
    record = _record("Using key sk-ant-abcdef1234567890 for request")
    redactor.filter(record)
    assert "sk-ant-abcdef" not in record.getMessage()
    assert "***" in record.getMessage()


def test_maskiert_key_value_password() -> None:
    redactor = RedactionFilter()
    record = _record('login payload {"password": "geheim1234", "user": "a@b.de"}')
    redactor.filter(record)
    text = record.getMessage()
    assert "geheim1234" not in text
    assert "a@b.de" in text  # nicht sensible Felder bleiben


def test_maskiert_api_key_query_param() -> None:
    redactor = RedactionFilter()
    record = _record("GET /v1/x?api_key=abcdefghij status=200")
    redactor.filter(record)
    text = record.getMessage()
    assert "abcdefghij" not in text
    assert "status=200" in text


def test_interpoliert_format_args_vor_maskierung() -> None:
    redactor = RedactionFilter()
    record = _record("401 bei %s %s — token=%s", "POST", "/x", "secretpayload")
    redactor.filter(record)
    text = record.getMessage()
    assert "secretpayload" not in text
    assert "POST" in text and "/x" in text


def test_belaesst_harmlose_zeilen_unangetastet() -> None:
    redactor = RedactionFilter()
    record = _record("Scheduler gestartet — Intervall 120 min")
    redactor.filter(record)
    assert record.getMessage() == "Scheduler gestartet — Intervall 120 min"


def test_install_ist_idempotent() -> None:
    logger = logging.getLogger("test.install.idempotent")
    logger.filters.clear()
    first = install_redaction_filter(logger)
    second = install_redaction_filter(logger)
    assert first is second
    assert sum(isinstance(f, RedactionFilter) for f in logger.filters) == 1
