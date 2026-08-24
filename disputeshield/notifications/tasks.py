from __future__ import annotations

from celery import shared_task


@shared_task(name="disputeshield.notifications.dispatch")
def dispatch() -> dict:
    from disputeshield.notifications.dispatcher import dispatch as run

    result = run()
    return {"sent": result.sent, "failed": result.failed, "exhausted": result.exhausted}
