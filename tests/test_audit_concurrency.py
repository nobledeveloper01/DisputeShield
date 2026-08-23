"""ADR-0003 — concurrent appends cannot fork the chain.

This is the test that justifies paying for an advisory lock on every write path.
Without it, two appends read the same head, write records claiming the same
predecessor, and the nightly verifier pages for a security incident that was
actually two agents clicking at the same moment.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, transaction

from disputeshield import audit
from disputeshield.models import AuditRecord, Tenant

pytestmark = pytest.mark.django_db(transaction=True)

WRITERS = 8
APPENDS_PER_WRITER = 5


def _append_batch(args) -> list[str]:
    tenant_pk, writer = args
    # Each thread gets its own connection, and gives it back — a leaked
    # connection per thread is how this kind of test starts failing intermittently
    # for reasons that have nothing to do with what it is testing.
    try:
        tenant = Tenant.objects.get(pk=tenant_pk)
        return [
            audit.append(
                tenant=tenant,
                event_type="dispute.message_added",
                subject_type="dispute",
                subject_id=f"dsp_{writer}_{i}",
                actor_type="user",
                actor_id=f"agt_{writer}",
                payload={"writer": writer, "i": i},
            ).pk
            for i in range(APPENDS_PER_WRITER)
        ]
    finally:
        connection.close()


def test_concurrent_appends_produce_one_unbroken_chain():
    tenant = Tenant.objects.create(name="Concurrent Co", slug="concurrent")
    total = WRITERS * APPENDS_PER_WRITER

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        written = list(pool.map(_append_batch, [(tenant.pk, w) for w in range(WRITERS)]))

    assert sum(len(batch) for batch in written) == total, "an append was lost"

    result = audit.verify_tenant(tenant.pk)
    assert result.ok, f"chain broken at {result.first_break}: {result.failures[:3]}"
    assert result.records_checked == total


def test_sequences_are_contiguous_with_no_duplicates():
    """A fork shows up here first: two records claiming the same sequence.

    The unique constraint on (tenant, sequence) is the backstop that turns a fork
    into a loud IntegrityError rather than a quiet duplicate — but the lock is
    what stops it happening, and a backstop that fires in production is a design
    that failed.
    """
    tenant = Tenant.objects.create(name="Sequence Co", slug="sequence")
    total = WRITERS * APPENDS_PER_WRITER

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        list(pool.map(_append_batch, [(tenant.pk, w) for w in range(WRITERS)]))

    from disputeshield.tenancy.middleware import db_tenant_context

    # SET LOCAL needs a transaction to be local to. Outside one — which is where
    # a transactional test runs — it silently does nothing and RLS then denies
    # every row, so the assertion below would pass an empty list without this.
    with transaction.atomic(), db_tenant_context(tenant.pk):
        sequences = sorted(
            AuditRecord.objects.all_tenants()
            .filter(tenant_id=tenant.pk)
            .values_list("sequence", flat=True)
        )
    assert sequences == list(range(1, total + 1))
