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

from collections.abc import Callable

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
