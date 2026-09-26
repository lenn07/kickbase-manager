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
        # P0-2: der Zähler eingehender Gebote auf **eigenen** Listings. Nicht
        # mehr als Konkurrenzmaß beim Overbid — siehe
        # `test_prompt_does_not_read_the_offer_count_as_competition`.
        "listing.offer_count",
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


def test_prompt_covers_the_fields_that_came_with_p2_13() -> None:
    """Overbid-Kalibrierung und Trading-Playbook brauchen ihre Felder im Text.

    Der `trading`-Block und die beiden Update-Zähler kosten Tokens in jedem
    Tick. Ein Feld, das der Prompt nicht nennt, wird nicht gelesen — man zahlt
    es, ohne etwas dafür zu bekommen.
    """
    prompt = load_system_prompt()
    for expected in (
        "trading.phase",  # Zielhierarchie kippt darüber
        "mv_updates_until_expiry",  # Drift-Anteil des Overbids
        "mv_updates_until_matchday",  # bleibt überhaupt Zeit für einen Trade?
        "squad_slots_free",  # Slot-Ökonomie
        "spendable_before_debt_limit",  # 33 %-Spielraum dieses Ticks
        "profit_positions",
        "days_held",  # totes Kapital erkennen
        "my_open_bid_count",
        "listed_by",  # Manager-Listing braucht einen Aufschlag
    ):
        assert expected in prompt, f"Der Prompt erwähnt {expected!r} nicht"


def test_prompt_does_not_read_the_offer_count_as_competition() -> None:
    """`ofc` ist auf fremden Listings **kein** Konkurrenzmaß (P2-13).

    Bis P2-13 stand im Prompt, `offer_count` sage, „wie viele bereits geboten
    haben", und daraus leitete er zwei falsche Regeln ab: bei `== 1` sei man der
    einzige Bieter, bei `== 0` genüge der Marktwert. Kickbase zeigt fremde
    Gebote aber nirgends an — der Zähler meint auf fremden Listings die eigenen
    Gebote. Der Rückfall ist leicht: der Feldname klingt wie das Gegenteil.

    Geprüft wird beides — dass die alten Formulierungen weg sind **und** dass
    die Unsichtbarkeit ausdrücklich dasteht. Nur das Erste wäre zu schwach: ein
    Prompt, der zum Thema schweigt, lädt das Modell ein, vom Feldnamen auf die
    Bedeutung zu schließen.
    """
    prompt = load_system_prompt()
    for wrong in (
        "`offer_count` sagt, wie viele bereits geboten haben",
        "`offer_count > 1`",
        "`offer_count == 1`",
    ):
        assert wrong not in prompt, f"Alte Lesart von `ofc` wieder im Prompt: {wrong!r}"
    assert "Du siehst die Gebote der anderen nicht." in prompt
    assert "es zählt die **eigenen**" in prompt


def test_prompt_only_promises_fields_the_payload_carries() -> None:
    """P2-11 dreht diesen Wächter um.

    Bis dahin verlangte §1.2 Restspielplan und Gegnerstärke, ohne dass ein
    einziges Feld dafür im Payload stand — P0-5 hat das mit einem ausdrücklichen
    „stehen derzeit *nicht* im Kontext" geschlossen, und dieser Test hat auf
    genau diesen Satz geprüft. Seit P2-11 sind die Felder da; der Satz muss
    **weg**, sonst verbietet der Prompt die Nutzung vorhandener Daten.

    Was bleibt, ist die Regel für den Ausfall: fehlt der Spielplan, trägt der
    Spieler `missing_data:fixtures` und es wird nichts erfunden.
    """
    prompt = load_system_prompt()
    assert "derzeit *nicht* im Kontext" not in prompt, (
        "§1.2 verbietet die Nutzung von Daten, die seit P2-11 im Payload stehen."
    )
    for field_name in ("`next_opponent`", "`is_home`", "`fdr`", "`fdr_next3`"):
        assert field_name in prompt, f"{field_name} fehlt in §1.2"
    assert "missing_data:fixtures" in prompt
    assert "nicht erfinden" in prompt or "erfinden" in prompt


def test_prompt_states_the_fdr_direction_unambiguously() -> None:
    """Die Skalenrichtung muss im Prompt stehen, nicht nur im Code.

    Dieselbe Falle wie bei `prob` (Plan §8/F2): eine verdrehte Skala lässt das
    Modell systematisch die Spieler mit den schwersten Spielen bevorzugen.
    Der Code definiert die Richtung in `app/domain/fixtures.py`; steht sie im
    Prompt nicht oder anders, entscheidet das Modell gegen die Daten.
    """
    prompt = load_system_prompt()
    assert "1 = leichtester Gegner, 5 = schwerster" in prompt
    assert "Die Skala läuft aufwärts in Richtung Schwierigkeit." in prompt


def test_rules_last_verified_matches_the_prompt_file() -> None:
    """Sonst meldet das Modell `rules_may_be_stale` für einen frischen Prompt."""
    raw = (Path(__file__).resolve().parents[2] / "docs" / "master_prompt.md").read_text()
    assert f"Fixiert am {RULES_LAST_VERIFIED.isoformat()}" in raw


def test_prompt_explains_how_to_use_the_league_standing() -> None:
    """P2-12: der Ligakontext muss als Rechnung im Prompt stehen, nicht als Stimmung.

    „Sei mutiger, wenn du zurückliegst" ist keine Anweisung, die ein Modell
    anwenden kann — es braucht die beiden Zahlen und die Richtung: Rückstand
    gegen Restspieltage, und was daraus für die Auswahl folgt. Ohne den
    Ausfall-Satz wäre zudem offen, was bei fehlendem Block gilt.
    """
    prompt = load_system_prompt()
    for field_name in (
        "`league`",
        "`points_behind_leader`",
        "`points_to_next_rank`",
        "`matchdays_left`",
        "`rivals[]`",
    ):
        assert field_name in prompt, f"{field_name} fehlt in §1"
    assert "Varianz ist\n  dann der einzige Weg" in prompt or "Varianz" in prompt
    assert "missing_data:league_ranking" in prompt


def test_prompt_does_not_promise_the_rivals_names() -> None:
    """Der Payload schickt keine Klarnamen — der Prompt darf sie nicht anfordern.

    Ein Modell, das nach einem Feld greift, das nicht kommt, erfindet es (§7).
    Und der Grund für das Fehlen ist kein Versehen: der Payload geht an die
    Anthropic-API, Rang und Punkte tragen jede Entscheidung, ein Name trägt
    keine.
    """
    prompt = load_system_prompt()
    assert "Namen stehen dort bewusst nicht" in prompt


def test_prompt_puts_the_season_clock_next_to_the_matchday_clock() -> None:
    """P2-14: zwei Uhren, und der Prompt muss sagen, welche wofür gilt.

    `phase` misst bis zum Anpfiff, `season_phase` bis zum Saisonende. Fehlt die
    zweite, tradet der Bot im Mai weiter, als wäre im August — der Gewinn käme
    dann zu spät, um noch in Punkte umgesetzt zu werden.
    """
    prompt = load_system_prompt()
    assert "`trading.season_phase`" in prompt
    for label in ("`regular`", "`endgame`", "`over`"):
        assert label in prompt, f"Saisonphase {label} fehlt in §1"
    assert "Trading fällt aus der Zielhierarchie" in prompt
    # Der Ausfall muss geregelt sein, sonst rät das Modell.
    assert "`unknown`" in prompt
