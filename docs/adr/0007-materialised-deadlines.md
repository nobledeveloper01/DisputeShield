# ADR-0007 — SLA deadlines are materialised as rows; the sweep is watermark-driven

**Status:** accepted
**Date:** 2026-08-23

## Context

§4.4 sweeps every minute for cases crossing a warning threshold or a breach boundary. §11.3 sets the
tightest SLO in the product on sweep freshness — a heartbeat within 120 seconds, 99.99% of the time —
because §11.5 establishes that a stalled sweep is an outage of the compliance function itself.

The direct implementation loads every open dispute and evaluates its thresholds against the clock. At
the §11.9 load target of 10,000 open disputes that is 10,000 rows per minute per tenant, and the
sweep's cost scales with queue size rather than with the number of events actually due. The compliance
clock therefore becomes least reliable exactly when a customer has the most cases — which is when it
matters most, and is the opposite of the behaviour the SLO promises.

## Decision

Warning instants, breach instants, auto-close instants and reopen-window expiries are computed at
filing time and stored as rows:

```
SLADeadline(dispute_id, tenant_id, kind, threshold_percent, fires_at, fired_at)
  partial index on (fires_at) WHERE fired_at IS NULL
```

The sweep selects rows whose `fires_at` has passed and whose `fired_at` is null, ordered by
`fires_at`, with `SKIP LOCKED`. Its cost is proportional to events due, not to cases open.

Pause and resume are the only operations that move a deadline. Both already write an `SLAEvent`, so
both recompute and rewrite the affected rows in the same transaction.

## Consequences

- Deadline state exists in two places — derivable from `compute_deadline`, and materialised in rows —
  and they must not diverge. A nightly reconciliation recomputes deadlines for every open case and
  asserts equality with the stored rows, alerting on mismatch. This is a real cost and it is the
  price of the SLO.
- Catch-up mode (§11.5 step 4) becomes trivial and provably correct: unfired rows with a past
  `fires_at` **are** exactly the missed notifications. The runbook's promise that catch-up "will send
  only what was actually missed" becomes a property of the schema rather than a claim about the code,
  which is what makes it safe to run during an incident.
- A policy change that would move deadlines on open cases requires an explicit, audited backfill.
  Deadlines cannot drift silently, because nothing recomputes them implicitly.
- `SKIP LOCKED` lets the sweep run in multiple workers without double-firing, so sweep throughput
  scales even though Celery beat itself stays at exactly one replica (§11.1).
