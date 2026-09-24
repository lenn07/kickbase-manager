from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.infrastructure.scheduler.scheduler import KickbaseScheduler

_NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
_KICKOFF = datetime(2026, 10, 9, 18, 30, tzinfo=UTC)


async def _make_scheduler(counter: list[int]) -> KickbaseScheduler:
    async def tick() -> str:
        counter.append(1)
        return "ok"

    return KickbaseScheduler(tick=tick, interval_min=1)


async def test_trigger_now_runs_tick() -> None:
    counter: list[int] = []
    scheduler = await _make_scheduler(counter)
    result = await scheduler.trigger_now()
    assert result == "ok"
    assert counter == [1]


async def test_status_reports_not_running_before_start() -> None:
    scheduler = await _make_scheduler([])
    s = scheduler.status()
    assert s.running is False
    assert s.paused is False
    assert s.interval_min == 1
    assert s.next_run is None


async def test_start_stop_updates_status() -> None:
    counter: list[int] = []
    scheduler = await _make_scheduler(counter)
    scheduler.start()
    try:
        s = scheduler.status()
        assert s.running is True
        assert s.next_run is not None
    finally:
        await scheduler.shutdown()
    assert scheduler.status().running is False


async def test_pause_resume_marks_paused_flag() -> None:
    scheduler = await _make_scheduler([])
    scheduler.start()
    try:
        scheduler.pause()
        assert scheduler.status().paused is True
        scheduler.resume()
        assert scheduler.status().paused is False
    finally:
        await scheduler.shutdown()


async def test_reschedule_replaces_interval() -> None:
    scheduler = await _make_scheduler([])
    scheduler.start()
    try:
        scheduler.reschedule(5)
        assert scheduler.status().interval_min == 5
    finally:
        await scheduler.shutdown()


async def test_scheduler_swallows_tick_exceptions() -> None:
    calls: list[int] = []

    async def flaky_tick() -> None:
        calls.append(1)
        raise RuntimeError("boom")

    scheduler = KickbaseScheduler(tick=flaky_tick, interval_min=1)
    # `_safe_tick` fängt und loggt — kein Re-Raise nach oben.
    await scheduler._safe_tick()
    assert calls == [1]


def test_construct_with_zero_interval_raises() -> None:
    async def tick() -> None:
        return None

    with pytest.raises(ValueError, match="positiv"):
        KickbaseScheduler(tick=tick, interval_min=0)


async def test_set_digest_registers_and_removes_job() -> None:
    scheduler = await _make_scheduler([])
    scheduler.start()
    try:
        assert scheduler.has_digest_job() is False

        async def digest_cb() -> None:
            return None

        scheduler.set_digest(digest_cb, hour=7)
        assert scheduler.has_digest_job() is True

        scheduler.set_digest(None)
        assert scheduler.has_digest_job() is False
    finally:
        await scheduler.shutdown()


def test_set_digest_rejects_invalid_hour() -> None:
    async def tick() -> None:
        return None

    scheduler = KickbaseScheduler(tick=tick, interval_min=1)
    with pytest.raises(ValueError, match="zwischen 0 und 23"):
        scheduler.set_digest(lambda: (_ for _ in ()).throw(RuntimeError()), hour=24)  # type: ignore[arg-type]


# -- Ereignis-Fenster (P1-10) --------------------------------------------


async def test_windows_are_registered_alongside_the_interval_job() -> None:
    """Fenster ersetzen den Intervall-Job nicht, sie ergänzen ihn.

    Der Intervall-Job bleibt der Fallback für alles, was in kein Fenster
    fällt — ohne ihn liefe zwischen 22:15 und 21:45 des Folgetages nichts.
    """
    scheduler = await _make_scheduler([])
    scheduler.start()
    try:
        names = scheduler.set_windows(_KICKOFF, now=_NOW)
        assert "pre_deadline" in names
        assert "debt_window_open" in names
        assert len(scheduler.window_job_ids()) == len(names)
        # Der Intervall-Job steht weiterhin.
        assert scheduler.status().next_run is not None
    finally:
        await scheduler.shutdown()


async def test_setting_windows_again_replaces_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zweimal setzen darf keine Job-Leichen hinterlassen.

    `next_matchday_start` wandert nach jedem Spieltag weiter. Bliebe der alte
    Deadline-Job stehen, feuerte er zur Zeit des vorigen Spieltags.
    """
    del monkeypatch
    scheduler = await _make_scheduler([])
    scheduler.start()
    try:
        scheduler.set_windows(_KICKOFF, now=_NOW)
        first = scheduler.window_job_ids()
        scheduler.set_windows(_KICKOFF + timedelta(days=7), now=_NOW)
        assert scheduler.window_job_ids() == first
    finally:
        await scheduler.shutdown()


async def test_a_past_kickoff_leaves_only_the_fixed_windows() -> None:
    scheduler = await _make_scheduler([])
    scheduler.start()
    try:
        names = scheduler.set_windows(_KICKOFF, now=_KICKOFF + timedelta(hours=3))
        assert "pre_deadline" not in names
        assert "debt_window_open" not in names
        assert "pre_market_value_update" in names
    finally:
        await scheduler.shutdown()


async def test_two_triggers_in_the_same_moment_yield_one_tick() -> None:
    """Der Fall, den `max_instances=1` **nicht** abdeckt.

    Seit es Fenster-Jobs gibt, sind es verschiedene Jobs mit verschiedenen
    IDs — `max_instances` wirkt pro Job und lässt sie nebeneinander laufen.
    Zwei gleichzeitige Ticks hieße: zwei Entscheidungen auf derselben Lage,
    zwei Trades, doppelte Last gegen dieselbe API.
    """
    started: list[int] = []
    release = asyncio.Event()

    async def slow_tick() -> str:
        started.append(1)
        await release.wait()
        return "ok"

    scheduler = KickbaseScheduler(tick=slow_tick, interval_min=1)
    first = asyncio.create_task(scheduler._safe_tick())
    await asyncio.sleep(0)  # ersten Tick bis zum `await` laufen lassen
    second = asyncio.create_task(scheduler._safe_tick())
    await asyncio.sleep(0)

    release.set()
    await asyncio.gather(first, second)

    assert started == [1], "Der zweite Auslöser hat einen zweiten Tick gestartet."


async def test_manual_trigger_waits_instead_of_running_beside() -> None:
    """Ein Klick im Dashboard soll einen Tick auslösen, nicht einen zweiten.

    Anders als ein Zeitfenster wird ein manueller Auslöser nicht verworfen —
    der Nutzer hat ihn ausdrücklich angefordert und erwartet ein Ergebnis.
    """
    order: list[str] = []
    release = asyncio.Event()

    async def slow_tick() -> str:
        order.append("start")
        await release.wait()
        order.append("end")
        return "ok"

    scheduler = KickbaseScheduler(tick=slow_tick, interval_min=1)
    background = asyncio.create_task(scheduler._safe_tick())
    await asyncio.sleep(0)
    manual = asyncio.create_task(scheduler.trigger_now())
    await asyncio.sleep(0)

    release.set()
    await asyncio.gather(background, manual)

    assert order == ["start", "end", "start", "end"]
