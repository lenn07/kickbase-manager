"""Dünner Wrapper um `AsyncIOScheduler` — kein Broker, kein Persistenz-Jobstore.

Der Scheduler bekommt eine Async-Callback-Factory (`tick_factory`), die pro Tick
frische Ressourcen (DB-Session, Kickbase-Client) provisioniert und einen
Use-Case ausführt. So bleibt der Scheduler selbst zustandslos.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

_log = logging.getLogger(__name__)

_JOB_ID = "kickbase-tick"
_DIGEST_JOB_ID = "kickbase-hold-digest"


TickCallable = Callable[[], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class SchedulerStatus:
    running: bool
    paused: bool
    interval_min: int
    next_run: datetime | None


class KickbaseScheduler:
    def __init__(
        self,
        *,
        tick: TickCallable,
        interval_min: int,
        timezone: str = "Europe/Berlin",
    ) -> None:
        if interval_min <= 0:
            raise ValueError("interval_min muss positiv sein.")
        self._tick = tick
        self._interval_min = interval_min
        self._scheduler = AsyncIOScheduler(timezone=timezone)
        self._paused = False

    # -- Lifecycle -----------------------------------------------------

    def start(self) -> None:
        if self._scheduler.running:
            return
        self._scheduler.add_job(
            self._safe_tick,
            trigger=IntervalTrigger(minutes=self._interval_min),
            id=_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.start()
        _log.info("Scheduler gestartet — Intervall %d min", self._interval_min)

    async def shutdown(self, *, wait: bool = False) -> None:
        """Fährt den Scheduler herunter und yieldet, bis der Zustand STOPPED ist.

        `AsyncIOScheduler.shutdown()` routet über `call_soon_threadsafe`; ohne
        Event-Loop-Yield bliebe `running` fälschlich True.
        """
        if not self._scheduler.running:
            return
        self._scheduler.shutdown(wait=wait)
        for _ in range(10):
            if not self._scheduler.running:
                break
            await asyncio.sleep(0)
        _log.info("Scheduler gestoppt.")

    # -- Steuerung ------------------------------------------------------

    def pause(self) -> None:
        if not self._scheduler.running or self._paused:
            return
        self._scheduler.pause_job(_JOB_ID)
        self._paused = True

    def resume(self) -> None:
        if not self._scheduler.running or not self._paused:
            return
        self._scheduler.resume_job(_JOB_ID)
        self._paused = False

    def reschedule(self, interval_min: int) -> None:
        if interval_min <= 0:
            raise ValueError("interval_min muss positiv sein.")
        self._interval_min = interval_min
        if not self._scheduler.running:
            return
        self._scheduler.reschedule_job(_JOB_ID, trigger=IntervalTrigger(minutes=interval_min))
        _log.info("Scheduler-Intervall neu gesetzt: %d min", interval_min)

    async def trigger_now(self) -> Any:
        """Führt sofort einen Tick synchron aus — nützlich für UI und Tests."""
        return await self._tick()

    # -- HOLD-Digest (F-9) --------------------------------------------

    def set_digest(self, callback: TickCallable | None, *, hour: int = 20) -> None:
        """Setzt oder entfernt den täglichen Digest-Job (Cron auf `hour`).

        `callback=None` entfernt einen existierenden Job — nützlich, wenn der
        Nutzer den Digest im UI abschaltet. Der Wrapper schluckt Exceptions,
        analog zu `_safe_tick`, damit ein SMTP-Ausfall den Scheduler nicht kippt.
        """
        if not 0 <= hour <= 23:  # noqa: PLR2004 — Cron-Stunden 0..23
            raise ValueError("digest hour muss zwischen 0 und 23 liegen.")

        if callback is None:
            if self._scheduler.get_job(_DIGEST_JOB_ID) is not None:
                self._scheduler.remove_job(_DIGEST_JOB_ID)
                _log.info("Digest-Job entfernt.")
            return

        async def safe_digest() -> None:
            try:
                await callback()
            except Exception:
                _log.exception("HOLD-Digest fehlgeschlagen.")

        self._scheduler.add_job(
            safe_digest,
            trigger=CronTrigger(hour=hour, minute=0),
            id=_DIGEST_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _log.info("Digest-Job gesetzt — täglich um %02d:00 Uhr.", hour)

    def has_digest_job(self) -> bool:
        return self._scheduler.get_job(_DIGEST_JOB_ID) is not None

    # -- Introspection --------------------------------------------------

    def status(self) -> SchedulerStatus:
        next_run: datetime | None = None
        if self._scheduler.running:
            job = self._scheduler.get_job(_JOB_ID)
            if job is not None:
                next_run = job.next_run_time
        return SchedulerStatus(
            running=self._scheduler.running,
            paused=self._paused,
            interval_min=self._interval_min,
            next_run=next_run,
        )

    # -- Intern --------------------------------------------------------

    async def _safe_tick(self) -> None:
        # Scheduler-Loop soll auch bei unerwarteten Tick-Fehlern weiterlaufen;
        # der Fehler landet im Log, damit Debugging möglich bleibt.
        try:
            await self._tick()
        except Exception:
            _log.exception("Scheduler-Tick fehlgeschlagen.")
