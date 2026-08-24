"""Deciding what an inbound message is, and refusing to guess.

A1's guardrail, stated once: **channel identity never grants case access on its
own.** A message whose sender is not the case's verified contact is quarantined
for a human to attribute — never appended, and never echoed back into the thread.

The failure mode this prevents is not subtle. Match too permissively and one
customer's message is attached to another customer's case, which is a data breach
rather than a bug. So every path that could attach a message to a case checks the
sender against `DisputeContact`, and the path that cannot decide has its own
state (`unmatched_review`) rather than a default.
"""

from __future__ import annotations

import dataclasses

from django.db import transaction
from django.utils import timezone

from disputeshield import audit
from disputeshield.disputes import service
from disputeshield.intake.normalise import Inbound, normalise
from disputeshield.models import (
    Channel,
    Dispute,
    DisputeContact,
    DisputeMessage,
    InboundMessage,
    SLAPolicy,
    hash_identity,
)


@dataclasses.dataclass(frozen=True)
class Routed:
    record: InboundMessage
    dispute: Dispute | None

    @property
    def state(self) -> str:
        return self.record.state


def receive(*, tenant, channel: str, payload: dict, default_category: str = "other") -> Routed:
    """Record what arrived, then decide. In that order.

    Recording first matters: a message that arrived and was silently dropped is a
    complaint the firm cannot prove it never received, and "we never got it" is
    not a defence anyone accepts twice.
    """
    inbound = normalise(channel, payload)

    with transaction.atomic():
        record = InboundMessage.objects.create(
            tenant=tenant,
            channel=inbound.channel,
            from_identity_hash=hash_identity(tenant, inbound.from_identity),
            thread_key_hash=(
                hash_identity(tenant, inbound.thread_key) if inbound.thread_key else ""
            ),
            subject=inbound.subject[:255],
            body=inbound.body,
            received_at=inbound.received_at,
            state=InboundMessage.State.UNMATCHED_REVIEW,
        )

        if inbound.is_auto_reply:
            # Treating an out-of-office as a customer's response would resume a
            # paused clock on the strength of a mail server's holiday message.
            return _record_disposition(record, InboundMessage.State.IGNORED, "auto-reply or bounce")

        if not inbound.from_identity:
            return _record_disposition(
                record, InboundMessage.State.UNMATCHED_REVIEW, "no sender identity"
            )

        existing = _find_case(tenant, inbound, record)
        if existing is not None:
            return _append_to(record, existing, inbound, tenant)

        return _file_new(record, inbound, tenant, default_category)


def _find_case(tenant, inbound: Inbound, record: InboundMessage) -> Dispute | None:
    """A quoted reference first, then the thread. Never the subject line."""
    if inbound.quoted_reference:
        return Dispute.objects.filter(reference=inbound.quoted_reference).first()

    if record.thread_key_hash:
        prior = (
            InboundMessage.objects.filter(
                channel=inbound.channel,
                thread_key_hash=record.thread_key_hash,
                dispute__isnull=False,
            )
            .order_by("-received_at")
            .first()
        )
        if prior is not None:
            return prior.dispute
    return None


def _append_to(record: InboundMessage, dispute: Dispute, inbound: Inbound, tenant) -> Routed:
    """Append only if the sender is this case's verified contact for this channel."""
    if not DisputeContact.objects.filter(
        dispute=dispute, channel=inbound.channel, identity_hash=record.from_identity_hash
    ).exists():
        audit.append(
            tenant=tenant,
            event_type="intake.quarantined",
            subject_type="dispute",
            subject_id=dispute.pk,
            actor_type="system",
            payload={
                "inbound_id": record.pk,
                "channel": inbound.channel,
                # The hash, never the address. A quarantine queue is reviewed by
                # people, and it should not be a directory of customers.
                "from_identity_hash": record.from_identity_hash,
                "reason": "sender is not a verified contact for this case",
            },
        )
        return _record_disposition(
            record,
            InboundMessage.State.QUARANTINED,
            "sender is not a verified contact for this case",
            dispute=dispute,
        )

    message = service.add_message(
        dispute=dispute,
        body=inbound.body,
        author_type=DisputeMessage.AuthorType.CUSTOMER,
        visibility=DisputeMessage.Visibility.CUSTOMER,
    )
    _ = message
    return _record_disposition(record, InboundMessage.State.MATCHED, "", dispute=dispute)


def _file_new(record: InboundMessage, inbound: Inbound, tenant, default_category: str) -> Routed:
    """Open a case, on the same clock every other channel gets."""
    category = inbound.category or default_category
    policy = SLAPolicy.objects.filter(category=category).first()
    if policy is None or policy.current_version is None:
        policy = SLAPolicy.objects.filter(category=default_category).first()
    if policy is None or policy.current_version is None:
        return _record_disposition(
            record,
            InboundMessage.State.UNMATCHED_REVIEW,
            f"no SLA policy for {category!r} and no default",
        )

    dispute = service.file_dispute(
        tenant=tenant,
        # The channel identity is the customer reference for channels where we
        # have nothing better. It is hashed identically either way, so a case
        # filed by WhatsApp is scoped exactly like one filed by the widget.
        customer_ref_hash=record.from_identity_hash,
        category=policy.category,
        description=inbound.body,
        policy_version=policy.current_version,
        transaction_ref=inbound.transaction_ref,
        actor_type="system",
    )

    # The sender becomes this case's verified contact for this channel — they
    # opened it, so they are by construction the person it belongs to. Every
    # later message from anyone else is quarantined.
    DisputeContact.objects.create(
        tenant=tenant,
        dispute=dispute,
        channel=inbound.channel,
        identity_hash=record.from_identity_hash,
        verified_at=timezone.now(),
    )
    if record.thread_key_hash:
        record.dispute = dispute

    audit.append(
        tenant=tenant,
        event_type="intake.filed",
        subject_type="dispute",
        subject_id=dispute.pk,
        actor_type="system",
        payload={"inbound_id": record.pk, "channel": inbound.channel},
    )
    return _record_disposition(record, InboundMessage.State.FILED, "", dispute=dispute)


def _record_disposition(
    record: InboundMessage, state: str, reason: str, *, dispute: Dispute | None = None
) -> Routed:
    record.state = state
    record.state_reason = reason[:255]
    if dispute is not None:
        record.dispute = dispute
    record.save(update_fields=["state", "state_reason", "dispute"])
    return Routed(record=record, dispute=dispute)


def attribute(*, record: InboundMessage, dispute: Dispute, actor_id: str, reason: str) -> Routed:
    """A human resolving a quarantine or a review item.

    The message is appended *and* the sender is added as a verified contact, so
    the next message in the thread lands without a human. That is the whole point
    of a review queue: it should shrink as it is worked.
    """
    if not reason.strip():
        raise ValueError("Attributing a quarantined message to a case requires a reason.")

    with transaction.atomic():
        DisputeContact.objects.get_or_create(
            tenant=dispute.tenant,
            dispute=dispute,
            channel=record.channel,
            identity_hash=record.from_identity_hash,
            defaults={"verified_at": timezone.now()},
        )
        service.add_message(
            dispute=dispute,
            body=record.body,
            author_type=DisputeMessage.AuthorType.CUSTOMER,
            visibility=DisputeMessage.Visibility.CUSTOMER,
        )
        audit.append(
            tenant=dispute.tenant,
            event_type="intake.attributed",
            subject_type="dispute",
            subject_id=dispute.pk,
            actor_type="user",
            actor_id=actor_id,
            payload={"inbound_id": record.pk, "channel": record.channel, "reason": reason},
        )
        return _record_disposition(record, InboundMessage.State.MATCHED, reason, dispute=dispute)


CHANNELS_ACCEPTING_INBOUND = tuple(c for c in Channel.values if c != Channel.WIDGET)
