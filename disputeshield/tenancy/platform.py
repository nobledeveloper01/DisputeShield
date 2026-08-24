"""Iterating every tenant, for the work that legitimately spans all of them.

Row level security is FORCEd on every tenant-scoped table, so a query issued with
no tenant context returns **zero rows** — not an error, not every row. That is the
correct failure for a request that forgot to scope itself, and it is a trap for
background work that never had a request to inherit a scope from.

The trap is not hypothetical. The SLA sweep, the notification dispatcher and the
deadline reconciler all began life querying `all_tenants()` directly, and all
three would have found nothing in production while passing their tests — because
the tests inherited a tenant context from a fixture that happened to hold one
open. A sweep that silently fires nothing is §11.5's failure mode arriving by a
route the runbook does not cover: the heartbeat is fresh, the scheduler is
healthy, and no clock ever advances.

So platform work goes through here, and `for_each_tenant` is the only supported
way to write it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from disputeshield.models import Tenant
from disputeshield.tenancy import context
from disputeshield.tenancy.middleware import db_tenant_context


def tenant_ids(*, active_only: bool = True) -> list[str]:
    """Every tenant. `Tenant` carries no RLS policy, deliberately — it is the
    isolation root, and a policy on it would be circular."""
    queryset = Tenant.objects.all()
    if active_only:
        queryset = queryset.filter(is_active=True)
    return list(queryset.values_list("pk", flat=True))


def for_each_tenant[T](work: Callable[[str], T], *, active_only: bool = True) -> Iterator[T]:
    """Run `work(tenant_id)` once per tenant, with both isolation contexts set.

    Both, not one: the Python contextvar the scoped managers read, and the
    Postgres session variable RLS reads. Setting only the second leaves every
    `Model.objects` call raising `TenantContextRequired`; setting only the first
    leaves every query returning nothing.
    """
    for tenant_id in tenant_ids(active_only=active_only):
        with context.tenant_context(tenant_id), db_tenant_context(tenant_id):
            yield work(tenant_id)
