# tests/

Four suites are **blocking gates**. They run as their own CI job so a failure is
unmissable rather than buried in a general test run, and they never go yellow.

| Suite | Marker | Asserts |
|---|---|---|
| `test_tenant_isolation.py` | `isolation` | Cross-tenant and cross-customer reads return **404, not 403**. Runs through PgBouncer in transaction-pooling mode (ADR-0005) — against Postgres directly it passes while testing nothing |
| `test_immutability.py` | `immutability` | `UPDATE`/`DELETE` on the audit table raises **in Postgres**, asserted by a test that bypasses the ORM. An ORM-only assertion tests Django, not the database |
| `test_serializer_leakage.py` | `leakage` | Introspects the widget serializer's **full field graph** and asserts no path reaches internal content. Sampling outputs is insufficient — a future field could open a path no sample exercises |
| `test_sla_deadlines.py` | — | Weekends, holidays, DST in both directions, multiple pauses, a pause spanning a holiday, sub-business-day windows. Runs twice, the second time under `TZ=Pacific/Kiritimati` |

```bash
make gates        # the blocking suites alone
make test         # everything, with the coverage gate
```

Suites land with their phases (`docs/ROADMAP.md`). Phase 1 brings isolation and
immutability; phase 2 brings deadlines; phase 3 brings leakage.
