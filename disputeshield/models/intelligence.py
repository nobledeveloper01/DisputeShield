from __future__ import annotations

from django.db import models

from disputeshield.identifiers import cluster_id, signal_id, suggestion_id
from disputeshield.tenancy.managers import TenantScopedModel


class Suggestion(TenantScopedModel):
    """What a model proposed, and what the human did about it.

    **A separate model on purpose.** §3.3 lists priority prediction under
    **Won't**, and that exclusion is right for anything that acts on its own. The
    way it would quietly be reversed is a model writing a field on `Dispute` —
    so suggestions live here, with no path to case fields, and
    `tests/test_advisory_only.py` asserts that by introspecting the write path.

    Every row names the model and its version. A shift in behaviour six months
    from now has to be attributable to a specific model, or the accuracy number
    below means nothing.
    """

    class Kind(models.TextChoices):
        CATEGORY = "category", "Category"
        SUBCATEGORY = "subcategory", "Subcategory"
        PRIORITY = "priority", "Priority"
        ASSIGNEE = "assignee", "Assignee"
        REPLY_DRAFT = "reply_draft", "Reply draft"

    class Disposition(models.TextChoices):
        PENDING = "pending", "Not yet decided"
        ACCEPTED = "accepted", "Accepted as proposed"
        OVERRIDDEN = "overridden", "Human chose something else"
        DISCARDED = "discarded", "Discarded"

    id = models.CharField(primary_key=True, max_length=32, default=suggestion_id, editable=False)
    dispute = models.ForeignKey(
        "disputeshield.Dispute", related_name="suggestions", on_delete=models.PROTECT
    )

    kind = models.CharField(max_length=16, choices=Kind.choices, db_index=True)
    value = models.TextField()
    confidence = models.FloatField(null=True, blank=True)
    rationale = models.TextField(blank=True)

    # Attribution. Without these the accuracy metric is a number about nothing.
    model_id = models.CharField(max_length=64)
    model_version = models.CharField(max_length=32)

    disposition = models.CharField(
        max_length=16, choices=Disposition.choices, default=Disposition.PENDING, db_index=True
    )
    # What the human actually chose. The difference between this and `value` is
    # the training signal and the accuracy metric, and it only exists because the
    # suggestion never became the case's own field.
    chosen_value = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_suggestion"
        ordering = ["-created_at", "id"]
        indexes = [
            models.Index(fields=["tenant", "kind", "disposition"]),
            models.Index(fields=["tenant", "model_id", "model_version"]),
        ]

    def __str__(self) -> str:
        return f"{self.kind} suggestion ({self.disposition})"

    @property
    def was_correct(self) -> bool | None:
        if self.disposition == self.Disposition.ACCEPTED:
            return True
        if self.disposition == self.Disposition.OVERRIDDEN:
            return False
        return None


class RootCauseCluster(TenantScopedModel):
    """A hypothesis about why a group of cases exists (amplifier A10).

    §2.3 identifies the compounding effect: every unhandled transaction failure
    becomes a dispute, and handling disputes well treats symptoms forever. A
    cluster is what lets a Head of Compliance walk into an engineering planning
    session with a case count and a financial exposure attached to a cause.

    A cluster is a **hypothesis**, and hypotheses presented with the confidence of
    facts get acted on wrongly. So membership and evidence are inspectable down to
    individual cases, and clustering never modifies one.
    """

    id = models.CharField(primary_key=True, max_length=32, default=cluster_id, editable=False)
    label = models.CharField(max_length=255)
    basis = models.CharField(max_length=64)
    evidence = models.JSONField(default=dict, blank=True)

    case_count = models.PositiveIntegerField(default=0)
    exposure_minor = models.BigIntegerField(default=0)
    first_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    computed_at = models.DateTimeField()
    model_id = models.CharField(max_length=64, blank=True)
    model_version = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_rootcausecluster"
        ordering = ["-case_count", "label"]
        indexes = [models.Index(fields=["tenant", "computed_at"])]

    def __str__(self) -> str:
        return f"{self.label} ({self.case_count} cases)"


class RiskSignal(TenantScopedModel):
    """Context for an agent, and structurally nothing more (amplifier A13).

    The amplifier with the most serious failure mode in the product. A signal that
    influences an outcome turns a complaints system into an automated denial
    system — a consumer-protection violation with the audit trail helpfully
    documenting it.

    So a signal is visible to an agent with its evidence attached, and is
    incapable of reaching an SLA policy, a priority, a channel gate or an outcome.
    `tests/test_advisory_only.py` asserts that from the call graph.
    """

    class Kind(models.TextChoices):
        REPEAT_CLAIMANT = "repeat_claimant", "Unusual claim frequency for this customer"
        RAPID_FILING = "rapid_filing", "Filed unusually soon after delivery"
        CROSS_PROVIDER = "cross_provider", "Non-receipt claimed across many providers"

    id = models.CharField(primary_key=True, max_length=32, default=signal_id, editable=False)
    dispute = models.ForeignKey(
        "disputeshield.Dispute", related_name="risk_signals", on_delete=models.PROTECT
    )
    kind = models.CharField(max_length=24, choices=Kind.choices)
    # Shown to the agent alongside the signal. A signal without its evidence is
    # an accusation.
    evidence = models.JSONField(default=dict, blank=True)
    summary = models.CharField(max_length=255)

    model_id = models.CharField(max_length=64, blank=True)
    model_version = models.CharField(max_length=32, blank=True)
    computed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_risksignal"
        ordering = ["-computed_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["dispute", "kind"], name="uniq_risk_signal_per_kind")
        ]

    def __str__(self) -> str:
        return f"{self.kind} on {self.dispute_id}"
