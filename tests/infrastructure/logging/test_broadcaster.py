"""Tests für LogBroadcaster + Handler."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import UTC, datetime

import pytest
from app.infrastructure.logging import BroadcastLogHandler, LogBroadcaster, LogEntry


def _entry(msg: str = "hello") -> LogEntry:
    return LogEntry(ts=datetime.now(UTC), level="INFO", logger="test", message=msg)


def test_publish_stores_history() -> None:
    b = LogBroadcaster(buffer_size=3)
    b.publish(_entry("1"))
    b.publish(_entry("2"))
    assert [e.message for e in b.history()] == ["1", "2"]


def test_publish_evicts_oldest_when_buffer_full() -> None:
    b = LogBroadcaster(buffer_size=2)
    b.publish(_entry("a"))
    b.publish(_entry("b"))
    b.publish(_entry("c"))
    assert [e.message for e in b.history()] == ["b", "c"]


async def test_subscribe_receives_new_entries() -> None:
    b = LogBroadcaster()
    b.bind_loop(asyncio.get_running_loop())
    queue = b.subscribe()
    b.publish(_entry("live"))
    entry = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert entry.message == "live"
    b.unsubscribe(queue)
    assert b.subscriber_count() == 0


async def test_publish_from_thread_reaches_subscriber() -> None:
    b = LogBroadcaster()
    b.bind_loop(asyncio.get_running_loop())
    queue = b.subscribe()

    def _publish_in_thread() -> None:
        b.publish(_entry("threaded"))

    thread = threading.Thread(target=_publish_in_thread)
    thread.start()
    thread.join()

    entry = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert entry.message == "threaded"


async def test_full_queue_evicts_oldest_entry() -> None:
    b = LogBroadcaster()
    b.bind_loop(asyncio.get_running_loop())
    queue: asyncio.Queue[LogEntry] = asyncio.Queue(maxsize=2)
    # Direktes Handling der internen Set-Struktur, damit wir eine kleine Queue nutzen.
    b._subscribers.add(queue)

    for i in range(5):
        b.publish(_entry(str(i)))

    # Nach 5 publishes stehen die letzten 2 in der Queue.
    await asyncio.sleep(0)  # scheduled call_soon_threadsafe callbacks abarbeiten
    collected = [queue.get_nowait().message for _ in range(queue.qsize())]
    assert collected == ["3", "4"]


def test_handler_formats_and_publishes_records() -> None:
    b = LogBroadcaster()
    handler = BroadcastLogHandler(b)
    handler.setFormatter(logging.Formatter("%(message)s"))

    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hallo %s",
        args=("welt",),
        exc_info=None,
    )
    handler.emit(record)

    history = b.history()
    assert len(history) == 1
    assert history[0].message == "hallo welt"
    assert history[0].level == "INFO"
    assert history[0].logger == "app.test"


async def test_publish_without_loop_still_stores_history() -> None:
    b = LogBroadcaster()
    # Kein bind_loop — publish soll trotzdem den Puffer füllen, nur ohne Fan-Out.
    b.publish(_entry("no-loop"))
    assert [e.message for e in b.history()] == ["no-loop"]


def test_log_entry_to_dict_serializes_iso() -> None:
    ts = datetime(2026, 1, 2, 12, 30, 45, tzinfo=UTC)
    entry = LogEntry(ts=ts, level="WARNING", logger="x", message="m")
    assert entry.to_dict() == {
        "ts": "2026-01-02T12:30:45+00:00",
        "level": "WARNING",
        "logger": "x",
        "message": "m",
    }


@pytest.fixture(autouse=True)
def _reset_root_logger() -> None:
    """Root-Logger nach jedem Test aufräumen — falls ein Test Handler installiert."""
    yield
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, BroadcastLogHandler):
            root.removeHandler(h)
