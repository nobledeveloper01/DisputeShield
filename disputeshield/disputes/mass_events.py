"""One investigation, many cases (amplifier A3).

The hard part is stated in the amplifier: bulk resolution is a bulk-edit surface
over immutable records, which is precisely what §8.3 forbids. So the fan-out is a
fan-out — each case is saved individually and each gets its own audit record.
It is slower than a `queryset.update()`, and slow-and-auditable is correct here.

The one thing that *is* batched is the advisory lock on the audit chain.
ADR-0003 anticipated exactly this: five thousand appends still produce five
thousand individually hashed records, but they need not acquire the chain lock
five thousand times.
"""

from __future__ import annotations

import dataclasses

from django.db import transaction
from django.utils import timezone

from disputeshield import audit
from disputeshield.disputes.states import ClockEffect, find
from disputeshield.models import (
    Dispute,
    MassEvent,
    MassEventMembership,
    SLAClock,
    SLAEvent,
)
from disputeshield.models.dispute import Outcome, Status

APPLICABLE_FROM = frozenset({Status.INVESTIGATING, Status.ESCALATED})


@dataclasses.dataclass(frozen=True)
class FanOutResult:
    applied: int
    skipped: tuple[str, ...]


class NotApplicable(ValueError):
    pass


def add(*, event: MassEvent, dispute: Dispute, actor_id: str) -> MassEventMembership:
    membership, created = MassEventMembership.objects.get_or_create(
        tenant=event.tenant, mass_event=event, dispute=dispute, defaults={"added_by": actor_id}
    )
    if created:
        audit.append(
            tenant=event.tenant,
            event_type="mass_event.case_added",
            subject_type="dispute",
            subject_id=dispute.pk,
            actor_type="user",
            actor_id=actor_id,
            payload={"mass_event_id": event.pk, "title": event.title},
        )
    return membership


def remove(*, membership: MassEventMembership, actor_id: str, reason: str) -> MassEventMembership:
    """Close the membership; never delete it.

    A case removed from an event keeps everything that happened to it while it
    was a member. That it was once grouped with four thousand others is part of
    how it was handled, and deleting the row would remove the only evidence of
    why an outcome was applied.
    """
    if not reason.strip():
        raise ValueError("Removing a case from a mass event requires a reason.")

    membership.removed_at = timezone.now()
    membership.removed_by = actor_id
    membership.removal_reason = reason[:255]
    membership.save(update_fields=["removed_at", "removed_by", "removal_reason"])

    audit.append(
        tenant=membership.tenant,
        event_type="mass_event.case_removed",
        subject_type="dispute",
        subject_id=membership.dispute_id,
        actor_type="user",
        actor_id=actor_id,
        payload={"mass_event_id": membership.mass_event_id, "reason": reason},
    )
    return membership


def apply_outcome(
    *,
    event: MassEvent,
    outcome: str,
    notes: str,
    actor_id: str,
    refund_amount_minor: int | None = None,
    batch_size: int = 500,
) -> FanOutResult:
    """Apply one finding to every member case, individually.

    No `queryset.update()` anywhere in this function, and a test counts the
    statements to prove it. Each case gets its own row write, its own SLA event
    and its own audit record naming the mass event it came from — so six months
    later a supervisor asking why *this* case was rejected finds a specific
    answer rather than "it was part of a batch".
    """
    if outcome not in Outcome.values:
        raise ValueError(f"outcome must be one of {Outcome.values}, got {outcome!r}")
    if not notes.strip():
        raise ValueError("A mass resolution requires notes explaining the finding.")

    applied = 0
    skipped: list[str] = []
    now = timezone.now()

    memberships = (
        MassEventMembership.objects.filter(
            mass_event=event, removed_at__isnull=True, outcome_applied_at__isnull=True
        )
        .select_related("dispute", "dispute__clock")
        .order_by("pk")
    )

    while True:
        batch = list(memberships[:batch_size])
        if not batch:
            break
        applied_now, skipped_now = _apply_batch(
            event, batch, outcome, notes, actor_id, refund_amount_minor, now
        )
        applied += applied_now
        skipped.extend(skipped_now)
        if applied_now == 0 and not skipped_now:
            break

    if applied:
        event.status = MassEvent.Status.APPLIED
        event.finding = notes
        event.applied_at = now
        event.applied_by = actor_id
        event.save(update_fields=["status", "finding", "applied_at", "applied_by"])

    return FanOutResult(applied=applied, skipped=tuple(skipped))


def _apply_batch(event, batch, outcome, notes, actor_id, refund, now) -> tuple[int, list[str]]:
    skipped: list[str] = []
    audit_entries: list[dict] = []
    sla_events: list[SLAEvent] = []
    applied = 0

    with transaction.atomic():
        for membership in batch:
            dispute = membership.dispute
            if dispute.status not in APPLICABLE_FROM:
                skipped.append(dispute.reference)
                membership.outcome_applied_at = now
                membership.save(update_fields=["outcome_applied_at"])
                continue

            rule = find(dispute.status, Status.RESOLVED)

            # One row write per case. Not a bulk update — §8.3 forbids a
            # bulk-edit surface over auditable records, and the statement-count
            # test asserts this stays true.
            dispute.outcome = outcome
            dispute.outcome_notes = notes
            dispute.refund_amount_minor = refund
            dispute.resolved_at = now
            dispute.status = Status.RESOLVED
            dispute.save(
                update_fields=[
                    "outcome",
                    "outcome_notes",
                    "refund_amount_minor",
                    "resolved_at",
                    "status",
                ]
            )

            if (
                rule.clock_effect is ClockEffect.STOP
                and dispute.clock.state != SLAClock.State.STOPPED
            ):
                dispute.clock.state = SLAClock.State.STOPPED
                dispute.clock.stopped_at = now
                dispute.clock.save(update_fields=["state", "stopped_at"])
                sla_events.append(
                    SLAEvent(
                        tenant=event.tenant,
                        clock=dispute.clock,
                        kind=SLAEvent.Kind.STOPPED,
                        actor_type="user",
                        actor_id=actor_id,
                        clock_remaining_seconds=0,
                        occurred_at=now,
                    )
                )

            membership.outcome_applied_at = now
            membership.save(update_fields=["outcome_applied_at"])

            audit_entries.append(
                {
                    "event_type": "dispute.resolve",
                    "subject_type": "dispute",
                    "subject_id": dispute.pk,
                    "actor_type": "user",
                    "actor_id": actor_id,
                    "occurred_at": now,
                    "payload": {
                        "from": rule.source,
                        "to": Status.RESOLVED,
                        "reason": notes,
                        "outcome": outcome,
                        "refund_amount_minor": refund,
                        "clock_remaining_seconds": 0,
                        # Names the event, so a supervisor asking why this case
                        # was resolved this way finds the investigation.
                        "mass_event_id": event.pk,
                        "mass_event_title": event.title,
                    },
                }
            )
            applied += 1

        if sla_events:
            SLAEvent.objects.bulk_create(sla_events, batch_size=1000)
        # Individual records, one lock acquisition (ADR-0003).
        audit.append_batch(tenant=event.tenant, entries=audit_entries)

    return applied, skipped
