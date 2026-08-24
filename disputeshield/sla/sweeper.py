"""The sweep. §11.5 calls a stall here an outage of the compliance function itself.

Three properties carry that weight, and each is a design choice rather than care:

  * **Watermark-driven** (ADR-0007). It selects deadlines that are due, not cases
    that are open, so its cost tracks events rather than queue size. Otherwise the
    clock gets least reliable exactly when a tenant has most cases.
  * **Idempotent.** The notification is written in the same transaction that marks
    the deadline fired, under a deterministic idempotency key. A replay cannot
    double-notify, which is what makes the runbook's catch-up step safe to run
    during an incident rather than a second incident.
  * **`SKIP LOCKED`.** Several workers can sweep concurrently without double-firing,
    so throughput scales even though beat itself stays at exactly one replica.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from disputeshield.models import (
    NotificationOutbox,
    SLAClock,
    SLADeadline,
    SLAEvent,
    SweepHeartbeat,
)
from disputeshield.sla.clock import _record, remaining_seconds

BATCH_SIZE = 500


@dataclasses.dataclass(frozen=True)
class SweepResult:
    fired: int
    notifications_created: int
    duration_ms: int
    swept_at: datetime

    @property
    def quiet(self) -> bool:
        return self.fired == 0


def idempotency_key(deadline: SLADeadline) -> str:
    """Derived from what the notification is *about*, never from when it was made.

    This is the property that makes a replay safe. A key containing a timestamp
    or a random component would make every catch-up run produce a fresh set of
    pages for events that were already notified.
    """
    threshold = "" if deadline.threshold_percent is None else f":{deadline.threshold_percent}"
    return f"sla:{deadline.clock_id}:{deadline.kind}{threshold}"


def sweep(*, now: datetime | None = None, limit: int = BATCH_SIZE) -> SweepResult:
    """Fire everything due, across every tenant.

    Tenant by tenant, because row level security means a query with no tenant
    context returns nothing at all. A sweep written as one cross-tenant query
    passes its tests — which inherit a context from a fixture — and fires nothing
    in production, where Celery calls it with no context to inherit. The
    heartbeat stays fresh throughout, so §11.5's runbook never triggers.
    """
    from disputeshield.tenancy.platform import for_each_tenant

    now = now or timezone.now()
    started = timezone.now()
    fired = 0
    created = 0

    for tenant_fired, tenant_created in for_each_tenant(
        lambda _tenant_id: _sweep_one_tenant(now, limit)
    ):
        fired += tenant_fired
        created += tenant_created

    duration_ms = int((timezone.now() - started).total_seconds() * 1000)
    _beat(now, duration_ms, fired)
    return SweepResult(
        fired=fired, notifications_created=created, duration_ms=duration_ms, swept_at=now
    )


def _sweep_one_tenant(now: datetime, limit: int) -> tuple[int, int]:
    fired = created = 0
    while True:
        batch = _claim(now, limit)
        if not batch:
            break
        for deadline in batch:
            if _fire(deadline, now):
                created += 1
            fired += 1
        if len(batch) < limit:
            break
    return fired, created


def _claim(now: datetime, limit: int) -> list[SLADeadline]:
    """Take the next tranche of due deadlines, skipping any another worker holds."""
    with transaction.atomic():
        return list(
            SLADeadline.objects.all_tenants()
            .filter(fired_at__isnull=True, fires_at__lte=now)
            .exclude(clock__state=SLAClock.State.STOPPED)
            .order_by("fires_at")
            .select_related("clock", "clock__policy_version", "tenant")
            .select_for_update(skip_locked=True)[:limit]
        )


def _fire(deadline: SLADeadline, now: datetime) -> bool:
    """Mark fired and record the notification, in one transaction.

    Returns whether a notification row was created. A deadline whose notification
    already exists still gets marked fired — that is a replay, and the point of
    the idempotency key is that a replay is uneventful.
    """
    from disputeshield.tenancy.middleware import db_tenant_context

    with transaction.atomic(), db_tenant_context(deadline.tenant_id):
        # Re-read under the lock: another worker may have fired it between the
        # claim and here.
        current = SLADeadline.objects.all_tenants().select_for_update().get(pk=deadline.pk)
        if current.fired_at is not None:
            return False

        current.fired_at = now
        current.save(update_fields=["fired_at"])

        clock = current.clock
        breached = current.kind in {
            SLADeadline.Kind.ACKNOWLEDGEMENT,
            SLADeadline.Kind.RESOLUTION,
        }
        _record(
            clock,
            SLAEvent.Kind.BREACHED if breached else SLAEvent.Kind.WARNED,
            occurred_at=now,
            actor_type="system",
            payload={
                "deadline_kind": current.kind,
                "threshold_percent": current.threshold_percent,
                # §11.5 step 5: a breach detected late must say so, so that the
                # lateness is attributable to the systems cause rather than to
                # the handling of the case.
                "detected_late_seconds": max(0, int((now - current.fires_at).total_seconds())),
            },
        )

        _, created = NotificationOutbox.objects.get_or_create(
            tenant=clock.tenant,
            idempotency_key=idempotency_key(current),
            defaults={
                "event_type": f"sla.{current.kind}",
                "payload": {
                    "clock_id": clock.pk,
                    "subject_type": clock.subject_type,
                    "subject_id": clock.subject_id,
                    "kind": current.kind,
                    "threshold_percent": current.threshold_percent,
                    "due_at": current.fires_at.isoformat(),
                    "remaining_seconds": remaining_seconds(clock, at=now),
                },
            },
        )
        return created


def _beat(now: datetime, duration_ms: int, fired: int) -> None:
    """The dead-man's switch (§11.4).

    Written even when the sweep found nothing, because "nothing was due" and "the
    scheduler is dead" look identical from the outside and only one of them is an
    incident.
    """
    SweepHeartbeat.objects.update_or_create(
        singleton=True,
        defaults={"last_swept_at": now, "last_duration_ms": duration_ms, "last_fired_count": fired},
    )


def heartbeat_age_seconds(*, now: datetime | None = None) -> float | None:
    """Seconds since the sweep last ran. None if it has never run.

    None is the more dangerous answer, not the safer one: a deployment where beat
    never started has an API that is up, a dashboard that renders, and no clock.
    """
    now = now or timezone.now()
    beat = SweepHeartbeat.objects.first()
    if beat is None:
        return None
    return (now - beat.last_swept_at).total_seconds()
