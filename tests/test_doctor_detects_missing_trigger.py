"""The doctor has to be able to fail.

§6.2's argument is that a self-hosted install where the immutability migration
silently failed has an audit trail that is immutable only by convention, and no
way to find out. That argument is worth exactly as much as this test: a preflight
nobody has watched fail is a preflight nobody should trust.
"""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

pytestmark = pytest.mark.django_db


@pytest.fixture
def without_the_immutability_trigger(raw_sql):
    raw_sql(
        "DROP TRIGGER IF EXISTS disputeshield_auditrecord_immutable ON disputeshield_auditrecord"
    )
    yield
    raw_sql(
        """
        CREATE TRIGGER disputeshield_auditrecord_immutable
            BEFORE UPDATE OR DELETE ON disputeshield_auditrecord
            FOR EACH ROW EXECUTE FUNCTION disputeshield_audit_immutable()
        """
    )


def test_the_doctor_passes_on_a_correctly_migrated_database():
    out = io.StringIO()
    call_command("disputeshield_doctor", "--strict", stdout=out)
    assert "FAIL" not in out.getvalue()


def test_the_doctor_fails_when_the_trigger_is_missing(without_the_immutability_trigger):
    out = io.StringIO()
    with pytest.raises(CommandError):
        call_command("disputeshield_doctor", "--strict", stdout=out)
    assert "immutability trigger" in out.getvalue()
    assert "MISSING" in out.getvalue()


def test_without_strict_it_reports_but_does_not_refuse(without_the_immutability_trigger):
    """`--strict` is for deployment. Without it, an operator diagnosing a broken
    install gets the full report instead of an exception on the first failure."""
    out = io.StringIO()
    call_command("disputeshield_doctor", stdout=out)
    assert "FAIL" in out.getvalue()
