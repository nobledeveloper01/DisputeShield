"""§8.1 layer 2: there is no unscoped access path.

The important property is not that the manager filters. It is that **no manager
on a tenant-scoped model returns unscoped rows** — including the default one,
including the one the Django admin uses, including the one a `related_name`
traversal goes through. A forgotten filter raises in development instead of
leaking in production.

`all_tenants()` exists because platform operations genuinely need it (the SLA
sweep, chain verification, tenant provisioning). It is a separate method with an
unmissable name, so that using it is a decision somebody made rather than a
default somebody inherited.
"""

from __future__ import annotations

from django.db import models

from disputeshield.tenancy import context


class TenantScopedQuerySet(models.QuerySet):
    def for_current_tenant(self) -> TenantScopedQuerySet:
        return self.filter(tenant_id=context.require())


class TenantScopedManager(models.Manager.from_queryset(TenantScopedQuerySet)):
    """Default manager for every tenant-scoped model. Raises without a context."""

    def get_queryset(self) -> TenantScopedQuerySet:
        return super().get_queryset().filter(tenant_id=context.require())

    def all_tenants(self) -> TenantScopedQuerySet:
        """Deliberately unscoped. Platform operations only, and always audited.

        Every use of this in application code should be visible in review. If it
        appears inside a request-handling path, that is the finding.
        """
        return super().get_queryset()


class TenantScopedModel(models.Model):
    """Abstract base: a tenant FK, a scoped default manager, and PROTECT.

    `on_delete=PROTECT` is ADR-0006. On a system whose product is evidence,
    CASCADE means one mistaken tenant deletion silently destroys the seven-year
    record — in one statement, taking the audit trail that would have recorded
    the destruction down with it.
    """

    tenant = models.ForeignKey(
        "disputeshield.Tenant",
        on_delete=models.PROTECT,
        db_index=True,
        related_name="+",
    )

    objects = TenantScopedManager()

    class Meta:
        abstract = True
