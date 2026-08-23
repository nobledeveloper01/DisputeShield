from __future__ import annotations

from django.db import models

from disputeshield.identifiers import policy_id, policy_version_id
from disputeshield.tenancy.managers import TenantScopedModel


class SLAPolicy(TenantScopedModel):
    """A named policy for a dispute category. A container; the fields live on versions."""

    id = models.CharField(primary_key=True, max_length=32, default=policy_id, editable=False)
    category = models.CharField(max_length=64)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_slapolicy"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "category"], name="uniq_policy_per_category")
        ]

    def __str__(self) -> str:
        return f"SLA policy for {self.category}"

    @property
    def current_version(self) -> SLAPolicyVersion | None:
        return self.versions.order_by("-version").first()


class SLAPolicyVersion(TenantScopedModel):
    """An immutable snapshot of a policy's terms (ADR-0004).

    Editing a policy creates version n+1. A dispute pins the version in force when
    it was filed, so the standard a case was judged against is still recoverable
    years later — which is the whole question a supervisor asks about a breach.
    """

    id = models.CharField(
        primary_key=True, max_length=32, default=policy_version_id, editable=False
    )
    policy = models.ForeignKey(SLAPolicy, on_delete=models.PROTECT, related_name="versions")
    version = models.PositiveIntegerField()

    acknowledgement_minutes = models.PositiveIntegerField(default=60)
    resolution_hours = models.PositiveIntegerField(default=72)
    business_hours_only = models.BooleanField(default=True)
    calendar = models.ForeignKey(
        "disputeshield.BusinessCalendar", on_delete=models.PROTECT, related_name="+"
    )

    warning_thresholds = models.JSONField(default=list)  # [50, 80, 95]
    escalate_at_percent = models.PositiveIntegerField(default=80)

    # D11 — the two periods §3.4's state machine implies but never sizes.
    auto_close_after_hours = models.PositiveIntegerField(default=168)
    reopen_window_hours = models.PositiveIntegerField(default=336)

    # Turns a configuration value into documented evidence of intent, which is
    # what a supervisor actually asks about.
    regulatory_reference = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "disputeshield_slapolicyversion"
        ordering = ["policy_id", "version"]
        constraints = [
            models.UniqueConstraint(fields=["policy", "version"], name="uniq_version_per_policy")
        ]

    def __str__(self) -> str:
        return f"{self.policy.category} v{self.version}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError(
                "SLA policy versions are immutable (ADR-0004). Editing a policy creates "
                "version n+1; open cases keep the version they were filed under."
            )
        return super().save(*args, **kwargs)
