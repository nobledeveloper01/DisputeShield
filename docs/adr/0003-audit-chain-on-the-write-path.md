# ADR-0003 — The audit hash chain is built synchronously, serialised per tenant

**Status:** accepted
**Date:** 2026-08-23

## Context

§8.3 requires every audit record's hash to cover its own content plus its predecessor's hash, per
tenant, so that tampering anywhere invalidates everything after it. That is a strict serial order
over the table that every write path in the product appends to.

Two concurrent appends that read the same head produce two records claiming the same `prev_hash`.
The chain forks. A fork is indistinguishable from tampering to the nightly verifier, so the first
production symptom is a **page for a security incident** (§11.4, "audit chain verification failed")
caused by nothing more than two agents clicking at the same moment.

The tempting fix is to append records unchained and link them asynchronously. It is faster and it
quietly dismantles the guarantee: there is now a window in which a record exists outside the chain,
and that window is exactly what an insider with database access would use. §8.3's claim only holds if
there is no unchained state to tamper with.

## Decision

Appending an audit record takes a Postgres transaction-scoped advisory lock keyed on the tenant id,
inside the same transaction as the domain write:

```python
with transaction.atomic():
    cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", (ADVISORY_NAMESPACE, tenant.lock_key))
    head = AuditRecord.objects.filter(tenant=tenant).order_by('-id').values_list('hash', flat=True).first()
    ...  # compute hash over (content, head), insert
```

The lock is per tenant, so tenants never contend. It is transaction-scoped, so it releases on commit
or rollback with no path to leaking a held lock. The domain write and its audit record commit
together or not at all — a resolved dispute with no audit record, or an audit record for a resolution
that rolled back, are both impossible rather than unlikely.

## Consequences

- A ceiling on audit writes per tenant per second, measured in phase 1 and published in the
  operations documentation. It is on the order of a thousand per second against a workload where a
  busy case produces a handful of records a day.
- Mass-incident fan-out (amplifier A3) is the one workload that approaches the ceiling. Phase 7
  batches its appends inside a single lock acquisition rather than taking the lock per case.
- Every write path pays a lock acquisition. This is the correct place to pay it: the alternative
  spends the saving on a guarantee the product is sold on.
- The verifier can treat any fork as tampering without qualification, because concurrency can no
  longer produce one. A monitor that alerts on a condition with a benign cause is a monitor that gets
  ignored, and this is the one alert in §11.4 that must never be ignored.
