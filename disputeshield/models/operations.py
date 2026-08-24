from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from disputeshield.identifiers import (
    qa_review_id,
    simulation_id,
    webhook_delivery_id,
    webhook_endpoint_id,
)
from disputeshield.tenancy.managers import TenantScopedModel


class PolicySimulation(TenantScopedModel):
    """What a proposed SLA change would have done to real cases (amplifier A9).

    §6.5 lets a compliance officer change an SLA policy without a deploy —
    correctly, because filing an engineering ticket to fix a regulatory window is
    absurd. But a policy change is a change to a *control*, and the specification
    gives its author no way to see the consequence before committing to it.

    This is the counterpart to a diff on a limit: make the magnitude of the change
    loud at the moment it is made.

    Stored with the policy version it evaluated, so the change record shows what
    the author was told at the time rather than what the same simulation would say
    today.
    """

    id = models.CharField(primary_key=True, max_length=32, default=simulation_id, editable=False)
    policy_version = models.ForeignKey(
        "disputeshield.SLAPolicyVersion", on_delete=models.PROTECT, related_name="simulations"
    )
    proposed = models.JSONField(default=dict, blank=True)

    period_from = models.DateTimeField()
    period_to = models.DateTimeField()

    cases_examined = models.PositiveIntegerField(default=0)
    actual_breaches = models.PositiveIntegerField(default=0)
    projected_breaches = models.PositiveIntegerField(default=0)
    by_category = models.JSONField(default=dict, blank=True)
    by_agent = models.JSONField(default=dict, blank=True)

    ran_at = models.DateTimeField()
    ran_by = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_policysimulation"
        ordering = ["-ran_at"]
        indexes = [models.Index(fields=["tenant", "policy_version", "ran_at"])]

    def __str__(self) -> str:
        return f"simulation over {self.cases_examined} cases"

    @property
    def delta(self) -> int:
        """Projected minus actual. The number the author is being shown."""
        return self.projected_breaches - self.actual_breaches


class QaReview(TenantScopedModel):
    """A supervisor's review of how a case was handled (amplifier A15).

    §3.2 gives the compliance officer breach counts, which measure whether cases
    were closed *in time*. Nothing measures whether they were closed *well*. A
    firm that resolves every case within its window by consistently rejecting
    valid complaints has perfect SLA metrics and a serious problem.

    A score is a record about the **review**, not about the case: it attaches
    without altering the case's history, and the agent can see and respond to
    every score about their own work.
    """

    class Trigger(models.TextChoices):
        SAMPLED = "sampled", "Randomly sampled"
        FORCED = "forced", "Met a forced-review criterion"

    id = models.CharField(primary_key=True, max_length=32, default=qa_review_id, editable=False)
    dispute = models.ForeignKey(
        "disputeshield.Dispute", related_name="qa_reviews", on_delete=models.PROTECT
    )
    agent_id = models.CharField(max_length=64, blank=True, db_index=True)

    trigger = models.CharField(max_length=16, choices=Trigger.choices)
    forced_reason = models.CharField(max_length=128, blank=True)

    # rubric key -> score. The rubric itself is configuration; the scores are the
    # record.
    scores = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)

    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.CharField(max_length=64, blank=True)
    # An agent can see and respond to every score about their own work.
    agent_response = models.TextField(blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_qareview"
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["dispute"], name="uniq_qa_review_per_case")]
        indexes = [models.Index(fields=["tenant", "agent_id", "reviewed_at"])]

    def __str__(self) -> str:
        return f"QA review of {self.dispute_id} ({self.trigger})"

    @property
    def average(self) -> float | None:
        values = [v for v in self.scores.values() if isinstance(v, int | float)]
        return round(sum(values) / len(values), 2) if values else None


class WebhookEndpoint(TenantScopedModel):
    """Where a tenant wants events delivered (amplifier A14)."""

    id = models.CharField(
        primary_key=True, max_length=32, default=webhook_endpoint_id, editable=False
    )
    url = models.URLField()
    description = models.CharField(max_length=128, blank=True)
    event_types = models.JSONField(default=list, blank=True)

    # Used for the `X-DisputeShield-Signature` HMAC. Deliberately the Stripe
    # scheme (§8.2): well documented, widely understood, and not a novel
    # cryptographic design.
    signing_secret = models.CharField(max_length=128)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "disputeshield_webhookendpoint"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "url"], name="uniq_webhook_url_per_tenant")
        ]

    def __str__(self) -> str:
        return self.url

    def wants(self, event_type: str) -> bool:
        return not self.event_types or event_type in self.event_types

    def clean(self) -> None:
        if self.url.startswith("http://") and not self.url.startswith("http://localhost"):
            raise ValidationError(
                "Webhook endpoints must be https. A signed payload over plaintext is "
                "a payload anyone on the path can read."
            )


class WebhookDelivery(TenantScopedModel):
    """One event, one endpoint, and every attempt to get it there.

    Ordered per dispute and parked rather than dropped. A `dispute.resolved`
    arriving before its `dispute.acknowledged` would have the fintech's ledger
    reacting to a case it has not heard of, and an event silently discarded after
    a customer's outage is a reconciliation gap nobody can explain later.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DELIVERED = "delivered", "Delivered"
        PARKED = "parked", "Parked after exhausting retries"

    id = models.CharField(
        primary_key=True, max_length=32, default=webhook_delivery_id, editable=False
    )
    endpoint = models.ForeignKey(
        WebhookEndpoint, on_delete=models.PROTECT, related_name="deliveries"
    )

    event_type = models.CharField(max_length=64, db_index=True)
    # Deterministic, derived from what the event is about. A consumer that
    # de-duplicates on this cannot be made to double-process by our retries.
    idempotency_key = models.CharField(max_length=128)
    # Per-dispute ordering key. Deliveries for one case are attempted in this
    # order and a later one waits for an earlier failure.
    sequence_key = models.CharField(max_length=64, db_index=True)
    sequence = models.BigIntegerField(default=0)

    payload = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "disputeshield_webhookdelivery"
        ordering = ["sequence_key", "sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "endpoint", "idempotency_key"],
                name="uniq_delivery_per_endpoint_event",
            )
        ]
        indexes = [
            models.Index(
                fields=["next_attempt_at"],
                condition=models.Q(status="pending"),
                name="idx_pending_deliveries",
            ),
            models.Index(fields=["tenant", "sequence_key", "sequence"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} -> {self.endpoint_id} ({self.status})"
