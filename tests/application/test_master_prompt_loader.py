"""Unit-Tests für den Master-Prompt-Loader (AI-Only-Modus)."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.application.master_prompt_loader import (
    MasterPromptError,
    load_system_prompt,
)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_extracts_system_body_between_markers(tmp_path: Path) -> None:
    md = _write(
        tmp_path / "master_prompt.md",
        (
            "# Kickbase Master-Prompt\n"
            "\n"
            "---\n"
            "\n"
            "## SYSTEM\n"
            "\n"
            "Du bist der autonome Transfer-Manager.\n"
            "\n"
            "### 1. Zielhierarchie\n"
            "\n"
            "Regeln zuerst.\n"
            "\n"
            "---\n"
            "\n"
            "## USER (pro Tick vom Code eingefüllt)\n"
            "\n"
            "```json\n"
            "{}\n"
            "```\n"
        ),
    )

    result = load_system_prompt(md)

    assert result.startswith("Du bist der autonome Transfer-Manager.")
    assert "### 1. Zielhierarchie" in result
    assert "## USER" not in result
    assert "\n---\n" not in result  # Section-Separator wurde entfernt


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(MasterPromptError):
        load_system_prompt(tmp_path / "does_not_exist.md")


def test_missing_markers_raises(tmp_path: Path) -> None:
    md = _write(tmp_path / "master_prompt.md", "# Ohne die richtigen Header\nnur Prosa")
    with pytest.raises(MasterPromptError):
        load_system_prompt(md)


def test_repo_master_prompt_is_loadable() -> None:
    """Der aktuelle docs/master_prompt.md muss immer laden — sonst ist der Auto-Modus tot."""
    text = load_system_prompt()
    assert "Zielhierarchie" in text
    assert "Ausgabeformat" in text
