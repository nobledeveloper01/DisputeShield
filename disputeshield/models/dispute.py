from __future__ import annotations

import hashlib
import hmac

from django.db import models

from disputeshield.identifiers import dispute_id, idempotency_record_id, message_id
from disputeshield.tenancy.managers import (
    TenantScopedManager,
    TenantScopedModel,
    TenantScopedQuerySet,
)


def hash_customer_ref(tenant, customer_ref: str) -> str:
    """§8.4 — the customer's own identifier is pseudonymised before storage.

    HMAC with a per-tenant salt rather than a bare digest. A bare SHA-256 of a
    short identifier space (`usr_9931`) is trivially reversed by enumeration, and
    a shared salt would let one tenant's leaked table be used to enumerate
    another's.
    """
    return hmac.new(
        tenant.customer_ref_salt.encode(), customer_ref.encode(), hashlib.sha256
    ).hexdigest()


class Status(models.TextChoices):
    """§3.4. The values are stored in the audit trail, so they never change."""

    SUBMITTED = "submitted", "Submitted"
    ACKNOWLEDGED = "acknowledged", "Acknowledged"
    INVESTIGATING = "investigating", "Investigating"
    AWAITING_CUSTOMER = "awaiting_customer", "Awaiting customer"
    ESCALATED = "escalated", "Escalated"
    RESOLVED = "resolved", "Resolved"
    REOPENED = "reopened", "Reopened"
    CLOSED = "closed", "Closed"
    AUTO_CLOSED = "auto_closed", "Auto-closed"


class Outcome(models.TextChoices):
    UPHELD = "upheld", "Upheld"
    REJECTED = "rejected", "Rejected"
    PARTIAL = "partial", "Partially upheld"
    WITHDRAWN = "withdrawn", "Withdrawn"


class DisputeQuerySet(TenantScopedQuerySet):
    def open(self) -> DisputeQuerySet:
        return self.exclude(status__in=[Status.CLOSED, Status.AUTO_CLOSED])

    def by_sla_urgency(self) -> DisputeQuerySet:
        """The default queue order (§3.2 B1): most at risk first.

        Breached cases pin to the top and stay visually distinct; everything else
        sorts by how much time is left. An agent who has to sort a queue to find
        urgent work is using a table, not a queue.
        """
        return self.order_by("-breach_resolution", "-breach_ack", "resolution_deadline", "id")


class DisputeManager(TenantScopedManager.from_queryset(DisputeQuerySet)):
    pass


class Dispute(TenantScopedModel):
    """A complaint, and everything the regulator will ask about it.

    `on_delete=PROTECT` throughout (ADR-0006): on a system whose product is
    evidence, a cascade means one mistaken deletion destroys the seven-year record
    and takes the audit trail that would have recorded it down in the same
    transaction.
    """

    id = models.CharField(primary_key=True, max_length=32, default=dispute_id, editable=False)

    # Human-facing, quoted to the customer, unique within the tenant.
    reference = models.CharField(max_length=32)

    # §8.4: only the hash and an optional display name. The fintech decides what
    # context to supply, and can supply none.
    customer_ref_hash = models.CharField(max_length=64, db_index=True)
    customer_display_name = models.CharField(max_length=128, blank=True)

    category = models.CharField(max_length=64, db_index=True)
    subcategory = models.CharField(max_length=64, blank=True)
    description = models.TextField()

    transaction_ref = models.CharField(max_length=128, blank=True, db_index=True)
    amount_minor = models.BigIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)

    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.SUBMITTED, db_index=True
    )
    priority = models.CharField(max_length=16, default="normal")
    # SET_NULL rather than PROTECT (ADR-0006): an agent leaving must not make
    # their past cases undeletable, and must not take the cases with them either.
    # The action records keep naming the agent id; the assignment simply empties.
    assigned_to = models.ForeignKey(
        "disputeshield.Agent",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_disputes",
    )

    policy_version = models.ForeignKey(
        "disputeshield.SLAPolicyVersion", on_delete=models.PROTECT, related_name="+"
    )
    clock = models.OneToOneField(
        "disputeshield.SLAClock", on_delete=models.PROTECT, related_name="dispute"
    )

    submitted_at = models.DateTimeField()
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    # Denormalised from SLADeadline so the queue can sort without a join per row.
    # The deadline rows remain authoritative; nightly reconciliation checks both.
    ack_deadline = models.DateTimeField(db_index=True)
    resolution_deadline = models.DateTimeField(db_index=True)

    breach_ack = models.BooleanField(default=False)
    breach_resolution = models.BooleanField(default=False)
    breach_reason = models.TextField(blank=True)

    outcome = models.CharField(max_length=32, choices=Outcome.choices, blank=True)
    outcome_notes = models.TextField(blank=True)
    # Recorded, never executed. §3.3 puts moving money under permanent Won't, and
    # phase 9 adds a call-graph gate asserting nothing reaches a payment path.
    refund_amount_minor = models.BigIntegerField(null=True, blank=True)

    objects = DisputeManager()

    class Meta:
        db_table = "disputeshield_dispute"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "reference"], name="uniq_dispute_reference_per_tenant"
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "status", "resolution_deadline"]),
            models.Index(fields=["tenant", "assigned_to", "status"]),
            models.Index(fields=["tenant", "customer_ref_hash", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.reference} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status not in {Status.CLOSED, Status.AUTO_CLOSED}


class DisputeMessageQuerySet(TenantScopedQuerySet):
    def customer_visible(self) -> DisputeMessageQuerySet:
        return self.filter(visibility=DisputeMessage.Visibility.CUSTOMER)


class DisputeMessageManager(TenantScopedManager.from_queryset(DisputeMessageQuerySet)):
    pass


class DisputeMessage(TenantScopedModel):
    """A message on a case. Immutable by construction.

    There is no `updated_at` and no edit path, here or anywhere above this model.
    A correction is a new message, never a rewrite of an old one — the same
    discipline as the audit trail, for the same reason: a conversation that can be
    edited after the fact is not evidence of what was said.
    """

    class Visibility(models.TextChoices):
        CUSTOMER = "customer", "Visible to the customer"
        INTERNAL = "internal", "Internal only"

    class AuthorType(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        AGENT = "agent", "Agent"
        SYSTEM = "system", "System"

    id = models.CharField(primary_key=True, max_length=32, default=message_id, editable=False)
    dispute = models.ForeignKey(Dispute, related_name="messages", on_delete=models.PROTECT)

    author_type = models.CharField(max_length=16, choices=AuthorType.choices)
    author_id = models.CharField(max_length=64, blank=True)
    visibility = models.CharField(max_length=16, choices=Visibility.choices)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = DisputeMessageManager()

    class Meta:
        db_table = "disputeshield_disputemessage"
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["tenant", "dispute", "visibility"])]

    def __str__(self) -> str:
        return f"{self.author_type} message on {self.dispute_id}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError(
                "Messages are immutable. A correction is a new message, never a "
                "rewrite of an old one."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Messages cannot be deleted.")


class IdempotencyRecord(TenantScopedModel):
    """§8.6 principle 4 — every write endpoint replays its original result.

    Stored rather than cached: a replay after the cache expires must still return
    the original answer, and "we returned a different result because it had been a
    while" is not an answer a payments team accepts.
    """

    id = models.CharField(
        primary_key=True, max_length=32, default=idempotency_record_id, editable=False
    )
    key = models.CharField(max_length=128)
    endpoint = models.CharField(max_length=128)
    request_fingerprint = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField()
    response_body = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_idempotencyrecord"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "key"], name="uniq_idempotency_key_per_tenant"
            )
        ]

    def __str__(self) -> str:
        return f"{self.endpoint} [{self.key}]"
