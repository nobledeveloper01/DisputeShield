from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from disputeshield.identifiers import regulatory_return_id, return_template_id
from disputeshield.tenancy.managers import TenantScopedModel


class ReturnTemplate(TenantScopedModel):
    """A supervisor's periodic return, as data rather than as code (amplifier A17).

    §6.5's regulator-ready export answers an ad-hoc request. A *return* is the
    recurring obligation: the same shape, every month or quarter, assembled by
    hand under deadline. This converts a scheduled fire drill into a
    review-and-approve.

    Data-driven and versioned because templates are jurisdiction-specific and
    change. A revision must never alter what was filed last year — a return
    regenerated under this year's template would silently disagree with the
    document the supervisor holds, which is worse than not being able to
    regenerate it at all.
    """

    class Period(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        ANNUAL = "annual", "Annual"

    id = models.CharField(
        primary_key=True, max_length=32, default=return_template_id, editable=False
    )
    code = models.CharField(max_length=64, help_text="e.g. cbn-consumer-complaints")
    version = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    jurisdiction = models.CharField(max_length=64, blank=True)
    period = models.CharField(max_length=16, choices=Period.choices, default=Period.MONTHLY)
    regulatory_reference = models.CharField(max_length=255, blank=True)

    # The whole template: an ordered list of {key, label, source, filter}.
    # Interpreted by `disputeshield/reports/returns.py`, which supports a closed
    # set of sources — a template is a specification of what to count, never a
    # query somebody can write into the dashboard.
    rows = models.JSONField(default=list)

    effective_from = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "disputeshield_returntemplate"
        ordering = ["code", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "code", "version"], name="uniq_return_template_version"
            )
        ]

    def __str__(self) -> str:
        return f"{self.code} v{self.version}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError(
                "Return templates are immutable versions. A revision is version n+1, "
                "so a return filed last year still regenerates as it was filed."
            )
        return super().save(*args, **kwargs)


class RegulatoryReturn(TenantScopedModel):
    """One filing period, generated, reviewed and approved.

    Nothing is filed automatically. A return is generated, reviewed, approved by a
    named person, and the approved artefact is hashed into the audit chain — so
    what was filed is provable later rather than merely remembered.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Generated, awaiting review"
        APPROVED = "approved", "Approved for filing"
        SUPERSEDED = "superseded", "Replaced by a later generation"

    id = models.CharField(
        primary_key=True, max_length=32, default=regulatory_return_id, editable=False
    )
    template = models.ForeignKey(ReturnTemplate, on_delete=models.PROTECT, related_name="returns")
    period_from = models.DateTimeField()
    period_to = models.DateTimeField()

    rows = models.JSONField(default=list)
    # SHA-256 of the canonical rendering. What "byte-identical" is checked against.
    content_digest = models.CharField(max_length=64)

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    generated_at = models.DateTimeField()
    generated_by = models.CharField(max_length=64, blank=True)

    approved_at = models.DateTimeField(null=True, blank=True)
    # Maker-checker: the approver may not be the generator. Enforced in the
    # service and asserted by a test.
    approved_by = models.CharField(max_length=64, blank=True)
    approval_note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_regulatoryreturn"
        ordering = ["-period_from", "-generated_at"]
        indexes = [models.Index(fields=["tenant", "status", "period_from"])]

    def __str__(self) -> str:
        return f"{self.template_id} {self.period_from:%Y-%m} ({self.status})"

    @property
    def is_approved(self) -> bool:
        return self.status == self.Status.APPROVED

    def clean(self) -> None:
        if self.approved_by and self.approved_by == self.generated_by:
            raise ValidationError(
                "A return must be approved by somebody other than whoever generated "
                "it. Maker-checker that one person can satisfy twice is not a control."
            )
