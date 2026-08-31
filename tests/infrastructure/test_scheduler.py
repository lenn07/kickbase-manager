from __future__ import annotations

import pytest
from app.infrastructure.scheduler.scheduler import KickbaseScheduler


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
