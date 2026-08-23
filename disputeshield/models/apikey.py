from __future__ import annotations

from django.db import models
from django.utils import timezone

from disputeshield.identifiers import api_key_id


class APIKey(models.Model):
    """§8.2. An API key resolves to exactly one tenant. No key spans tenants. Ever.

    Not a TenantScopedModel: authentication has to find the key *before* a tenant
    context exists, so scoping the lookup by tenant would be circular. The
    isolation guarantee here comes from the key resolving to one tenant and the
    authenticator establishing that tenant from the key — layer 1 of §8.1, which
    is what layers 2 and 3 then depend on.
    """

    class Environment(models.TextChoices):
        TEST = "test", "Test"
        LIVE = "live", "Live"

    id = models.CharField(primary_key=True, max_length=32, default=api_key_id, editable=False)
    tenant = models.ForeignKey(
        "disputeshield.Tenant", on_delete=models.PROTECT, related_name="api_keys"
    )
    name = models.CharField(max_length=128)
    environment = models.CharField(max_length=8, choices=Environment.choices)

    # Plaintext, for lookup and for showing the user which key this is.
    prefix = models.CharField(max_length=16, db_index=True)
    # Argon2id. Shown once at creation, never retrievable (§8.2).
    key_hash = models.CharField(max_length=255)

    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    # Rotation uses overlapping validity: a new key is issued and both work until
    # the old one is explicitly revoked, so rotation never causes downtime (§8.2).
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "disputeshield_apikey"
        indexes = [models.Index(fields=["prefix", "revoked_at"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix}…, {self.environment})"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def revoke(self) -> None:
        self.revoked_at = timezone.now()
        self.save(update_fields=["revoked_at"])
