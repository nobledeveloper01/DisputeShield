from __future__ import annotations

import secrets
import zlib

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

    @property
    def lock_key(self) -> int:
        """A stable 31-bit key for pg_advisory_xact_lock (ADR-0003).

        CRC32 of the identifier, masked to fit a signed 32-bit integer. A
        collision between two tenants costs contention and nothing else — the
        lock exists to serialise chain appends, so two tenants sharing one lock
        are merely slower, never incorrect.
        """
        return zlib.crc32(self.id.encode()) & 0x7FFF_FFFF
