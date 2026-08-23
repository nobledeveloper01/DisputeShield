"""Establishes the RLS tenant context for the life of the request's transaction.

ADR-0005 is the whole reason this is middleware and not a helper somebody
remembers to call. `SET LOCAL` is scoped to the transaction, so under PgBouncer
in transaction-pooling mode a recycled connection cannot carry one tenant's
context into another tenant's request.

Setting it any other way is the bug this file exists to prevent, and it is
invisible without a pooler in front of Postgres — which is why the isolation
suite runs through one.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator

from django.db import connection
from django.http import HttpRequest, HttpResponse

SESSION_VARIABLE = "disputeshield.tenant_id"


class TenantContextMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        tenant = getattr(request, "tenant", None)
        if tenant is not None:
            set_tenant_context(str(tenant.pk))
        return self.get_response(request)


def set_tenant_context(tenant_id: str) -> None:
    """Set the RLS session variable, local to the current transaction.

    The third argument to set_config is is_local=true. Passing false here would
    reintroduce ADR-0005's cross-tenant leak, so it is never parameterised.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [SESSION_VARIABLE, tenant_id])


def current_tenant_context() -> str:
    """Whatever tenant this transaction is currently scoped to. '' means none."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting(%s, true)", [SESSION_VARIABLE])
        return cursor.fetchone()[0] or ""


@contextlib.contextmanager
def db_tenant_context(tenant_id: str) -> Iterator[str]:
    """Scope the RLS variable to a block, restoring what was there before.

    `SET LOCAL` is scoped to the *transaction*, not to the Python block that set
    it. In a request that distinction is invisible, because a request is one
    transaction. Anywhere a single transaction touches more than one tenant — a
    sweep over every tenant, a batched audit append, a test — plain
    `set_tenant_context` leaves the last tenant's scope in place for everything
    that follows it.

    That is a cross-tenant read with no bad code anywhere in the traversal, which
    is why this restores rather than clears: clearing would deny the outer scope
    that a nested call was running inside.
    """
    previous = current_tenant_context()
    set_tenant_context(tenant_id)
    try:
        yield tenant_id
    finally:
        set_tenant_context(previous)
