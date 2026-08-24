from __future__ import annotations

from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from disputeshield.identifiers import erasure_request_id, legal_hold_id
from disputeshield.tenancy.managers import TenantScopedModel


class LegalHold(TenantScopedModel):
    """Suspends every retention and deletion process touching what it covers (A7).

    §11.7 promises seven-year retention *and* a tested deletion procedure. The
    moment a case is in litigation those two promises point in opposite
    directions, and automated deletion of material under hold is spoliation of
    evidence. There is no correct default to fall back on, so the hold is an
    explicit object with an author, a reason and a matter reference.

    Releasing one needs a second approver. A hold that one person can quietly
    lift is not a hold — it is a note.
    """

    class Scope(models.TextChoices):
        DISPUTE = "dispute", "One case"
        CUSTOMER = "customer", "Every case for one customer"
        CATEGORY = "category", "Every case in a category"
        PERIOD = "period", "Every case filed in a period"

    id = models.CharField(primary_key=True, max_length=32, default=legal_hold_id, editable=False)
    name = models.CharField(max_length=128)
    matter_reference = models.CharField(
        max_length=128,
        help_text="The litigation, investigation or regulatory matter this serves.",
    )
    reason = models.TextField()

    scope = models.CharField(max_length=16, choices=Scope.choices)
    dispute = models.ForeignKey(
        "disputeshield.Dispute",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="legal_holds",
    )
    customer_ref_hash = models.CharField(max_length=64, blank=True, db_index=True)
    category = models.CharField(max_length=64, blank=True)
    period_from = models.DateTimeField(null=True, blank=True)
    period_to = models.DateTimeField(null=True, blank=True)

    placed_at = models.DateTimeField(auto_now_add=True)
    placed_by = models.CharField(max_length=64)

    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.CharField(max_length=64, blank=True)
    release_reason = models.TextField(blank=True)
    # The second pair of eyes. Distinct from `released_by`, enforced in the
    # service and asserted by a test — a two-person rule that one person can
    # satisfy twice is a one-person rule with extra steps.
    release_approved_by = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "disputeshield_legalhold"
        ordering = ["-placed_at"]
        indexes = [
            models.Index(fields=["tenant", "scope", "released_at"]),
            models.Index(fields=["tenant", "released_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({'released' if self.released_at else 'active'})"

    @property
    def is_active(self) -> bool:
        return self.released_at is None

    def clean(self) -> None:
        required = {
            self.Scope.DISPUTE: self.dispute_id,
            self.Scope.CUSTOMER: self.customer_ref_hash,
            self.Scope.CATEGORY: self.category,
            self.Scope.PERIOD: self.period_from and self.period_to,
        }
        if not required.get(self.scope):
            raise ValidationError(
                f"A {self.scope} hold needs the field that defines its scope. A hold "
                "that covers nothing is worse than no hold: it reads as protection."
            )

    def covers(self, dispute) -> bool:
        if not self.is_active:
            return False
        if self.scope == self.Scope.DISPUTE:
            return self.dispute_id == dispute.pk
        if self.scope == self.Scope.CUSTOMER:
            return self.customer_ref_hash == dispute.customer_ref_hash
        if self.scope == self.Scope.CATEGORY:
            return self.category == dispute.category
        if self.scope == self.Scope.PERIOD:
            return bool(
                self.period_from
                and self.period_to
                and self.period_from <= dispute.submitted_at < self.period_to
            )
        return False


class ErasureRequest(TenantScopedModel):
    """A data-subject erasure request, and what we did about it (§11.7, NDPR/GDPR).

    Refusing a request silently is its own violation, so a refusal is a recorded
    outcome rather than an absence of one. §11.7 is deliberately honest that
    deletion in an append-only system is difficult and that the procedure must
    state plainly what is deleted, what is pseudonymised and what is retained
    under a legal-obligation basis. This model is where that statement lives per
    request.
    """

    class Outcome(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        REFUSED_LEGAL_HOLD = "refused_legal_hold", "Refused — material is under legal hold"
        REFUSED_RETENTION = "refused_retention", "Refused — regulatory retention applies"

    id = models.CharField(
        primary_key=True, max_length=32, default=erasure_request_id, editable=False
    )
    customer_ref_hash = models.CharField(max_length=64, db_index=True)
    requested_at = models.DateTimeField()
    requested_via = models.CharField(max_length=64, blank=True)

    outcome = models.CharField(
        max_length=24, choices=Outcome.choices, default=Outcome.PENDING, db_index=True
    )
    # The words the requester is given. Not a status code: a data subject is
    # entitled to know why, in language they can act on.
    outcome_reason = models.TextField(blank=True)
    blocking_holds = models.JSONField(default=list)

    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_erasurerequest"
        ordering = ["-requested_at"]

    def __str__(self) -> str:
        return f"erasure request ({self.outcome})"

    @property
    def was_refused(self) -> bool:
        return self.outcome in {self.Outcome.REFUSED_LEGAL_HOLD, self.Outcome.REFUSED_RETENTION}


def now_utc() -> datetime:
    return timezone.now()
