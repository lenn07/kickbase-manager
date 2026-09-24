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
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.application.tick_windows import (
    DAILY_WINDOWS,
    WEEKLY_WINDOWS,
    upcoming_matchday_windows,
)

_log = logging.getLogger(__name__)

_JOB_ID = "kickbase-tick"
_DIGEST_JOB_ID = "kickbase-hold-digest"
# Präfix aller Fenster-Jobs (P1-10). Eigene IDs, damit `set_windows()` sie
# gezielt ersetzen kann, ohne den Intervall-Job anzufassen.
_WINDOW_JOB_PREFIX = "kickbase-window-"


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
        self._timezone = timezone
        self._scheduler = AsyncIOScheduler(timezone=timezone)
        self._paused = False
        # Ein Lock über **alle** Auslöser, nicht pro Job (P1-10).
        #
        # `max_instances=1` verhindert nur, dass derselbe Job doppelt läuft.
        # Seit es Fenster-Jobs gibt, konkurrieren aber verschiedene Jobs: das
        # 21:45-Fenster und der Intervall-Job können auf dieselbe Minute
        # fallen. Dann liefen zwei Ticks gleichzeitig gegen dieselbe
        # Kickbase-API und dieselbe DB — zwei Entscheidungen, zwei Trades, und
        # das direkte Gegenteil von „genau eine Aktion pro Tick". Dasselbe gilt
        # für `trigger_now()` aus dem Dashboard.
        self._tick_lock = asyncio.Lock()

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
        """Führt sofort einen Tick aus — nützlich für UI und Tests.

        Wartet auf einen laufenden Tick, statt danebenzulaufen: ein Klick im
        Dashboard soll einen Tick auslösen, nicht einen zweiten parallelen.
        """
        async with self._tick_lock:
            return await self._tick()

    # -- Ereignis-Fenster (P1-10) --------------------------------------

    def set_windows(self, next_matchday_start: datetime | None, *, now: datetime) -> list[str]:
        """Setzt die Ereignis-Fenster neu und meldet, welche gesetzt wurden.

        Fixe Uhrzeiten (vor/nach dem Marktwert-Update, Montags-Review) laufen
        als Cron in der Scheduler-Zeitzone. Die beweglichen hängen am Anpfiff
        und werden bei jedem Aufruf neu gelegt — `next_matchday_start` wandert
        weiter, sobald ein Spieltag durch ist.

        Alte Fenster-Jobs werden vorher entfernt, damit ein abgelaufener
        Deadline-Job nicht als Leiche stehenbleibt. Der Intervall-Job bleibt
        unberührt: er ist der Fallback für alles, was in kein Fenster fällt.
        """
        self._clear_window_jobs()
        names: list[str] = []

        for name, hour, minute in DAILY_WINDOWS:
            self._add_window_job(name, CronTrigger(hour=hour, minute=minute))
            names.append(name)

        for name, weekday, hour, minute in WEEKLY_WINDOWS:
            self._add_window_job(name, CronTrigger(day_of_week=weekday, hour=hour, minute=minute))
            names.append(name)

        for window in upcoming_matchday_windows(next_matchday_start, now=now):
            self._add_window_job(window.name, DateTrigger(run_date=window.at))
            names.append(window.name)

        _log.info("Ereignis-Fenster gesetzt: %s", ", ".join(names))
        return names

    def window_job_ids(self) -> list[str]:
        return sorted(
            job.id for job in self._scheduler.get_jobs() if job.id.startswith(_WINDOW_JOB_PREFIX)
        )

    def _add_window_job(self, name: str, trigger: CronTrigger | DateTrigger) -> None:
        self._scheduler.add_job(
            self._safe_tick,
            trigger=trigger,
            id=f"{_WINDOW_JOB_PREFIX}{name}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            # Ein Fenster, das der Container verschlafen hat (Neustart,
            # überlanger Tick), soll noch kurz danach nachziehen dürfen — aber
            # nicht Stunden später, wenn die Lage eine andere ist.
            misfire_grace_time=300,
        )

    def _clear_window_jobs(self) -> None:
        for job_id in self.window_job_ids():
            self._scheduler.remove_job(job_id)

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
        #
        # Fällt ein Fenster mit dem Intervall-Job zusammen, gewinnt der erste
        # und der zweite überspringt: ein Tick pro Moment, egal wie viele
        # Auslöser ihn wollen. Warten statt Überspringen wäre falsch — der
        # zweite Lauf hätte dieselbe Lage neu bewertet und könnte ein zweites
        # Mal handeln.
        if self._tick_lock.locked():
            _log.info("Tick läuft bereits — dieser Auslöser wird übersprungen.")
            return
        async with self._tick_lock:
            try:
                await self._tick()
            except Exception:
                _log.exception("Scheduler-Tick fehlgeschlagen.")
