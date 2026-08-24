from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from disputeshield.identifiers import escalation_id
from disputeshield.tenancy.managers import TenantScopedModel


class ExternalEscalation(TenantScopedModel):
    """A complaint that has gone past the firm (amplifier A6).

    The highest-stakes case in the system and the one most likely to be handled
    in somebody's personal inbox. The state the product must prevent is the
    internal case being closed while the external one is live — that is precisely
    the shape that produces "the firm was unresponsive" in a supervisory finding.

    So an open external track blocks closure in the state machine, not in a
    convention, and `tests/test_escalation.py` asserts it against every transition
    rather than the obvious one.
    """

    class Body(models.TextChoices):
        REGULATOR = "regulator", "Regulator's consumer protection desk"
        OMBUDSMAN = "ombudsman", "Ombudsman"
        COURT = "court", "Court"
        SCHEME = "scheme", "Card scheme"

    class Determination(models.TextChoices):
        UPHELD = "upheld", "Upheld against the firm"
        REJECTED = "rejected", "Rejected"
        PARTIAL = "partial", "Partially upheld"
        WITHDRAWN = "withdrawn", "Withdrawn"

    id = models.CharField(primary_key=True, max_length=32, default=escalation_id, editable=False)
    dispute = models.ForeignKey(
        "disputeshield.Dispute", related_name="escalations", on_delete=models.PROTECT
    )

    body = models.CharField(max_length=16, choices=Body.choices)
    external_reference = models.CharField(max_length=128)
    opened_at = models.DateTimeField()
    # That body's own clock, which runs concurrently with ours and by its own
    # rules. Tracked separately for the same reason the scheme clock is in A5:
    # one can expire while the other is comfortable.
    response_due_at = models.DateTimeField(null=True, blank=True)

    closed_at = models.DateTimeField(null=True, blank=True)
    determination = models.CharField(max_length=16, choices=Determination.choices, blank=True)
    determination_notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "disputeshield_externalescalation"
        ordering = ["-opened_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "body", "external_reference"],
                name="uniq_escalation_reference_per_body",
            )
        ]
        indexes = [models.Index(fields=["tenant", "closed_at", "response_due_at"])]

    def __str__(self) -> str:
        return f"{self.body} {self.external_reference}"

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    def clean(self) -> None:
        if self.closed_at and not self.determination:
            raise ValidationError(
                "Closing an external track requires the body's determination. Closing "
                "one without recording what they decided loses the only part of it "
                "that matters later."
            )


class ExternalCorrespondence(TenantScopedModel):
    """A letter in or out on the external track.

    Kept on the case rather than in an inbox, because §11.7's evidence obligation
    does not stop at the firm's own boundary, and "it was in his email" is not a
    record.
    """

    class Direction(models.TextChoices):
        INBOUND = "inbound", "From the body"
        OUTBOUND = "outbound", "To the body"

    id = models.CharField(primary_key=True, max_length=32, default=escalation_id, editable=False)
    escalation = models.ForeignKey(
        ExternalEscalation, related_name="correspondence", on_delete=models.PROTECT
    )
    direction = models.CharField(max_length=16, choices=Direction.choices)
    occurred_at = models.DateTimeField()
    summary = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    recorded_by = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_externalcorrespondence"
        ordering = ["occurred_at", "id"]

    def __str__(self) -> str:
        return f"{self.direction}: {self.summary}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError(
                "Correspondence records what was sent or received. A correction is a "
                "new entry, never a rewrite."
            )
        return super().save(*args, **kwargs)
