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
    """A healthy installation has a sweep that has run, so the fixture runs one.

    The heartbeat check is non-fatal — a fresh install has legitimately never
    swept — but it still reports FAIL, because a blank must not read as healthy.
    """
    from disputeshield.sla import sweeper

    sweeper.sweep()

    out = io.StringIO()
    call_command("disputeshield_doctor", "--strict", stdout=out)
    assert "FAIL" not in out.getvalue()


def test_a_stalled_sweep_is_reported_but_does_not_refuse_to_serve():
    """§11.5's failure mode, surfaced at install time.

    Non-fatal on purpose: refusing to start because the heartbeat is stale would
    take down the API in response to a scheduler problem, turning a silent
    compliance outage into a loud availability one — and losing the ability to
    read the dashboard that shows which cases are affected.
    """
    from datetime import timedelta

    from django.utils import timezone

    from disputeshield.models import SweepHeartbeat

    SweepHeartbeat.objects.update_or_create(
        singleton=True, defaults={"last_swept_at": timezone.now() - timedelta(hours=1)}
    )

    out = io.StringIO()
    call_command("disputeshield_doctor", "--strict", stdout=out)  # does not raise
    assert "past the 3-minute budget" in out.getvalue()


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
