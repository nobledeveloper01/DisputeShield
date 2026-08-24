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

from disputeshield import audit
from disputeshield.models import (
    NotificationOutbox,
    SLAClock,
    SLADeadline,
    SLAEvent,
    SweepHeartbeat,
)

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
        batch_fired, batch_created = _fire_batch(now, limit)
        fired += batch_fired
        created += batch_created
        if batch_fired < limit:
            break
    return fired, created


def _fire_batch(now: datetime, limit: int) -> tuple[int, int]:
    """Claim and fire one tranche inside a single transaction.

    Claiming and firing were separate transactions in the first version, which
    made `SKIP LOCKED` almost decorative: the claim's locks were released at its
    own commit, so every row had to be re-locked individually a moment later.
    Ten thousand due deadlines then cost ten thousand transactions, ten thousand
    advisory-locked audit appends and 75 seconds against a 60-second budget.

    Holding one transaction for the tranche keeps the row locks for as long as
    they mean anything, and lets the writes below be three statements instead of
    four per row.
    """
    with transaction.atomic():
        batch = list(
            SLADeadline.objects.all_tenants()
            .filter(fired_at__isnull=True, fires_at__lte=now)
            .exclude(clock__state=SLAClock.State.STOPPED)
            .order_by("fires_at")
            .select_related("clock", "clock__policy_version", "tenant")
            .select_for_update(skip_locked=True)[:limit]
        )
        if not batch:
            return 0, 0

        tenant = batch[0].tenant
        events = []
        notifications = []
        audit_entries = []

        for deadline in batch:
            deadline.fired_at = now
            clock = deadline.clock
            breached = deadline.kind in {
                SLADeadline.Kind.ACKNOWLEDGEMENT,
                SLADeadline.Kind.RESOLUTION,
            }
            kind = SLAEvent.Kind.BREACHED if breached else SLAEvent.Kind.WARNED
            # §11.5 step 5: a breach detected late must say so, so the lateness is
            # attributable to the systems cause rather than to the handling.
            late = max(0, int((now - deadline.fires_at).total_seconds()))
            payload = {
                "clock_id": clock.pk,
                "reason": "",
                "clock_remaining_seconds": 0,
                "deadline_kind": deadline.kind,
                "threshold_percent": deadline.threshold_percent,
                "detected_late_seconds": late,
            }

            events.append(
                SLAEvent(
                    tenant=tenant,
                    clock=clock,
                    kind=kind,
                    actor_type="system",
                    clock_remaining_seconds=0,
                    occurred_at=now,
                )
            )
            audit_entries.append(
                {
                    "event_type": f"sla.{kind}",
                    "subject_type": clock.subject_type,
                    "subject_id": clock.subject_id,
                    "actor_type": "system",
                    "occurred_at": now,
                    "payload": payload,
                }
            )
            notifications.append(
                NotificationOutbox(
                    tenant=tenant,
                    idempotency_key=idempotency_key(deadline),
                    event_type=f"sla.{deadline.kind}",
                    payload={
                        "clock_id": clock.pk,
                        "subject_type": clock.subject_type,
                        "subject_id": clock.subject_id,
                        "kind": deadline.kind,
                        "threshold_percent": deadline.threshold_percent,
                        "due_at": deadline.fires_at.isoformat(),
                    },
                )
            )

        SLADeadline.objects.bulk_update(batch, ["fired_at"], batch_size=1000)
        SLAEvent.objects.bulk_create(events, batch_size=1000)
        # `ignore_conflicts` makes a replay uneventful: the unique idempotency key
        # is what turns a second delivery attempt into a no-op rather than a
        # second page at 03:00.
        created = NotificationOutbox.objects.bulk_create(
            notifications, batch_size=1000, ignore_conflicts=True
        )
        audit.append_batch(tenant=tenant, entries=audit_entries)

        return len(batch), len(created)


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
