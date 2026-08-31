"""JSON-Control-API für den Auto-Loop (F-8 Kill-Switch).

Alle Endpoints sind idempotent und geben denselben Status-Body zurück, damit
UI-Aufrufer nach jeder Aktion `POST` + Refresh brauchen — kein separater GET.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.application.run_tick_uc import TickOutcome
from app.infrastructure.metrics import get_metrics
from app.infrastructure.scheduler import KickbaseScheduler

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


class SchedulerStatusOut(BaseModel):
    enabled: bool
    running: bool
    paused: bool
    interval_min: int | None
    next_run: datetime | None


class TickOutcomeOut(BaseModel):
    executed: bool
    action: str | None
    reason: str | None
    log_id: int | None
    skipped_reason: str | None

    @classmethod
    def from_outcome(cls, outcome: TickOutcome) -> TickOutcomeOut:
        return cls(
            executed=outcome.executed,
            action=outcome.decision.action.value if outcome.decision is not None else None,
            reason=outcome.decision.reason if outcome.decision is not None else None,
            log_id=outcome.log_id,
            skipped_reason=outcome.skipped_reason,
        )


def get_scheduler(request: Request) -> KickbaseScheduler | None:
    return request.app.state.scheduler  # type: ignore[no-any-return]


SchedulerDep = Annotated[KickbaseScheduler | None, Depends(get_scheduler)]


def _status_payload(scheduler: KickbaseScheduler | None) -> SchedulerStatusOut:
    metrics = get_metrics()
    if scheduler is None:
        metrics.scheduler_running.set(0)
        metrics.scheduler_paused.set(0)
        metrics.next_tick_ts.set(0)
        return SchedulerStatusOut(
            enabled=False, running=False, paused=False, interval_min=None, next_run=None
        )
    s = scheduler.status()
    metrics.scheduler_running.set(1 if s.running else 0)
    metrics.scheduler_paused.set(1 if s.paused else 0)
    metrics.next_tick_ts.set(s.next_run.timestamp() if s.next_run is not None else 0)
    return SchedulerStatusOut(
        enabled=True,
        running=s.running,
        paused=s.paused,
        interval_min=s.interval_min,
        next_run=s.next_run,
    )


def _require_scheduler(scheduler: KickbaseScheduler | None) -> KickbaseScheduler:
    if scheduler is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler deaktiviert.",
        )
    return scheduler


@router.get("/status", response_model=SchedulerStatusOut)
async def scheduler_status(scheduler: SchedulerDep) -> SchedulerStatusOut:
    return _status_payload(scheduler)


@router.post("/pause", response_model=SchedulerStatusOut)
async def scheduler_pause(scheduler: SchedulerDep) -> SchedulerStatusOut:
    _require_scheduler(scheduler).pause()
    return _status_payload(scheduler)


@router.post("/resume", response_model=SchedulerStatusOut)
async def scheduler_resume(scheduler: SchedulerDep) -> SchedulerStatusOut:
    _require_scheduler(scheduler).resume()
    return _status_payload(scheduler)


@router.post("/trigger", response_model=TickOutcomeOut)
async def scheduler_trigger(scheduler: SchedulerDep) -> TickOutcomeOut:
    outcome = await _require_scheduler(scheduler).trigger_now()
    if not isinstance(outcome, TickOutcome):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tick-Ergebnis unerwartetes Format.",
        )
    return TickOutcomeOut.from_outcome(outcome)
