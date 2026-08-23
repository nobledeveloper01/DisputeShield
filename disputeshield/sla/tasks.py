"""Celery entry points. Thin on purpose: the logic is testable without a broker."""

from __future__ import annotations

from celery import shared_task

from disputeshield.sla import sweeper


@shared_task(name="disputeshield.sla.sweep")
def sweep() -> dict:
    result = sweeper.sweep()
    return {"fired": result.fired, "duration_ms": result.duration_ms}


@shared_task(name="disputeshield.sla.reconcile_deadlines")
def reconcile_deadlines() -> dict:
    from disputeshield.sla.reconcile import reconcile

    result = reconcile()
    return {"checked": result.checked, "mismatched": len(result.mismatches)}
