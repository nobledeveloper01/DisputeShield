# deploy/

| Directory | Contents |
|---|---|
| `docker/` | Production compose stack: web, both Celery pools, beat, Postgres, two Redis instances |
| `helm/` | Chart for `helm install disputeshield disputeshield/disputeshield` |
| `terraform/` | Managed Postgres with PITR and a read replica, Redis, object storage with an object lock, KMS |

Two constraints that a deployment must not get wrong, both of which are
compliance failures rather than availability failures:

- **Celery beat runs exactly one replica, holding a leader lock.** Two schedulers
  double-fire every task, and in this product that is a duplicate breach page at
  03:00 — followed by an alert nobody trusts.
- **The broker and the cache are separate Redis instances.** A cache flush must
  not be capable of destroying the SLA sweep's task queue. See
  [the sweep runbook](../docs/runbook-sla-sweep.md) for what that costs.

The deploy step verifies the audit immutability trigger is installed and refuses
to proceed if it is absent.

Lands in phase 6.
