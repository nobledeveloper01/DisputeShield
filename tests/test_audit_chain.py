"""The hash chain: linking, detection, and what tampering actually looks like."""

from __future__ import annotations

import itertools

import pytest

from disputeshield import audit
from disputeshield.audit.chain import COVERED_FIELDS, GENESIS, compute_hash, record_content
from disputeshield.models import AuditRecord

pytestmark = pytest.mark.django_db


def _append(tenant, n: int = 1):
    return [
        audit.append(
            tenant=tenant,
            event_type="dispute.message_added",
            subject_type="dispute",
            subject_id=f"dsp_{i}",
            actor_type="user",
            actor_id="agt_1",
            payload={"i": i},
        )
        for i in range(n)
    ]


def test_the_first_record_links_to_genesis(tenant_a):
    (first,) = _append(tenant_a, 1)
    assert first.sequence == 1
    assert first.prev_hash == GENESIS


def test_each_record_links_to_its_predecessor(tenant_a):
    records = _append(tenant_a, 5)
    for previous, current in itertools.pairwise(records):
        assert current.prev_hash == previous.hash
        assert current.sequence == previous.sequence + 1


def test_a_clean_chain_verifies(tenant_a):
    _append(tenant_a, 20)
    result = audit.verify_tenant(tenant_a.pk)
    assert result.ok
    assert result.records_checked == 20


def test_tampering_invalidates_that_record_and_everything_after_it(tenant_a, raw_sql, tamper):
    """§8.3's actual claim, tested as stated.

    The database refuses an UPDATE, so tampering has to be simulated the way a
    real attacker with write access would: drop the trigger first. That is the
    scenario the chain exists for — the trigger stops the casual case, and the
    chain catches the determined one.
    """
    records = _append(tenant_a, 10)
    victim = records[4]

    with tamper(tenant_a):
        raw_sql(
            "UPDATE disputeshield_auditrecord SET payload = '{\"i\": 999}' WHERE id = %s",
            [victim.pk],
        )

    result = audit.verify_tenant(tenant_a.pk)
    assert not result.ok
    assert result.first_break == victim.sequence

    # §8.3's claim, asserted literally: the altered record fails its own hash and
    # *every* record after it fails its link. An attacker who edits one row has to
    # rewrite the entire remainder of the chain to hide it.
    broken = {failure.sequence for failure in result.failures}
    assert broken == set(range(victim.sequence, len(records) + 1))


def test_a_deleted_record_shows_as_a_sequence_gap(tenant_a, raw_sql, tamper):
    records = _append(tenant_a, 6)
    victim = records[2]

    with tamper(tenant_a):
        raw_sql("DELETE FROM disputeshield_auditrecord WHERE id = %s", [victim.pk])

    result = audit.verify_tenant(tenant_a.pk)
    assert not result.ok
    assert any("sequence gap" in failure.reason for failure in result.failures)


def test_chains_are_independent_per_tenant(tenant_a, tenant_b):
    _append(tenant_a, 3)
    _append(tenant_b, 2)

    assert audit.verify_tenant(tenant_a.pk).records_checked == 3
    assert audit.verify_tenant(tenant_b.pk).records_checked == 2
    assert audit.verify_tenant(tenant_a.pk).ok
    assert audit.verify_tenant(tenant_b.pk).ok


def test_the_hash_covers_every_meaningful_field(tenant_a):
    """A field the hash does not cover is a field an attacker can change freely.

    This asserts the covered set against the model rather than against a list
    written by hand, so adding a field to AuditRecord without deciding whether it
    is evidence fails here instead of silently creating an unprotected column.
    """
    derived = {"recorded_at", "prev_hash", "hash", "tenant"}
    model_fields = {f.name for f in AuditRecord._meta.fields} - derived
    covered = {
        field.removesuffix("_id") if field == "tenant_id" else field for field in COVERED_FIELDS
    }
    covered.add("tenant_id")
    model_fields = {"tenant_id" if f == "tenant" else f for f in model_fields}
    assert model_fields <= covered, f"not covered by the hash: {sorted(model_fields - covered)}"


def test_recomputing_a_hash_is_deterministic(tenant_a):
    (record,) = _append(tenant_a, 1)
    again = compute_hash(record_content(record), record.prev_hash)
    assert again == record.hash


def test_corrections_are_appended_and_the_original_survives(tenant_a, as_tenant):
    (original,) = _append(tenant_a, 1)
    correction = audit.correct(
        original=original, reason="wrong outcome recorded", actor_type="user", actor_id="agt_2"
    )

    with as_tenant(tenant_a):
        assert AuditRecord.objects.filter(pk=original.pk).exists()
        assert AuditRecord.objects.count() == 2
    assert correction.corrects == original.pk
    assert audit.verify_tenant(tenant_a.pk).ok


def test_an_unattributed_record_is_refused(tenant_a):
    with pytest.raises(audit.ActorRequired):
        audit.append(
            tenant=tenant_a,
            event_type="dispute.resolved",
            subject_type="dispute",
            subject_id="dsp_1",
            actor_type="user",
            actor_id="",
        )
    with pytest.raises(audit.ActorRequired):
        audit.append(
            tenant=tenant_a,
            event_type="dispute.resolved",
            subject_type="dispute",
            subject_id="dsp_1",
            actor_type="anonymous",
        )
