"""P1-10 — die Fenster-Berechnung (Defekt D12).

Zwei Dinge können hier still schiefgehen, und beide kosten Punkte statt nur
Requests: ein Deadline-Fenster, das auf Freitag verdrahtet ist und die
englische Woche verpasst — und ein Fenster in der Vergangenheit, das
APScheduler kommentarlos wegwirft.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.tick_windows import (
    DAILY_WINDOWS,
    WEEKLY_WINDOWS,
    matchday_windows,
    upcoming_matchday_windows,
)

FRIDAY_KICKOFF = datetime(2026, 10, 9, 18, 30, tzinfo=UTC)
TUESDAY_KICKOFF = datetime(2026, 10, 20, 16, 30, tzinfo=UTC)


def test_deadline_window_is_relative_to_the_kickoff() -> None:
    """Der Test, den der Plan verlangt: ein Dienstags-Spieltag.

    Englische Wochen pfeifen dienstags an. Ein Fenster, das auf „Freitag
    20:30 minus 45 Minuten" verdrahtet ist, verpasst genau diesen Spieltag —
    und ein negatives Konto kostet dort dieselben 0 Punkte wie am Freitag.
    """
    windows = {w.name: w.at for w in matchday_windows(TUESDAY_KICKOFF)}
    assert windows["pre_deadline"] == TUESDAY_KICKOFF - timedelta(minutes=45)
    assert windows["debt_window_open"] == TUESDAY_KICKOFF + timedelta(minutes=5)
    # Und für den Regelfall dasselbe, mit demselben Code.
    friday = {w.name: w.at for w in matchday_windows(FRIDAY_KICKOFF)}
    assert friday["pre_deadline"] == datetime(2026, 10, 9, 17, 45, tzinfo=UTC)


def test_without_a_known_kickoff_nothing_is_scheduled() -> None:
    """Kein Anpfiff bekannt ⇒ kein erfundener Zeitpunkt, nur der Intervall-Fallback."""
    assert matchday_windows(None) == ()
    assert upcoming_matchday_windows(None, now=FRIDAY_KICKOFF) == ()


def test_past_windows_are_dropped() -> None:
    """Ein `DateTrigger` in der Vergangenheit feuert nie — er wird still verworfen.

    Genau das passiert kurz nach dem Anpfiff mit `pre_deadline`. Hier
    herausgefiltert wird daraus ein sichtbar fehlendes Fenster statt eines
    Jobs, der aussieht, als stünde er.
    """
    just_after = FRIDAY_KICKOFF + timedelta(minutes=1)
    names = [w.name for w in upcoming_matchday_windows(FRIDAY_KICKOFF, now=just_after)]
    assert names == ["debt_window_open"]

    long_after = FRIDAY_KICKOFF + timedelta(hours=3)
    assert upcoming_matchday_windows(FRIDAY_KICKOFF, now=long_after) == ()


def test_daily_windows_bracket_the_market_value_update() -> None:
    """21:45 und 22:15 rahmen das 22:00-Update ein — davor handeln, danach werten.

    Liegen beide auf derselben Seite, ist eines der beiden wertlos: vor dem
    Update gibt es nichts auszuwerten, danach nichts mehr mitzunehmen.
    """
    by_name = {name: (hour, minute) for name, hour, minute in DAILY_WINDOWS}
    assert by_name["pre_market_value_update"] < (22, 0)
    assert by_name["post_market_value_update"] > (22, 0)


def test_weekly_review_lands_after_the_final_points() -> None:
    """Montag 18:30 — vorher stehen die Spieltagspunkte noch nicht fest."""
    assert WEEKLY_WINDOWS == (("matchday_review", "mon", 18, 30),)
