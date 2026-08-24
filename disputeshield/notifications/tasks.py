from __future__ import annotations

from celery import shared_task


@shared_task(name="disputeshield.notifications.dispatch")
def dispatch() -> dict:
    from disputeshield.notifications.dispatcher import dispatch as run

    result = run()
    return {"sent": result.sent, "failed": result.failed, "exhausted": result.exhausted}


@shared_task(name="disputeshield.reports.run_schedules")
def run_report_schedules() -> dict:
    from disputeshield.reports.schedules import run_due

    result = run_due()
    return {
        "queued": result.queued,
        "retried": result.retried,
        "confirmed": result.already_sent,
        "abandoned": result.abandoned,
        "blocked": result.skipped_inactive,
    }
