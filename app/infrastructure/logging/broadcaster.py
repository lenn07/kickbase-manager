"""In-Memory-Log-Broadcasting für den Dashboard-WebSocket (Phase 6).

Der `LogBroadcaster` kombiniert einen Ringpuffer (History für neu verbundene
Clients) mit einem Fan-Out an async Queues (Live-Stream). Ein an den Root-Logger
gehängter `BroadcastLogHandler` speist ihn.

Thread-Safety: APScheduler-Tick läuft im Event-Loop, aber `logging.Handler.emit`
kann prinzipiell aus anderen Threads gefeuert werden. Deshalb wird das
Puffer-Append über einen `threading.Lock` geschützt und die Zustellung an
Queues über `loop.call_soon_threadsafe`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

_MAX_HISTORY = 200
_QUEUE_MAX = 100


@dataclass(frozen=True, slots=True)
class LogEntry:
    ts: datetime
    level: str
    logger: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "ts": self.ts.isoformat(),
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
        }


class LogBroadcaster:
    """Ringpuffer + Fan-Out an subscribed Queues."""

    def __init__(self, *, buffer_size: int = _MAX_HISTORY) -> None:
        self._buffer: deque[LogEntry] = deque(maxlen=buffer_size)
        self._subscribers: set[asyncio.Queue[LogEntry]] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Muss vom Async-Kontext einmal aufgerufen werden (App-Start)."""
        self._loop = loop

    def publish(self, entry: LogEntry) -> None:
        """Wird von jedem Handler-Thread aufgerufen; muss thread-safe sein."""
        with self._lock:
            self._buffer.append(entry)
            subscribers = list(self._subscribers)
            loop = self._loop

        if loop is None or not subscribers:
            return

        for queue in subscribers:
            # Loop kann während Shutdown geschlossen sein → Entry verwerfen.
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._offer, queue, entry)

    @staticmethod
    def _offer(queue: asyncio.Queue[LogEntry], entry: LogEntry) -> None:
        # Bei vollem Puffer: ältesten Eintrag verwerfen, damit langsame Clients
        # den Stream nicht blockieren.
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return
        queue.put_nowait(entry)

    def subscribe(self) -> asyncio.Queue[LogEntry]:
        queue: asyncio.Queue[LogEntry] = asyncio.Queue(maxsize=_QUEUE_MAX)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[LogEntry]) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def history(self) -> list[LogEntry]:
        with self._lock:
            return list(self._buffer)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


class BroadcastLogHandler(logging.Handler):
    """Logging-Handler, der jedes Record an einen `LogBroadcaster` weiterreicht."""

    def __init__(self, broadcaster: LogBroadcaster, *, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._broadcaster = broadcaster

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        entry = LogEntry(
            ts=datetime.fromtimestamp(record.created, tz=UTC),
            level=record.levelname,
            logger=record.name,
            message=message,
        )
        self._broadcaster.publish(entry)
