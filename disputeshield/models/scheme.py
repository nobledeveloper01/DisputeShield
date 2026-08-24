from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from disputeshield.identifiers import representment_id
from disputeshield.tenancy.managers import TenantScopedModel


class ReasonCode(TenantScopedModel):
    """A card scheme's reason code and what it requires as evidence (amplifier A5).

    Data, not code. Schemes revise their codes and their evidence requirements on
    their own schedule, and a mapping compiled into the application is a mapping
    that goes stale between releases — at which point a representment is refused
    for a missing element nobody knew had been added.
    """

    class Scheme(models.TextChoices):
        VISA = "visa", "Visa"
        MASTERCARD = "mastercard", "Mastercard"
        VERVE = "verve", "Verve"

    id = models.CharField(primary_key=True, max_length=32, default=representment_id, editable=False)
    scheme = models.CharField(max_length=16, choices=Scheme.choices)
    code = models.CharField(max_length=16)
    title = models.CharField(max_length=255)

    # Ordered list of {key, label, required}. The checklist an agent works, and
    # the thing a pack is validated against before it is exported.
    evidence_requirements = models.JSONField(default=list, blank=True)
    # The scheme's own window, in days from the chargeback date. Wall-clock: a
    # scheme does not observe the firm's business hours or its pauses.
    response_window_days = models.PositiveSmallIntegerField(default=30)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_reasoncode"
        ordering = ["scheme", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "scheme", "code"], name="uniq_reason_code_per_scheme"
            )
        ]

    def __str__(self) -> str:
        return f"{self.scheme} {self.code}"

    @property
    def required_keys(self) -> tuple[str, ...]:
        return tuple(
            item["key"] for item in self.evidence_requirements if item.get("required", True)
        )


class Representment(TenantScopedModel):
    """A card dispute's second clock, and the pack that answers it.

    DisputeShield builds and exports the pack. It does not submit it, and it never
    represents itself as having submitted it — submission is the acquirer's
    channel and the fintech's decision. `submitted_at` records what the fintech
    told us they did, and the field name says so.
    """

    class Status(models.TextChoices):
        GATHERING = "gathering", "Gathering evidence"
        READY = "ready", "Ready to export"
        EXPORTED = "exported", "Pack exported"
        RECORDED_SUBMITTED = "recorded_submitted", "Recorded as submitted by the fintech"
        ACCEPTED = "accepted", "Accepted by the scheme"
        DECLINED = "declined", "Declined by the scheme"
        EXPIRED = "expired", "Window closed without a response"

    id = models.CharField(primary_key=True, max_length=32, default=representment_id, editable=False)
    dispute = models.OneToOneField(
        "disputeshield.Dispute", related_name="representment", on_delete=models.PROTECT
    )
    reason_code = models.ForeignKey(ReasonCode, on_delete=models.PROTECT, related_name="+")

    chargeback_reference = models.CharField(max_length=128)
    chargeback_at = models.DateTimeField()
    # The scheme's deadline. Independent of the regulatory one by construction:
    # a separate field, computed on wall-clock time, and never moved by a pause.
    respond_by = models.DateTimeField(db_index=True)

    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.GATHERING, db_index=True
    )
    evidence = models.JSONField(default=dict, blank=True)

    exported_at = models.DateTimeField(null=True, blank=True)
    # What the fintech told us they did. We never submit.
    submitted_at = models.DateTimeField(null=True, blank=True)
    outcome_at = models.DateTimeField(null=True, blank=True)
    outcome_detail = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "disputeshield_representment"
        ordering = ["respond_by"]
        indexes = [models.Index(fields=["tenant", "status", "respond_by"])]

    def __str__(self) -> str:
        return f"representment {self.chargeback_reference} ({self.status})"

    @property
    def missing_evidence(self) -> tuple[str, ...]:
        return tuple(key for key in self.reason_code.required_keys if not self.evidence.get(key))

    @property
    def is_complete(self) -> bool:
        return not self.missing_evidence

    def clean(self) -> None:
        if self.respond_by <= self.chargeback_at:
            raise ValidationError("The scheme window must end after the chargeback date.")
