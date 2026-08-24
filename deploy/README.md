# deploy/

| Directory | Contents |
|---|---|
| `docker/` | Production compose stack: web, both Celery pools, beat, Postgres, two Redis instances |
| `helm/` | Chart for `helm install disputeshield disputeshield/disputeshield` |
| `terraform/` | Managed Postgres with PITR and a read replica, Redis, object-locked audit storage, KMS |

Three constraints a deployment must not get wrong. All three are compliance
failures rather than availability failures, which is why they are stated here
rather than left to a values file:

- **Celery beat runs exactly one replica**, holding a leader lock. Two schedulers
  double-fire every task, and in this product that is a duplicate breach page at
  03:00 — followed by an alert nobody trusts. The Helm template hard-codes `1`
  and uses a `Recreate` strategy, so there is never a rollout second with two
  schedulers alive.
- **The broker and the cache are separate Redis instances.** A cache flush must
  not be capable of destroying the SLA sweep's task queue. See
  [the sweep runbook](../docs/runbook-sla-sweep.md) for what that costs.
- **The audit bucket uses object lock in COMPLIANCE mode.** GOVERNANCE mode can
  be overridden by a sufficiently privileged principal, which is exactly the
  principal an evidence store has to survive.

The deploy step verifies the audit immutability trigger is installed and refuses
to proceed if it is absent — `disputeshield_doctor --strict` is that check, and
it is a deployment gate rather than a smoke test.
