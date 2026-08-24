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

import contextlib
from collections.abc import Callable, Iterator

from django.db import transaction

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

    And a transaction around each, because `SET LOCAL` outside one is discarded —
    silently. A Celery task runs in autocommit, so a version of this function
    without the `atomic()` below sets nothing, matches nothing, and reports that
    there was no work to do.

    One transaction per tenant rather than one for the whole loop: a failure
    while sweeping the eleventh tenant must not roll back the ten before it.
    """
    for tenant_id in tenant_ids(active_only=active_only):
        with (
            transaction.atomic(),
            context.tenant_context(tenant_id),
            db_tenant_context(tenant_id),
        ):
            yield work(tenant_id)


@contextlib.contextmanager
def replica_reads(*, using: str = "replica") -> Iterator[str]:
    """Read on the replica, with the tenant context that connection needs.

    A replica is a **different connection**, and row level security is a property
    of the session — so a context established on the primary is simply absent
    there, and a query returns zero rows with nothing raised. Every read routed
    to the replica goes through here, so "we run analytics on the replica" is a
    statement about the code rather than about the docstring.

    Falls back to the primary when no replica is configured, rather than
    returning nothing: an installation with one database should still get its
    reports.
    """
    from django.conf import settings
    from django.db import transaction

    tenant_id = context.require()
    if using not in settings.DATABASES:
        yield tenant_id
        return

    with transaction.atomic(using=using), db_tenant_context(tenant_id, using=using):
        yield tenant_id
