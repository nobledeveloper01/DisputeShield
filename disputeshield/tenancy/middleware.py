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

from django.db import DEFAULT_DB_ALIAS, connections
from django.http import HttpRequest, HttpResponse

from disputeshield.tenancy import context

SESSION_VARIABLE = "disputeshield.tenant_id"


class TenantContextMiddleware:
    """Owns the lifetime of the tenant context for a request.

    Both halves matter, and the second is the one that bites:

      * **Set**, if the request already carries a tenant (a session, a resolved
        subdomain). Authentication may also set it later, from the API key.
      * **Reset, always.** A contextvar set during a request outlives it —
        worker threads and event loops are reused, so the next request handled by
        the same worker starts with the previous request's tenant still in scope
        until something overwrites it. An anonymous or failed-authentication
        request never overwrites it, and inherits the last tenant that did.

    RLS is `SET LOCAL` and clears itself at commit, so a stale contextvar alone
    yields zero rows rather than another tenant's rows. That is the third layer
    doing its job — not a reason to leave the first one dirty.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        token = context.set_none()
        try:
            tenant = getattr(request, "tenant", None)
            if tenant is not None:
                context.set(str(tenant.pk))
                set_tenant_context(str(tenant.pk))
            return self.get_response(request)
        finally:
            context.reset(token)


class NoTransaction(RuntimeError):
    """`SET LOCAL` was issued outside a transaction, where it does nothing.

    Postgres warns and moves on, so the failure is silent: the variable is never
    set, row level security matches nothing, and every query returns zero rows.
    Nothing raises, nothing logs, and a background job reports that it found no
    work to do.

    This is the third time that shape of bug appeared in this codebase — the SLA
    sweep, the attachment download, and the packaged-install smoke test — so it
    raises now instead of being something each caller has to remember.
    """


def set_tenant_context(tenant_id: str, *, using: str = DEFAULT_DB_ALIAS) -> None:
    """Set the RLS session variable, local to the current transaction.

    The third argument to set_config is is_local=true. Passing false here would
    reintroduce ADR-0005's cross-tenant leak, so it is never parameterised.

    `using` exists because **a read replica is a different connection**. Row level
    security is a property of the session, so a context established on the primary
    is simply absent on the replica — and a query issued there returns zero rows
    with nothing raised. Anything reading from the replica (the regulatory export,
    the policy simulator) has to establish the context on that connection.
    """
    connection = connections[using]
    if not connection.in_atomic_block:
        raise NoTransaction(
            "Tenant context must be established inside a transaction. `SET LOCAL` "
            "outside one is discarded, so row level security would match nothing "
            "and every query would quietly return zero rows. Wrap the call in "
            "`transaction.atomic()` — or use `for_each_tenant`, which does."
        )
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [SESSION_VARIABLE, tenant_id])


def current_tenant_context(*, using: str = DEFAULT_DB_ALIAS) -> str:
    """Whatever tenant this transaction is currently scoped to. '' means none."""
    connection = connections[using]
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting(%s, true)", [SESSION_VARIABLE])
        return cursor.fetchone()[0] or ""


@contextlib.contextmanager
def db_tenant_context(tenant_id: str, *, using: str = DEFAULT_DB_ALIAS) -> Iterator[str]:
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
    previous = current_tenant_context(using=using)
    set_tenant_context(tenant_id, using=using)
    try:
        yield tenant_id
    finally:
        set_tenant_context(previous, using=using)
