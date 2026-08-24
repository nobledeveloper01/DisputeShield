"""§11.9's load target for the compliance clock.

"500 concurrent agents, 10,000 open disputes, sweep completes within 60 s." The
agent concurrency is a queue-read property and is covered by
`tests/test_queue_performance.py`. This file covers the half that matters more:
the sweep, at that case count, inside its budget.

The budget is not about throughput. §11.3 puts the tightest SLO in the product on
sweep freshness, and a sweep that takes longer than its interval never catches up
— it falls further behind on every run while the heartbeat keeps looking healthy.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from disputeshield.models import SLAClock, SLADeadline
from disputeshield.sla import sweeper

pytestmark = [pytest.mark.django_db, pytest.mark.slow]

UTC = UTC
CLOCKS = 10_000
BUDGET_SECONDS = 60


@pytest.fixture
def ten_thousand_open_clocks(tenant_a, make_policy, as_tenant):
    version = make_policy(tenant_a, resolution_hours=8)
    started = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)

    from django.db import transaction

    with transaction.atomic(), as_tenant(tenant_a):
        clocks = [
            SLAClock(
                tenant=tenant_a,
                subject_type="dispute",
                subject_id=f"dsp_{n}",
                policy_version=version,
                started_at=started,
            )
            for n in range(CLOCKS)
        ]
        SLAClock.objects.bulk_create(clocks, batch_size=1000)

        # One resolution deadline each, all already due. The sweep's cost is
        # meant to track events due rather than clocks open (ADR-0007), so this
        # is the worst case for it: every clock has something to fire.
        SLADeadline.objects.bulk_create(
            [
                SLADeadline(
                    tenant=tenant_a,
                    clock=clock,
                    kind=SLADeadline.Kind.RESOLUTION,
                    fires_at=started + timedelta(hours=1),
                )
                for clock in clocks
            ],
            batch_size=1000,
        )
        assert SLADeadline.objects.count() == CLOCKS
    return tenant_a


class TestSweepAtLoad:
    def test_it_completes_within_the_budget(self, ten_thousand_open_clocks):
        started = time.perf_counter()
        result = sweeper.sweep(now=datetime(2026, 8, 20, 12, 0, tzinfo=UTC))
        elapsed = time.perf_counter() - started

        assert result.fired == CLOCKS, f"fired {result.fired} of {CLOCKS}"
        assert elapsed < BUDGET_SECONDS, (
            f"sweep took {elapsed:.1f}s over {CLOCKS} due deadlines, past the "
            f"{BUDGET_SECONDS}s budget — a sweep slower than its interval never "
            "catches up, and the heartbeat keeps looking healthy while it falls behind"
        )

    def test_a_second_sweep_over_the_same_load_is_cheap(self, ten_thousand_open_clocks):
        """The watermark design's actual claim (ADR-0007): cost tracks events
        due, not clocks open. Once everything has fired, a sweep should cost
        almost nothing even though ten thousand clocks are still open."""
        sweeper.sweep(now=datetime(2026, 8, 20, 12, 0, tzinfo=UTC))

        started = time.perf_counter()
        result = sweeper.sweep(now=datetime(2026, 8, 20, 12, 1, tzinfo=UTC))
        elapsed = time.perf_counter() - started

        assert result.fired == 0
        assert elapsed < 5, (
            f"an empty sweep over {CLOCKS} open clocks took {elapsed:.1f}s — the "
            "sweep is scanning clocks rather than due deadlines"
        )
