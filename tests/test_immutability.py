"""§8.3 — the audit trail is immutable, and it is the database that says so.

Every assertion here bypasses the ORM. An ORM-level assertion proves that our
Python is careful; it says nothing about what happens when someone opens psql,
which is the scenario immutability exists for.
"""

from __future__ import annotations

import pytest
from django.db import InternalError, ProgrammingError, transaction
from django.db.utils import NotSupportedError

from disputeshield import audit
from disputeshield.models import AuditRecord

pytestmark = [pytest.mark.django_db, pytest.mark.immutability]

DB_REFUSAL = (InternalError, ProgrammingError, NotSupportedError)


@pytest.fixture
def record(tenant_a):
    return audit.append(
        tenant=tenant_a,
        event_type="dispute.resolved",
        subject_type="dispute",
        subject_id="dsp_TEST",
        actor_type="user",
        actor_id="agt_1",
        payload={"outcome": "upheld"},
    )


def test_update_raises_in_postgres_not_merely_in_the_orm(record, raw_sql):
    with pytest.raises(DB_REFUSAL), transaction.atomic():
        raw_sql(
            "UPDATE disputeshield_auditrecord SET payload = %s WHERE id = %s",
            ['{"outcome": "rejected"}', record.pk],
        )


def test_delete_raises_in_postgres_not_merely_in_the_orm(record, raw_sql):
    with pytest.raises(DB_REFUSAL), transaction.atomic():
        raw_sql("DELETE FROM disputeshield_auditrecord WHERE id = %s", [record.pk])


def test_truncate_is_blocked_too(record, raw_sql):
    """A DELETE-proof table that can still be truncated is not append-only.

    Row-level triggers do not fire for TRUNCATE, so this needs its own
    statement-level trigger — and its own test, because the gap is invisible
    until someone finds it.
    """
    with pytest.raises(DB_REFUSAL), transaction.atomic():
        raw_sql("TRUNCATE disputeshield_auditrecord")


def test_the_application_role_holds_no_update_or_delete_grant(raw_sql):
    granted = {
        row[0]
        for row in raw_sql(
            """
            SELECT privilege_type FROM information_schema.role_table_grants
            WHERE table_name = 'disputeshield_auditrecord' AND grantee = current_user
            """
        )
    }
    assert "SELECT" in granted, "the application must be able to read the audit trail"
    assert "INSERT" in granted, "the application must be able to append to the audit trail"
    assert not (granted & {"UPDATE", "DELETE", "TRUNCATE"}), (
        f"the application role holds {sorted(granted & {'UPDATE', 'DELETE', 'TRUNCATE'})} "
        "on the audit table; §8.3 permits INSERT and SELECT only"
    )


def test_the_orm_refuses_before_the_database_has_to(record, tenant_a, as_tenant):
    with pytest.raises(PermissionError):
        record.delete()
    with as_tenant(tenant_a):
        with pytest.raises(PermissionError):
            AuditRecord.objects.filter(pk=record.pk).delete()
        with pytest.raises(PermissionError):
            AuditRecord.objects.filter(pk=record.pk).update(event_type="tampered")


def test_saving_an_existing_record_is_refused(record):
    record.event_type = "dispute.rejected"
    with pytest.raises(PermissionError):
        record.save()


def test_no_change_or_delete_permission_exists_to_be_granted():
    """default_permissions = () — there is no permission to hand out by mistake."""
    assert AuditRecord._meta.default_permissions == ()
