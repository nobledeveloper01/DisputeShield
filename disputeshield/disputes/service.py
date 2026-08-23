"""Filing, moving and resolving a case.

Every function here is the *only* supported way to change what it changes. The
API calls these; the admin calls these (D10); a management command calls these.
That is what lets the audit trail be complete without qualification — there is no
second path that writes a dispute.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from disputeshield import audit
from disputeshield.disputes.states import ClockEffect, find
from disputeshield.models import (
    Agent,
    Dispute,
    DisputeMessage,
    SLAClock,
    SLADeadline,
)
from disputeshield.models.dispute import Outcome, Status, hash_customer_ref
from disputeshield.sla import clock as clock_service

REFERENCE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class ReasonRequired(ValueError):
    """A transition that changes what the firm owes the customer, unexplained."""


class ActorNotPermitted(ValueError):
    """A transition attempted by an actor type the table does not allow.

    `auto_close` is the one that matters: only the sweep may close a case for
    silence. A human closing a case and recording it as an automatic closure
    would misattribute a decision to the system.
    """


def file_dispute(
    *,
    tenant,
    customer_ref: str,
    category: str,
    description: str,
    policy_version,
    display_name: str = "",
    subcategory: str = "",
    transaction_ref: str = "",
    amount_minor: int | None = None,
    currency: str = "",
    submitted_at=None,
    actor_type: str = "api_key",
    actor_id: str = "",
) -> Dispute:
    # Checked here rather than left to surface from inside the audit append three
    # frames down. The default pair (`api_key`, `""`) is not a usable combination,
    # and a caller who accepts the defaults deserves to be told that by the
    # function they called.
    if actor_type != "system" and not actor_id:
        raise ValueError(
            f"filing as {actor_type!r} requires actor_id — every case names who filed it. "
            "Pass the API key id, the agent id, or actor_type='system'."
        )

    submitted_at = submitted_at or timezone.now()

    with transaction.atomic():
        clock = clock_service.start(
            tenant=tenant,
            subject_id="pending",
            policy_version=policy_version,
            started_at=submitted_at,
            actor_type=actor_type,
            actor_id=actor_id,
        )
        deadlines = {
            deadline.kind: deadline.fires_at
            for deadline in clock.deadlines.filter(
                kind__in=[SLADeadline.Kind.ACKNOWLEDGEMENT, SLADeadline.Kind.RESOLUTION]
            )
        }

        dispute = Dispute.objects.create(
            tenant=tenant,
            reference=_reference(tenant),
            customer_ref_hash=hash_customer_ref(tenant, customer_ref),
            customer_display_name=display_name,
            category=category,
            subcategory=subcategory,
            description=description,
            transaction_ref=transaction_ref,
            amount_minor=amount_minor,
            currency=currency,
            policy_version=policy_version,
            clock=clock,
            submitted_at=submitted_at,
            ack_deadline=deadlines[SLADeadline.Kind.ACKNOWLEDGEMENT],
            resolution_deadline=deadlines[SLADeadline.Kind.RESOLUTION],
        )

        # The clock was created before the case had an id, because the deadlines
        # it materialises are what the case stores. Point it back now.
        clock.subject_id = dispute.pk
        clock.save(update_fields=["subject_id"])

        audit.append(
            tenant=tenant,
            event_type="dispute.created",
            subject_type="dispute",
            subject_id=dispute.pk,
            actor_type=actor_type,
            actor_id=actor_id,
            occurred_at=submitted_at,
            payload={
                "reference": dispute.reference,
                "category": category,
                "policy_version": policy_version.pk,
                "resolution_deadline": dispute.resolution_deadline.isoformat(),
            },
        )
        return dispute


def transition(
    *,
    dispute: Dispute,
    to: str,
    actor_type: str,
    actor_id: str = "",
    reason: str = "",
    at=None,
    payload: dict | None = None,
) -> Dispute:
    """Move a case, applying the clock effect the transition table specifies."""
    rule = find(dispute.status, to)
    at = at or timezone.now()

    if rule.requires_reason and not reason.strip():
        raise ReasonRequired(
            f"{rule.trigger} requires a reason. It changes what the firm owes this "
            "customer, and an unexplained change is not evidence of anything."
        )
    if actor_type not in rule.actor_types:
        raise ActorNotPermitted(
            f"{rule.trigger} may be performed by {rule.actor_types}, not {actor_type!r}."
        )

    with transaction.atomic():
        previous = dispute.status
        dispute.status = to

        fields = ["status"]
        if to == Status.ACKNOWLEDGED and dispute.acknowledged_at is None:
            dispute.acknowledged_at = at
            fields.append("acknowledged_at")
        if to in {Status.CLOSED, Status.AUTO_CLOSED}:
            dispute.closed_at = at
            fields.append("closed_at")
        dispute.save(update_fields=fields)

        _apply_clock_effect(dispute, rule.clock_effect, reason, actor_type, actor_id, at)
        _sync_deadlines(dispute)

        audit.append(
            tenant=dispute.tenant,
            event_type=f"dispute.{rule.trigger}",
            subject_type="dispute",
            subject_id=dispute.pk,
            actor_type=actor_type,
            actor_id=actor_id,
            occurred_at=at,
            payload={
                "from": previous,
                "to": to,
                "reason": reason,
                # §3.4: the clock state at the moment of the transition. This is
                # the field that makes a breach explainable six months later.
                "clock_remaining_seconds": clock_service.remaining_seconds(dispute.clock, at=at),
                **(payload or {}),
            },
        )
        return dispute


def resolve(
    *,
    dispute: Dispute,
    outcome: str,
    actor_type: str,
    actor_id: str,
    notes: str,
    refund_amount_minor: int | None = None,
    at=None,
) -> Dispute:
    """Record an outcome. Records a refund amount; never moves money (§3.3)."""
    if outcome not in Outcome.values:
        raise ValueError(f"outcome must be one of {Outcome.values}, got {outcome!r}")
    if not notes.strip():
        raise ReasonRequired("A resolution requires notes explaining the outcome.")

    at = at or timezone.now()
    with transaction.atomic():
        dispute.outcome = outcome
        dispute.outcome_notes = notes
        dispute.refund_amount_minor = refund_amount_minor
        dispute.resolved_at = at
        dispute.save(
            update_fields=["outcome", "outcome_notes", "refund_amount_minor", "resolved_at"]
        )
        return transition(
            dispute=dispute,
            to=Status.RESOLVED,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=notes,
            at=at,
            payload={"outcome": outcome, "refund_amount_minor": refund_amount_minor},
        )


def add_message(
    *,
    dispute: Dispute,
    body: str,
    author_type: str,
    visibility: str,
    author_id: str = "",
) -> DisputeMessage:
    if not body.strip():
        raise ValueError("A message needs a body.")

    with transaction.atomic():
        message = DisputeMessage.objects.create(
            tenant=dispute.tenant,
            dispute=dispute,
            author_type=author_type,
            author_id=author_id,
            visibility=visibility,
            body=body,
        )
        audit.append(
            tenant=dispute.tenant,
            event_type="dispute.message_added",
            subject_type="dispute",
            subject_id=dispute.pk,
            actor_type="user" if author_type == "agent" else author_type,
            actor_id=author_id,
            payload={
                "message_id": message.pk,
                "visibility": visibility,
                # The body is deliberately not in the audit payload. It is already
                # stored, immutably, on the message; duplicating it here would put
                # customer content into a second place with a different retention
                # and a different export path.
                "length": len(body),
            },
        )
        return message


def assign(
    *, dispute: Dispute, agent: Agent | None, actor_type: str, actor_id: str, reason: str = ""
) -> Dispute:
    previous = dispute.assigned_to_id
    dispute.assigned_to = agent
    dispute.save(update_fields=["assigned_to"])

    audit.append(
        tenant=dispute.tenant,
        event_type="dispute.assigned",
        subject_type="dispute",
        subject_id=dispute.pk,
        actor_type=actor_type,
        actor_id=actor_id,
        payload={"from": previous, "to": agent.pk if agent else None, "reason": reason},
    )
    return dispute


def next_assignee(*, tenant, category: str) -> Agent | None:
    """Round-robin over active agents, by open case count.

    Least-loaded rather than strictly rotating: a rotation that ignores load
    hands the next case to whoever happens to be next, including the agent who
    already has thirty. §3.2 B4's requirement is that nothing sits unowned, and
    the useful version of that also keeps the queue survivable.
    """
    from django.db.models import Count, Q

    from disputeshield.disputes.states import TERMINAL

    return (
        Agent.objects.filter(is_active=True, role__in=[Agent.Role.AGENT, Agent.Role.OWNER])
        .annotate(
            open_cases=Count(
                "assigned_disputes",
                filter=~Q(assigned_disputes__status__in=list(TERMINAL)),
                distinct=True,
            )
        )
        .order_by("open_cases", "pk")
        .first()
    )


# -- internals -----------------------------------------------------------------


def _apply_clock_effect(dispute, effect, reason, actor_type, actor_id, at) -> None:
    if effect is ClockEffect.PAUSE:
        clock_service.pause(
            clock=dispute.clock, reason=reason, actor_type=actor_type, actor_id=actor_id, at=at
        )
    elif effect is ClockEffect.RESUME:
        clock_service.resume(
            clock=dispute.clock, reason=reason, actor_type=actor_type, actor_id=actor_id, at=at
        )
    elif effect is ClockEffect.STOP and dispute.clock.state != SLAClock.State.STOPPED:
        clock_service.stop(clock=dispute.clock, actor_type=actor_type, actor_id=actor_id, at=at)


def _sync_deadlines(dispute: Dispute) -> None:
    """Keep the denormalised queue-sort columns in step with the deadline rows."""
    deadlines = {
        deadline.kind: deadline
        for deadline in dispute.clock.deadlines.filter(
            kind__in=[SLADeadline.Kind.ACKNOWLEDGEMENT, SLADeadline.Kind.RESOLUTION]
        )
    }
    fields = []
    ack = deadlines.get(SLADeadline.Kind.ACKNOWLEDGEMENT)
    resolution = deadlines.get(SLADeadline.Kind.RESOLUTION)
    if ack and dispute.ack_deadline != ack.fires_at:
        dispute.ack_deadline = ack.fires_at
        fields.append("ack_deadline")
    if resolution and dispute.resolution_deadline != resolution.fires_at:
        dispute.resolution_deadline = resolution.fires_at
        fields.append("resolution_deadline")
    if fields:
        dispute.save(update_fields=fields)


def _reference(tenant) -> str:
    year = timezone.now().year
    body = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))
    return f"DS-{year}-{body}"


def mark_breached(*, dispute: Dispute, kind: str, reason: str = "") -> Dispute:
    """Called by the sweep. Recorded on the case so the queue can pin it."""
    fields = []
    if kind == SLADeadline.Kind.ACKNOWLEDGEMENT and not dispute.breach_ack:
        dispute.breach_ack = True
        fields.append("breach_ack")
    if kind == SLADeadline.Kind.RESOLUTION and not dispute.breach_resolution:
        dispute.breach_resolution = True
        fields.append("breach_resolution")
    if reason and reason not in dispute.breach_reason:
        dispute.breach_reason = f"{dispute.breach_reason}\n{reason}".strip()
        fields.append("breach_reason")
    if fields:
        dispute.save(update_fields=fields)
    return dispute


def auto_close_window(policy_version) -> timedelta:
    return timedelta(hours=policy_version.auto_close_after_hours)
