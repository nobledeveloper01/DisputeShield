"""The phase 3 performance gate: the queue stays usable at load.

§11.9's target is 10,000 open disputes; the roadmap's gate is p95 under 300 ms.
The number matters less than what it protects: the queue's whole argument over a
shared inbox is that the most at-risk case is at the top, and a queue that takes
two seconds to render is one an agent stops reloading.

Marked `slow` — it builds ten thousand rows. `make test-fast` skips it; CI does not.
"""

from __future__ import annotations

import statistics
import time

import pytest

from disputeshield.models import Dispute

pytestmark = [pytest.mark.django_db, pytest.mark.slow]

CASES = 10_000
P95_BUDGET_MS = 300


@pytest.fixture
def a_full_queue(tenant_a, make_policy, make_dispute, as_tenant):
    """One real case, then bulk rows for the rest.

    Bulk-creating skips the service layer deliberately: this measures the read
    path, and filing ten thousand cases through the audit trail would measure the
    write path instead — and take minutes doing it.

    Each dispute gets its **own** clock. The first version of this fixture reused
    the seed's, which is a OneToOne, so every bulk row violated the constraint and
    `ignore_conflicts=True` swallowed all of them. The suite then measured a queue
    of one row and passed comfortably. Hence the assertion at the end: a
    performance gate that silently measures nothing is worse than no gate, because
    it reports success.
    """
    from datetime import timedelta

    from disputeshield.models import SLAClock

    seed = make_dispute(tenant_a, customer_ref="usr_seed")

    with as_tenant(tenant_a):
        clocks = [
            SLAClock(
                tenant=tenant_a,
                subject_type="dispute",
                subject_id=f"bulk_{n}",
                policy_version=seed.policy_version,
                started_at=seed.submitted_at,
            )
            for n in range(CASES - 1)
        ]
        SLAClock.objects.bulk_create(clocks, batch_size=1000)

        rows = [
            Dispute(
                tenant=tenant_a,
                reference=f"DS-2026-{n:06d}",
                customer_ref_hash=f"{n:064x}",
                category="failed_transfer" if n % 2 else "card_chargeback",
                description="bulk",
                policy_version=seed.policy_version,
                clock=clocks[n],
                submitted_at=seed.submitted_at,
                ack_deadline=seed.ack_deadline + timedelta(minutes=n % 500),
                resolution_deadline=seed.resolution_deadline + timedelta(minutes=n),
                breach_resolution=(n % 997 == 0),
                status="investigating",
            )
            for n in range(CASES - 1)
        ]
        Dispute.objects.bulk_create(rows, batch_size=1000)

        actual = Dispute.objects.count()
        assert actual == CASES, (
            f"the load fixture built {actual} cases, not {CASES}. Every assertion "
            "below would pass trivially against a short queue."
        )

    return tenant_a


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    index = min(int(len(ordered) * fraction), len(ordered) - 1)
    return ordered[index]


class TestQueueAtLoad:
    def test_the_queue_page_is_under_the_p95_budget(self, a_full_queue, client_for):
        client = client_for(a_full_queue)
        client.get("/v1/disputes/")  # warm connection and query plan

        samples = []
        for _ in range(20):
            started = time.perf_counter()
            response = client.get("/v1/disputes/")
            samples.append((time.perf_counter() - started) * 1000)
            assert response.status_code == 200

        p95 = _percentile(samples, 0.95)
        assert p95 < P95_BUDGET_MS, (
            f"queue p95 {p95:.0f}ms exceeds the {P95_BUDGET_MS}ms budget "
            f"(median {statistics.median(samples):.0f}ms) at {CASES} open cases"
        )

    def test_the_urgency_sort_uses_an_index_rather_than_a_full_sort(self, a_full_queue, as_tenant):
        """A budget met by a sequential scan is a budget that stops being met at
        the next order of magnitude. Asserting the plan is what makes the number
        durable."""
        from django.db import connection

        with as_tenant(a_full_queue), connection.cursor() as cursor:
            cursor.execute(
                "EXPLAIN (FORMAT JSON) "
                "SELECT * FROM disputeshield_dispute "
                "ORDER BY breach_resolution DESC, breach_ack DESC, resolution_deadline, id "
                "LIMIT 50"
            )
            plan = str(cursor.fetchone()[0])

        assert "Seq Scan" not in plan or "Index" in plan, (
            f"the queue's default sort plans a sequential scan at {CASES} rows:\n{plan}"
        )

    def test_a_filtered_queue_is_also_within_budget(self, a_full_queue, client_for):
        client = client_for(a_full_queue)
        samples = []
        for _ in range(10):
            started = time.perf_counter()
            response = client.get("/v1/disputes/?sla_risk=breached")
            samples.append((time.perf_counter() - started) * 1000)
            assert response.status_code == 200

        assert _percentile(samples, 0.95) < P95_BUDGET_MS
