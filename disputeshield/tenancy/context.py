"""The current tenant, for the life of one request or task.

This is the first of §8.1's three isolation layers to be consulted and the one
that fails loudest. `require()` raises rather than returning None, because every
caller that would have to handle None is a caller that could forget to.

A contextvar rather than thread-local storage: Django runs under ASGI, and a
thread-local is shared by every coroutine on the thread.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar

_current_tenant: ContextVar[str | None] = ContextVar("disputeshield_tenant", default=None)


class TenantContextRequired(RuntimeError):
    """Raised when tenant-scoped data is touched with no tenant established.

    This is a bug, never a permission problem, and it is deliberately not an
    HTTP exception: a missing tenant context means the code path did not decide
    who it was acting for, and answering with 403 or 404 would paper over that.
    """


def get() -> str | None:
    return _current_tenant.get()


def require() -> str:
    tenant_id = _current_tenant.get()
    if tenant_id is None:
        raise TenantContextRequired(
            "No tenant context. Tenant-scoped models cannot be queried without one — "
            "wrap the call in disputeshield.tenancy.context.tenant_context(tenant_id)."
        )
    return tenant_id


def set(tenant_id: str):
    return _current_tenant.set(tenant_id)


def reset(token) -> None:
    _current_tenant.reset(token)


@contextlib.contextmanager
def tenant_context(tenant_id: str) -> Iterator[str]:
    """Establish the tenant for a block, and restore whatever was there before.

    Restoring rather than clearing matters: nested contexts occur in management
    commands that sweep many tenants, and clearing would silently unscope the
    outer loop.
    """
    token = _current_tenant.set(tenant_id)
    try:
        yield tenant_id
    finally:
        _current_tenant.reset(token)
