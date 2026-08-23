"""Celery application.

Two worker pools, never one (§11.1). The `sla` queue carries short,
latency-sensitive sweep work and must never queue behind a slow notification
delivery — the SLO in §11.3 is on sweep freshness, and a shared pool makes that
SLO a function of how fast an email provider happens to be responding.

Beat runs as exactly one replica holding a leader lock. Two beat schedulers
double-fire every task, and in this product a double-fired task is a duplicate
breach page at 03:00.
"""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")

app = Celery("disputeshield")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.task_routes = {
    "disputeshield.sla.*": {"queue": "sla"},
    "disputeshield.notifications.*": {"queue": "notify"},
}

app.conf.beat_schedule = {
    "disputeshield-sla-sweep": {
        "task": "disputeshield.sla.sweep",
        "schedule": crontab(minute="*"),
    },
    "disputeshield-audit-verify": {
        "task": "disputeshield.audit.verify_chains",
        "schedule": crontab(hour=3, minute=0),
    },
    "disputeshield-deadline-reconcile": {
        # ADR-0007: materialised deadlines must not drift from what
        # compute_deadline would produce. Nothing recomputes them implicitly, so
        # something has to check.
        "task": "disputeshield.sla.reconcile_deadlines",
        "schedule": crontab(hour=4, minute=0),
    },
}
