"""The sweep, and the runbook promises that depend on it.

§11.5 is the most important runbook in the product, and two of its steps are only
safe because of properties asserted here: that catch-up sends exactly what was
missed, and that a replay cannot double-notify. A runbook whose steps have never
been executed against the code is a document, not a procedure.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from disputeshield.models import (
    AuditRecord,
    NotificationOutbox,
    SLADeadline,
    SLAEvent,
    SweepHeartbeat,
)
from disputeshield.sla import clock as clock_service
from disputeshield.sla import sweeper as sweep_module

pytestmark = pytest.mark.django_db

LAGOS = ZoneInfo("Africa/Lagos")
UTC = UTC


def wednesday(hour: int = 9, minute: int = 0) -> datetime:
    return datetime(2026, 8, 19, hour, minute, tzinfo=LAGOS).astimezone(UTC)


@pytest.fixture
def running_clock(tenant_a, make_policy, as_tenant):
    version = make_policy(tenant_a, resolution_hours=8, acknowledgement_minutes=60)
    with as_tenant(tenant_a):
        yield clock_service.start(
            tenant=tenant_a, subject_id="dsp_1", policy_version=version, started_at=wednesday(9)
        )


class TestFiring:
    def test_nothing_fires_before_anything_is_due(self, running_clock):
        assert sweep_module.sweep(now=wednesday(9, 30)).fired == 0

    def test_the_acknowledgement_deadline_fires_when_due(self, running_clock, tenant_a, as_tenant):
        result = sweep_module.sweep(now=wednesday(10, 1))
        assert result.fired == 1
        with as_tenant(tenant_a):
            fired = running_clock.deadlines.get(kind=SLADeadline.Kind.ACKNOWLEDGEMENT)
        assert fired.fired_at is not None

    def test_warnings_fire_in_order_of_when_they_are_due(self, running_clock, tenant_a, as_tenant):
        sweep_module.sweep(now=wednesday(14))  # ack (10:00) and the 50% warning (13:00)
        with as_tenant(tenant_a):
            fired = set(
                running_clock.deadlines.filter(fired_at__isnull=False).values_list(
                    "kind", "threshold_percent"
                )
            )
        assert fired == {("acknowledgement", None), ("warning", 50)}

    def test_a_breach_and_a_warning_are_recorded_as_different_events(
        self, running_clock, tenant_a, as_tenant
    ):
        sweep_module.sweep(now=wednesday(17, 1))
        with as_tenant(tenant_a):
            kinds = set(running_clock.events.values_list("kind", flat=True))
        assert SLAEvent.Kind.BREACHED in kinds
        assert SLAEvent.Kind.WARNED in kinds

    def test_every_firing_writes_an_audit_record(self, running_clock, tenant_a, as_tenant):
        sweep_module.sweep(now=wednesday(17, 1))
        with as_tenant(tenant_a):
            events = AuditRecord.objects.filter(event_type__startswith="sla.").count()
        # started, plus four fired deadlines (ack, 50%, 80%, 95%) and the breach.
        assert events >= 5

    def test_a_stopped_clock_does_not_fire(self, running_clock, tenant_a, as_tenant):
        with as_tenant(tenant_a):
            clock_service.stop(clock=running_clock, actor_type="user", actor_id="agt_1")
        assert sweep_module.sweep(now=wednesday(23)).fired == 0


class TestIdempotency:
    def test_sweeping_twice_does_not_notify_twice(self, running_clock, tenant_a, as_tenant):
        first = sweep_module.sweep(now=wednesday(17, 1))
        second = sweep_module.sweep(now=wednesday(17, 2))

        assert first.fired > 0
        assert second.fired == 0, "a fired deadline must never be re-claimed"
        with as_tenant(tenant_a):
            assert NotificationOutbox.objects.count() == first.notifications_created

    def test_the_idempotency_key_does_not_contain_a_timestamp(
        self, running_clock, tenant_a, as_tenant
    ):
        """A key derived from when the notification was generated makes every
        catch-up run produce a fresh set of pages for events already notified —
        which is how a recovery becomes a second incident."""
        with as_tenant(tenant_a):
            deadline = running_clock.deadlines.get(kind=SLADeadline.Kind.RESOLUTION)
        key = sweep_module.idempotency_key(deadline)
        assert key == f"sla:{running_clock.pk}:resolution"
        assert sweep_module.idempotency_key(deadline) == key

    def test_a_duplicate_notification_cannot_be_written(self, running_clock, tenant_a, as_tenant):
        from django.db import IntegrityError, transaction

        sweep_module.sweep(now=wednesday(17, 1))
        with as_tenant(tenant_a):
            existing = NotificationOutbox.objects.first()
            with pytest.raises(IntegrityError), transaction.atomic():
                NotificationOutbox.objects.create(
                    tenant=tenant_a,
                    idempotency_key=existing.idempotency_key,
                    event_type="sla.resolution",
                )


class TestCatchUp:
    def test_catch_up_fires_exactly_what_was_missed(self, running_clock, tenant_a, as_tenant):
        """§11.5 step 4. Unfired rows with a past `fires_at` *are* the missed
        notifications, so the runbook's promise is a property of the schema
        rather than a claim about the code."""
        # The sweep is down from 09:00 to 18:00: nothing fires while it is stalled.
        with as_tenant(tenant_a):
            due_during_outage = running_clock.deadlines.filter(
                fires_at__lte=wednesday(17, 30)
            ).count()

        recovery = sweep_module.sweep(now=wednesday(18))
        assert recovery.fired == due_during_outage

        with as_tenant(tenant_a):
            assert NotificationOutbox.objects.count() == due_during_outage
            assert running_clock.deadlines.filter(fired_at__isnull=True).count() == 0

    def test_catch_up_after_a_partial_sweep_fires_only_the_remainder(
        self, running_clock, tenant_a, as_tenant
    ):
        before_outage = sweep_module.sweep(now=wednesday(13, 1))
        recovery = sweep_module.sweep(now=wednesday(18))

        with as_tenant(tenant_a):
            total = running_clock.deadlines.filter(fired_at__isnull=False).count()
        assert before_outage.fired + recovery.fired == total

    def test_a_late_firing_records_how_late_it_was(self, running_clock, tenant_a, as_tenant):
        """§11.5 step 5: a breach detected late must say so, so the lateness is
        attributable to the systems cause rather than to the handling of the case."""
        sweep_module.sweep(now=wednesday(20))
        with as_tenant(tenant_a):
            breach = AuditRecord.objects.get(
                event_type="sla.breached", payload__deadline_kind="resolution"
            )
        assert breach.payload["detected_late_seconds"] == 3 * 3600


class TestHeartbeat:
    def test_the_heartbeat_is_written_even_when_nothing_fired(self, running_clock):
        """ "Nothing was due" and "the scheduler is dead" look identical from the
        outside, and only one of them is an incident."""
        sweep_module.sweep(now=wednesday(9, 30))
        assert SweepHeartbeat.objects.get().last_swept_at == wednesday(9, 30)

    def test_the_heartbeat_age_reports_the_gap(self, running_clock):
        sweep_module.sweep(now=wednesday(9, 30))
        age = sweep_module.heartbeat_age_seconds(now=wednesday(9, 34))
        assert age == pytest.approx(240, abs=1)

    def test_a_sweep_that_has_never_run_reports_none_not_zero(self):
        """None is the more dangerous answer, not the safer one: a deployment
        where beat never started has an API that is up, a dashboard that renders,
        and no clock at all."""
        assert sweep_module.heartbeat_age_seconds() is None

    def test_the_alert_threshold_would_fire_within_its_budget(self, running_clock):
        """§11.4 gives the dead-man's switch a three-minute budget. This asserts
        the signal an alert rule reads, so a change that stops the heartbeat
        advancing fails here rather than during an outage."""
        sweep_module.sweep(now=wednesday(9, 30))

        assert sweep_module.heartbeat_age_seconds(now=wednesday(9, 32)) < 180
        assert sweep_module.heartbeat_age_seconds(now=wednesday(9, 34)) > 180


class TestReconciliation:
    def test_a_clean_installation_reconciles(self, running_clock):
        from disputeshield.sla.reconcile import reconcile

        result = reconcile()
        assert result.ok
        assert result.checked > 0

    def test_a_tampered_deadline_is_reported_not_repaired(self, running_clock, tenant_a, as_tenant):
        """ADR-0007 accepts that deadlines live in two places. Divergence is
        reported rather than silently fixed: a mismatch may be a bug or an owed
        backfill, and rewriting the row destroys the evidence needed to tell."""
        from disputeshield.sla.reconcile import reconcile

        with as_tenant(tenant_a):
            deadline = running_clock.deadlines.get(kind=SLADeadline.Kind.RESOLUTION)
            original = deadline.fires_at
            deadline.fires_at = original + timedelta(hours=5)
            deadline.save(update_fields=["fires_at"])

        result = reconcile()
        assert not result.ok
        (mismatch,) = [m for m in result.mismatches if m.kind == "resolution"]
        assert mismatch.drift == timedelta(hours=-5)

        with as_tenant(tenant_a):
            deadline.refresh_from_db()
        assert deadline.fires_at == original + timedelta(hours=5), "reconcile must not repair"


class TestSweepCommand:
    """§11.5 step 4 is a shell command. A runbook step nobody has executed against
    the code is a document, not a procedure."""

    def test_a_dry_run_reports_without_firing(self, running_clock, tenant_a, as_tenant):
        import io

        from django.core.management import call_command

        out = io.StringIO()
        call_command(
            "disputeshield_sweep", "--dry-run", "--to", wednesday(18).isoformat(), stdout=out
        )

        assert "deadline(s) due" in out.getvalue()
        with as_tenant(tenant_a):
            assert running_clock.deadlines.filter(fired_at__isnull=False).count() == 0
            assert NotificationOutbox.objects.count() == 0

    def test_catch_up_replays_the_window_and_names_the_next_step(self, running_clock):
        import io

        from django.core.management import call_command

        out = io.StringIO()
        call_command(
            "disputeshield_sweep", "--catch-up", "--to", wednesday(18).isoformat(), stdout=out
        )

        assert "fired" in out.getvalue()
        assert "documented cause" in out.getvalue()

    def test_running_it_twice_pages_nobody_twice(self, running_clock, tenant_a, as_tenant):
        from django.core.management import call_command

        for _ in range(2):
            call_command("disputeshield_sweep", "--to", wednesday(18).isoformat())

        with as_tenant(tenant_a):
            keys = list(NotificationOutbox.objects.values_list("idempotency_key", flat=True))
        assert len(keys) == len(set(keys))

    def test_a_naive_instant_is_refused(self, running_clock):
        from django.core.management import call_command

        with pytest.raises(ValueError, match="timezone offset"):
            call_command("disputeshield_sweep", "--to", "2026-08-19T18:00:00")
