from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from disputeshield.identifiers import incident_id, mass_event_id, membership_id
from disputeshield.tenancy.managers import TenantScopedModel


class Incident(TenantScopedModel):
    """A declared, customer-facing outage (amplifier A2).

    During an outage the queue receives thousands of copies of one complaint, and
    every copy consumes an SLA clock, an acknowledgement, an agent touch and a
    resolution record. Telling the customer the truth — *"we know, reversals are
    running, expected by 18:00"* — is both a better experience and the only
    feature in the product that reduces load rather than adding it.

    The danger is equally plain: deflection that is wrong is complaint
    suppression, and complaint suppression is the worst accusation a regulator
    can make about a complaints system. Hence `file_anyway_enabled`, which is
    forced to True at every layer and cannot be configured away.
    """

    class Status(models.TextChoices):
        DECLARED = "declared", "Declared"
        MITIGATING = "mitigating", "Mitigating"
        RESOLVED = "resolved", "Resolved"

    id = models.CharField(primary_key=True, max_length=32, default=incident_id, editable=False)
    title = models.CharField(max_length=128)
    # Shown verbatim to customers. Written by a human, reviewed like a public
    # statement, because that is what it is.
    customer_message = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DECLARED)

    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    expected_resolution_at = models.DateTimeField(null=True, blank=True)

    # What this incident covers: categories, transaction reference prefixes, a
    # provider. Deliberately narrow matching — a broad matcher deflects
    # complaints that have nothing to do with the outage.
    match_categories = models.JSONField(default=list)
    match_transaction_prefixes = models.JSONField(default=list)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "disputeshield_incident"
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["tenant", "status", "started_at"])]

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"

    @property
    def is_live(self) -> bool:
        return self.status != self.Status.RESOLVED and self.ended_at is None

    def matches(self, *, category: str = "", transaction_ref: str = "") -> bool:
        if not self.is_live:
            return False
        if self.match_categories and category not in self.match_categories:
            return False
        if self.match_transaction_prefixes:
            return any(
                transaction_ref.startswith(prefix)
                for prefix in self.match_transaction_prefixes
                if prefix
            )
        return bool(self.match_categories)


class IncidentSubscription(TenantScopedModel):
    """A customer who chose "notify me" instead of filing.

    Counted next to case volume. §11.2's `deflections_total` exists so that a
    drop in complaints during an outage is visibly a deflection rather than
    silently a suppression.
    """

    id = models.CharField(primary_key=True, max_length=32, default=membership_id, editable=False)
    incident = models.ForeignKey(Incident, related_name="subscriptions", on_delete=models.PROTECT)
    customer_ref_hash = models.CharField(max_length=64, db_index=True)
    transaction_ref = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "disputeshield_incidentsubscription"
        constraints = [
            models.UniqueConstraint(
                fields=["incident", "customer_ref_hash", "transaction_ref"],
                name="uniq_subscription_per_customer",
            )
        ]

    def __str__(self) -> str:
        return f"subscription to {self.incident_id}"


class MassEvent(TenantScopedModel):
    """One root cause, many cases (amplifier A3).

    The difference between a system that survives a bad day and one that
    collapses on it. Four thousand cases about a single failed rail is one
    investigation and four thousand records — and without this an agent either
    resolves four thousand cases by hand or resolves them dishonestly with a
    copy-paste that says nothing specific.
    """

    class Status(models.TextChoices):
        INVESTIGATING = "investigating", "Investigating"
        APPLIED = "applied", "Outcome applied"

    id = models.CharField(primary_key=True, max_length=32, default=mass_event_id, editable=False)
    title = models.CharField(max_length=128)
    root_cause = models.TextField(blank=True)
    finding = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.INVESTIGATING)

    applied_at = models.DateTimeField(null=True, blank=True)
    applied_by = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "disputeshield_massevent"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"


class MassEventMembership(TenantScopedModel):
    """One case's membership of a mass event.

    Per case, individually recorded, individually reversible. A case removed from
    an event keeps everything that happened to it while it was a member — the
    membership is closed with `removed_at` rather than deleted, because the fact
    that a case was once grouped with four thousand others is part of how it was
    handled.
    """

    id = models.CharField(primary_key=True, max_length=32, default=membership_id, editable=False)
    mass_event = models.ForeignKey(MassEvent, related_name="memberships", on_delete=models.PROTECT)
    dispute = models.ForeignKey(
        "disputeshield.Dispute", related_name="mass_memberships", on_delete=models.PROTECT
    )

    added_at = models.DateTimeField(auto_now_add=True)
    added_by = models.CharField(max_length=64, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.CharField(max_length=64, blank=True)
    removal_reason = models.CharField(max_length=255, blank=True)

    outcome_applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "disputeshield_masseventmembership"
        constraints = [
            models.UniqueConstraint(
                fields=["mass_event", "dispute"], name="uniq_membership_per_case"
            )
        ]
        indexes = [models.Index(fields=["tenant", "mass_event", "removed_at"])]

    def __str__(self) -> str:
        return f"{self.dispute_id} in {self.mass_event_id}"

    @property
    def is_active(self) -> bool:
        return self.removed_at is None

    def clean(self) -> None:
        if self.removed_at and not self.removal_reason:
            raise ValidationError("Removing a case from a mass event requires a reason.")
