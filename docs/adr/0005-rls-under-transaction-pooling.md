# ADR-0005 — Tenant context is set with `SET LOCAL` inside an explicit transaction

**Status:** accepted
**Date:** 2026-08-23

## Context

§8.1's third isolation layer is Postgres Row Level Security, keyed on a session variable set from the
authenticated tenant, so that a query which forgets to scope returns nothing.

The straightforward implementation sets the variable once per request:

```python
cursor.execute("SET disputeshield.tenant_id = %s", (tenant.id,))
```

`SET` without `LOCAL` is session-scoped. Under PgBouncer in transaction-pooling mode — what any
Django deployment at meaningful scale runs — the backend connection returns to the pool at
transaction end and is handed to the next request, **carrying that setting**. The next request may
belong to a different tenant. If its own middleware has not yet run, or if it reads outside a
transaction, RLS evaluates against the previous tenant's id.

The result is a cross-tenant leak produced by the layer whose entire purpose is preventing
cross-tenant leaks. It appears only under connection reuse, which means only under load, which means
only in production, and it is invisible to a test suite that talks to Postgres directly — because
without a pooler there is no reuse to expose it.

## Decision

Tenant context is established with `SET LOCAL`, inside an explicit transaction, on every request:

```python
with transaction.atomic():
    cursor.execute("SELECT set_config('disputeshield.tenant_id', %s, true)", (str(tenant.id),))
```

The third argument is `is_local`. The setting is discarded at commit or rollback, so a pooled
connection cannot carry it anywhere.

`ATOMIC_REQUESTS` is enabled so that every request — including reads — runs in a transaction, because
a read outside one has no local scope to attach the setting to. Analytics and export paths against
the read replica take identical treatment; a replica connection with no tenant context returns zero
rows, which is the correct failure mode.

**The isolation suite runs through PgBouncer in transaction-pooling mode**, not against Postgres
directly. Against Postgres directly this class of bug cannot be reproduced, which is precisely why it
survives review elsewhere.

## Consequences

- Every request holds a transaction for its full duration. Long-running exports must be chunked so
  they do not hold one open; phase 6's export is built that way from the start.
- `ATOMIC_REQUESTS` means a view raising after a successful external call rolls back the database
  write. The transactional outbox in the architecture plan's D7 exists partly for this reason: sends
  are committed intent, dispatched afterwards.
- CI runs a pooled Postgres. It is slower to set up and it is the only configuration in which the
  test proves anything.
