"""Unit-Tests für den Master-Prompt-Loader (AI-Only-Modus)."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.application.master_prompt_loader import (
    RULES_LAST_VERIFIED,
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


# -- P0-5: Der Prompt muss zum Payload passen -----------------------------


def test_corrected_claims_are_gone_from_the_prompt() -> None:
    """Die Falschaussagen aus dem Defekt-Register §4.1 dürfen nicht zurückkehren.

    Jede einzelne hätte das Modell aktiv fehlgeleitet: eine erfundene
    Kaderquote, ein erfundener Abschlag beim Sofortverkauf, ein willkürlicher
    Overbid-Deckel.
    """
    prompt = load_system_prompt()
    for wrong in (
        "max. 3 Spieler pro Bundesliga-Club",  # ist eine Liga-Einstellung 1-11
        "2 TW / 5 DEF / 5 MID / 3 STK",  # frei erfunden
        "meist unter Marktwert",  # SELL_INSTANT bringt den vollen Marktwert
        "bis ca. **+15 %**",  # willkürlicher Deckel statt Herleitung
    ):
        assert wrong not in prompt, f"Korrigierte Falschaussage wieder im Prompt: {wrong!r}"


def test_prompt_covers_the_rules_that_came_with_phase_one() -> None:
    """Regeln ohne Prompt-Erwähnung sind Daten, die niemand liest."""
    prompt = load_system_prompt()
    for expected in (
        "SET_LINEUP",  # P0-4
        "mv_update_at_iso",  # P0-1, die zweite Uhr
        "start_probability_source",  # P0-3, Herkunft der Prognose
        "offer_count",  # P0-2, Konkurrenz beim Overbid
        "allowed_formations",
        "Head-to-Head",  # Wertungsmodus
        "underpay_blocked",  # Underpay-Block, seit §8/F6 als Feld statt als Prosa
    ):
        assert expected in prompt, f"Der Prompt erwähnt {expected!r} nicht"


def test_prompt_covers_the_fields_that_came_with_phase_two() -> None:
    """Dasselbe für Phase 2 — ein Feld, das der Prompt nicht nennt, wird nicht gelesen.

    Der Payload ist in Phase 2 um acht Felder gewachsen. Jedes davon kostet
    Tokens in jedem Tick; eines, das im Prompt nicht vorkommt, zahlt man,
    ohne etwas dafür zu bekommen.
    """
    prompt = load_system_prompt()
    for expected in (
        "bought_at_price",  # P1-6
        "unrealized_pnl",  # P1-6
        "minutes_last5",  # P1-8
        "starts_last5",  # P1-8
        "form_matchdays_counted",  # P1-8
        "squad_slots_left",  # P1-9
        "club_limit_is_unlimited",  # P1-9 / §8/F6
        "scoring_mode",  # P1-9 / §8/F6
    ):
        assert expected in prompt, f"Der Prompt erwähnt {expected!r} nicht"


def test_prompt_does_not_promise_data_the_payload_lacks() -> None:
    """Der alte Prompt verlangte Restspielplan und Gegnerstärke — beides fehlt.

    Ein Modell, das nach nicht vorhandenen Feldern greift, erfindet sie. Seit
    P0-5 steht ausdrücklich im Prompt, dass sie fehlen (P2-11 liefert sie).
    """
    prompt = load_system_prompt()
    assert "derzeit *nicht* im Kontext" in prompt
    assert "erfinde sie nicht" in prompt


def test_rules_last_verified_matches_the_prompt_file() -> None:
    """Sonst meldet das Modell `rules_may_be_stale` für einen frischen Prompt."""
    raw = (Path(__file__).resolve().parents[2] / "docs" / "master_prompt.md").read_text()
    assert f"Fixiert am {RULES_LAST_VERIFIED.isoformat()}" in raw
