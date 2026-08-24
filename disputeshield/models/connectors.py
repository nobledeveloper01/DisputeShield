from __future__ import annotations

from django.db import models

from disputeshield.identifiers import connector_id, provider_call_id, settlement_id
from disputeshield.tenancy.managers import TenantScopedModel


class ProviderConnector(TenantScopedModel):
    """Optional, per-tenant, per-provider, and read-only (amplifier A4).

    §7.1's strongest security claim is that DisputeShield never queries the
    fintech's database and holds no standing access to it. A connector holding a
    provider's credentials weakens that, and pretending otherwise would be
    dishonest — so connectors are opt-in, the credentials are envelope-encrypted
    with a per-tenant key, every outbound call is audited with the exact request
    made, and the interface has no write method for a caller to reach.
    """

    class Provider(models.TextChoices):
        PAYSTACK = "paystack", "Paystack"
        FLUTTERWAVE = "flutterwave", "Flutterwave"
        NIBSS = "nibss", "NIBSS"
        STRIPE = "stripe", "Stripe"
        GENERIC = "generic", "Generic REST"

    id = models.CharField(primary_key=True, max_length=32, default=connector_id, editable=False)
    provider = models.CharField(max_length=16, choices=Provider.choices)
    label = models.CharField(max_length=128, blank=True)
    base_url = models.URLField(blank=True)

    # Envelope-encrypted with a per-tenant data key (§8.4). Never returned by any
    # serializer, and never logged — the audit record of a call carries the
    # request that was made, not the credential that made it.
    credential_ciphertext = models.BinaryField()
    credential_key_ref = models.CharField(max_length=128, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "disputeshield_providerconnector"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "provider"], name="uniq_connector_per_provider"
            )
        ]

    def __str__(self) -> str:
        return f"{self.provider} connector"


class ProviderCall(TenantScopedModel):
    """Every outbound call, recorded with the exact request made.

    The point is accountability for reaching outside the boundary at all: a
    customer's security team asking "what did you ask our provider about me?"
    gets an answer from the record rather than from a log retention policy.
    """

    id = models.CharField(primary_key=True, max_length=32, default=provider_call_id, editable=False)
    connector = models.ForeignKey(ProviderConnector, on_delete=models.PROTECT, related_name="calls")
    dispute = models.ForeignKey(
        "disputeshield.Dispute",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="provider_calls",
    )

    method = models.CharField(max_length=8)
    path = models.CharField(max_length=512)
    request_summary = models.JSONField(default=dict)

    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    ok = models.BooleanField(default=False)
    error = models.CharField(max_length=255, blank=True)

    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_providercall"
        ordering = ["-occurred_at", "id"]
        indexes = [models.Index(fields=["tenant", "connector", "occurred_at"])]

    def __str__(self) -> str:
        return f"{self.method} {self.path} ({self.status_code or self.error})"


class SettlementConfirmation(TenantScopedModel):
    """What the fintech's ledger says it actually paid (amplifier A16).

    DisputeShield knows what was *promised*: `refund_amount_minor` on a resolved
    case. Only the fintech's ledger knows what was *paid*. The gap between the
    two is the interesting number, and surfacing it requires them to send this
    back — an integration, not a report.
    """

    id = models.CharField(primary_key=True, max_length=32, default=settlement_id, editable=False)
    dispute = models.ForeignKey(
        "disputeshield.Dispute", related_name="settlements", on_delete=models.PROTECT
    )
    reference = models.CharField(max_length=128)
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, blank=True)
    settled_at = models.DateTimeField()
    source = models.CharField(max_length=64, default="ledger")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_settlementconfirmation"
        ordering = ["settled_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "reference"], name="uniq_settlement_reference"
            )
        ]

    def __str__(self) -> str:
        return f"settlement {self.reference} ({self.amount_minor})"
