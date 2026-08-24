# Runbook — the SLA sweep stopped

**Severity:** page, immediately
**Alert:** `disputeshield_sla_sweep_heartbeat` stale for 3 minutes
**Owner:** platform on-call

---

## Why this is the most important runbook in the product

In most systems a stalled background scheduler is an inconvenience. Here it means
SLA clocks stopped advancing, warning notifications were never sent, and breaches
occurred undetected.

It is an outage of the compliance function itself — the exact thing the customer
bought the product to prevent — and **it is invisible from the outside**. The API
stays up. The dashboard keeps rendering. Every queue looks normal, because every
case's displayed state is derived from a clock that stopped.

Recognising that a background job can be the most safety-critical component in a
system is why the heartbeat metric exists at all, and why the SLO on it (120 s,
99.99%) is stricter than the SLO on the API that customers actually call.

---

## 1. Confirm scope

```
disputeshield_sla_sweep_heartbeat        # stale?
disputeshield_disputes_open              # non-zero?
disputeshield_celery_queue_depth{queue="sla"}
```

A stale heartbeat with zero open disputes is a monitoring problem. A stale
heartbeat with open disputes is a compliance incident. Establish which one you
have before doing anything else, and say so in the incident channel — the two
have completely different follow-ups.

## 2. Check beat and its leader lock

```bash
kubectl get pods -l app=disputeshield,component=beat
kubectl logs -l component=beat --tail=200
redis-cli -u "$BROKER_URL" GET disputeshield:beat:leader
redis-cli -u "$BROKER_URL" TTL disputeshield:beat:leader
```

**A stale lock left by a hard-killed pod is by far the most common cause.** A pod
that received `SIGKILL` rather than `SIGTERM` never released it, and the
replacement pod is running, healthy, and refusing to schedule because it believes
another scheduler holds leadership.

## 3. Clear the lock and restart

```bash
redis-cli -u "$BROKER_URL" DEL disputeshield:beat:leader
kubectl rollout restart deployment/disputeshield-beat
```

**Verify the heartbeat resumes before doing anything else.** Everything below
assumes the clock is advancing again; running catch-up against a still-stalled
scheduler produces a second incident on top of the first.

## 4. Backfill the outage window

```bash
kubectl exec deploy/disputeshield-web -- \
  python manage.py disputeshield_sweep --catch-up \
    --from "2026-08-23T09:00:00Z" --to "2026-08-23T09:47:00Z"
```

This is safe to run, and the reason is structural rather than a matter of care:
materialised deadline rows carry `fired_at`, so unfired rows with a past
`fires_at` **are** exactly the missed notifications (ADR-0007). The notification
is recorded before it is sent, so a retry cannot double-notify. Catch-up sends
what was actually missed and nothing else.

## 5. Identify and annotate every breach in the gap

```bash
python manage.py disputeshield_breaches --from "..." --to "..." --annotate-cause \
  "Beat scheduler stalled on a stale leader lock; SLA sweep did not run between
   09:00Z and 09:47Z. Breach detection was delayed, not the handling itself.
   Incident INC-2026-0823."
```

**A breach with a documented systems cause is defensible to a regulator. An
unexplained one is not.** This step is not paperwork; it is the difference
between an explainable event and a finding.

## 6. Notify affected tenants proactively

Do not wait for them to discover it. A customer discovering a compliance-clock
outage themselves is what destroys the trust the entire product is built on, and
they *will* discover it — the breach records are in their dashboard.

Tell them: the window, which of their cases were affected, that clocks have been
backfilled, and that each affected case carries an annotated cause they can show
their own supervisor.

## 7. Post-incident

- **Did the dead-man's switch fire within its 3-minute budget?** If it did not,
  **that is the real defect**, and fixing it takes priority over the root cause of
  the stall. A stall that alerts is a 45-minute incident; a stall that does not is
  discovered by a customer's regulator.
- Was the pod killed rather than drained? Check `terminationGracePeriodSeconds`
  against the drain budget (§8.6 principle 5).
- Does the leader lock have a TTL shorter than the alert threshold? If a stale
  lock can outlive the alert, the system cannot self-heal and every occurrence
  becomes a page.
- Add a `disputeshield_doctor` check for anything discovered here that an
  installation could get wrong at deploy time.

---

## Related

- Alert definitions: specification §11.4
- Deployment shape and the single-beat-replica requirement: §11.1
- Why deadlines are materialised, and why that makes catch-up provably correct:
  [ADR-0007](adr/0007-materialised-deadlines.md)
- A regulatory export that was queued for email and did not arrive:
  [runbook-report-delivery.md](runbook-report-delivery.md)
