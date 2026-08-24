from __future__ import annotations

import secrets
import zlib

from django.core.exceptions import ValidationError
from django.db import models

from disputeshield.identifiers import tenant_id


def new_customer_ref_salt() -> str:
    return secrets.token_urlsafe(32)


class Tenant(models.Model):
    """The isolation root.

    Deliberately *not* row-level-security protected. RLS on this table would be
    circular — creating a tenant would require the session already to be scoped
    to the tenant being created — and it protects nothing worth protecting: a
    Tenant row holds a name and a status, while every row that holds case data
    hangs off it and is protected.
    """

    id = models.CharField(primary_key=True, max_length=32, default=tenant_id, editable=False)
    name = models.CharField(max_length=128)
    slug = models.SlugField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)

    class Environment(models.TextChoices):
        TEST = "test", "Test"
        LIVE = "live", "Live"

    environment = models.CharField(
        max_length=8, choices=Environment.choices, default=Environment.LIVE, db_index=True
    )

    # A20: per-tenant region pinning, with no cross-region replication of case
    # content. §2.2 lists data residency as a procurement blocker, and a region
    # recorded on the tenant is what every read path can check against.
    region = models.CharField(max_length=32, default="eu-west-1", db_index=True)

    # A19: the sandbox's clock offset, so a 72-hour SLA can be observed in a demo.
    # A dangerous capability, which is why `clean()` and `save()` below refuse it
    # for a live tenant at the model layer rather than in a view — a view-layer
    # guard is one refactor away from being bypassed.
    clock_offset_seconds = models.BigIntegerField(default=0)

    # A20: content sealing, opt-in per tenant. Sealed content can be
    # crypto-shredded; unsealed content can only be deleted, which an append-only
    # system cannot do.
    content_sealing_enabled = models.BooleanField(default=False)

    # §8.4. Per tenant, not shared: a shared salt would let one tenant's leaked
    # table be used to enumerate another's, and a bare digest of a short
    # identifier space (`usr_9931`) is reversed by enumeration in seconds.
    customer_ref_salt = models.CharField(
        max_length=64, default=new_customer_ref_salt, editable=False
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_tenant"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"

    def save(self, *args, **kwargs):
        # Enforced on `save`, not only in `clean()`: the admin, a fixture, a data
        # migration and a management command all reach the model without a form
        # in between.
        if self.is_live and self.clock_offset_seconds:
            raise ValidationError(
                "A live tenant cannot carry a clock offset (§A19). Set environment="
                "'test' on a sandbox tenant instead."
            )
        return super().save(*args, **kwargs)

    def clean(self) -> None:
        if self.is_live and self.clock_offset_seconds:
            raise ValidationError(
                "A live tenant cannot carry a clock offset. Moving the clock on live "
                "data would move a regulatory deadline, and a breach computed against "
                "a shifted clock is a breach nobody can explain."
            )

    @property
    def is_live(self) -> bool:
        return self.environment == self.Environment.LIVE

    @property
    def lock_key(self) -> int:
        """A stable 31-bit key for pg_advisory_xact_lock (ADR-0003).

        CRC32 of the identifier, masked to fit a signed 32-bit integer. A
        collision between two tenants costs contention and nothing else — the
        lock exists to serialise chain appends, so two tenants sharing one lock
        are merely slower, never incorrect.
        """
        return zlib.crc32(self.id.encode()) & 0x7FFF_FFFF
