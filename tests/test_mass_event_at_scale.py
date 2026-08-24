"""The roadmap's phase 7 gate, at the number it names.

"Fan-out of a 5,000-case mass resolution writes 5,000 individual audit records
and executes zero bulk `UPDATE`s. Asserted by counting statements, not by reading
the code."

Both halves matter and they pull in opposite directions. Individual records are
what make each resolution explainable six months later; a bulk update is what
would make the fan-out fast. The design pays for the first and refuses the
second — and batches only the audit chain's advisory lock, which ADR-0003
anticipated for exactly this workload.
"""

from __future__ import annotations

import time
from collections import deque

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from disputeshield.disputes import mass_events
from disputeshield.identifiers import dispute_id
from disputeshield.models import (
    AuditRecord,
    Dispute,
    MassEvent,
    MassEventMembership,
    SLAClock,
)
from disputeshield.models.dispute import Outcome, Status

pytestmark = [pytest.mark.django_db, pytest.mark.slow]

CASES = 5_000


@pytest.fixture
def five_thousand_cases(tenant_a, make_dispute, make_policy, as_tenant):
    """Built in bulk: this measures the fan-out, not the filing path."""
    from datetime import timedelta

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
            for n in range(CASES)
        ]
        SLAClock.objects.bulk_create(clocks, batch_size=1000)

        cases = []
        for n in range(CASES):
            case_id = dispute_id()
            clocks[n].subject_id = case_id
            cases.append(
                Dispute(
                    id=case_id,
                    tenant=tenant_a,
                    reference=f"DS-2026-B{n:05d}",
                    customer_ref_hash=f"{n:064x}",
                    category="failed_transfer",
                    description="bulk",
                    status=Status.INVESTIGATING,
                    policy_version=seed.policy_version,
                    clock=clocks[n],
                    submitted_at=seed.submitted_at,
                    ack_deadline=seed.ack_deadline,
                    resolution_deadline=seed.resolution_deadline + timedelta(minutes=n),
                )
            )
        Dispute.objects.bulk_create(cases, batch_size=1000)
        SLAClock.objects.bulk_update(clocks, ["subject_id"], batch_size=1000)

        event = MassEvent.objects.create(
            tenant=tenant_a,
            title="Rail outage",
            root_cause="Provider timeouts",
            created_by="agt_1",
        )
        MassEventMembership.objects.bulk_create(
            [
                MassEventMembership(
                    tenant=tenant_a, mass_event=event, dispute=case, added_by="agt_1"
                )
                for case in cases
            ],
            batch_size=1000,
        )
        assert MassEventMembership.objects.filter(mass_event=event).count() == CASES
    return event


class TestFanOutAtScale:
    def test_it_writes_one_audit_record_per_case_and_no_bulk_update(
        self, tenant_a, five_thousand_cases, as_tenant
    ):
        event = five_thousand_cases

        with as_tenant(tenant_a):
            before = AuditRecord.objects.filter(event_type="dispute.resolve").count()

        # Django caps the query log at 9,000 entries and warns. A fan-out over
        # 5,000 cases issues more than that, so the default cap would leave the
        # assertion below inspecting only the tail — and a bulk UPDATE in the
        # first batch would pass a gate written to forbid it.
        # `queries_log` is a deque sized at connection setup, so raising
        # `queries_limit` alone does nothing — the deque keeps its original
        # maxlen and silently drops the head.
        original_log = connection.queries_log
        connection.queries_limit = 200_000
        connection.queries_log = deque(maxlen=200_000)

        started = time.perf_counter()
        with as_tenant(tenant_a), CaptureQueriesContext(connection) as captured:
            result = mass_events.apply_outcome(
                event=event,
                outcome=Outcome.UPHELD,
                notes="Provider confirmed the rail failure; reversals issued.",
                actor_id="agt_1",
            )
        elapsed = time.perf_counter() - started
        statements = list(captured)
        connection.queries_log = original_log

        assert len(statements) < 200_000, "the query log truncated; the assertion below is partial"

        with as_tenant(tenant_a):
            after = AuditRecord.objects.filter(event_type="dispute.resolve").count()

        assert result.applied == CASES
        assert after - before == CASES, (
            f"{after - before} audit records for {CASES} cases — a batch resolution "
            "must leave each case individually explainable"
        )

        bulk_case_updates = [
            q["sql"]
            for q in statements
            if q["sql"].startswith("UPDATE")
            and '"disputeshield_dispute"' in q["sql"]
            and " IN (" in q["sql"]
        ]
        assert not bulk_case_updates, (
            f"the fan-out issued {len(bulk_case_updates)} bulk UPDATE(s) over cases — "
            "§8.3 forbids a bulk-edit surface over auditable records"
        )

        # Not a stated budget, but a fan-out nobody can wait for is a fan-out an
        # agent abandons halfway, leaving half a mass event applied.
        assert elapsed < 180, f"the fan-out took {elapsed:.0f}s for {CASES} cases"

    def test_the_chain_still_verifies_after_a_five_thousand_case_fan_out(
        self, tenant_a, five_thousand_cases, as_tenant
    ):
        """The batched append is the one thing that changed about how records are
        written, so the chain it produces gets checked at the scale that motivated
        the batching."""
        from disputeshield import audit

        event = five_thousand_cases
        with as_tenant(tenant_a):
            mass_events.apply_outcome(
                event=event, outcome=Outcome.UPHELD, notes="Confirmed.", actor_id="agt_1"
            )

        result = audit.verify_tenant(tenant_a.pk)
        assert result.ok, f"chain broken at {result.first_break}: {result.failures[:3]}"
        assert result.records_checked >= CASES
