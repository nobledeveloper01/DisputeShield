"""The clock's lifecycle: starting, pausing, resuming, and what each one records."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from disputeshield.models import AuditRecord, SLAClock, SLADeadline, SLAEvent
from disputeshield.sla import (
    ClockStateError,
    ReasonRequired,
    business_time_between,
)
from disputeshield.sla import (
    clock as clock_service,
)

pytestmark = pytest.mark.django_db

LAGOS = ZoneInfo("Africa/Lagos")
UTC = UTC


def wednesday(hour: int = 9, minute: int = 0) -> datetime:
    return datetime(2026, 8, 19, hour, minute, tzinfo=LAGOS).astimezone(UTC)


@pytest.fixture
def started(tenant_a, make_policy, as_tenant):
    version = make_policy(tenant_a, resolution_hours=8)
    with as_tenant(tenant_a):
        yield clock_service.start(
            tenant=tenant_a, subject_id="dsp_1", policy_version=version, started_at=wednesday(9)
        )


class TestStarting:
    def test_starting_materialises_every_deadline_the_policy_implies(
        self, started, tenant_a, as_tenant
    ):
        with as_tenant(tenant_a):
            kinds = sorted((d.kind, d.threshold_percent) for d in started.deadlines.all())
        assert kinds == [
            ("acknowledgement", None),
            ("resolution", None),
            ("warning", 50),
            ("warning", 80),
            ("warning", 95),
        ]

    def test_the_resolution_deadline_respects_business_hours(self, started, tenant_a, as_tenant):
        with as_tenant(tenant_a):
            resolution = started.deadlines.get(kind=SLADeadline.Kind.RESOLUTION)
        # Eight business hours from Wednesday 09:00 is Wednesday 17:00.
        assert resolution.fires_at.astimezone(LAGOS).hour == 17
        assert resolution.fires_at.astimezone(LAGOS).day == 19

    def test_warnings_are_percentages_of_business_time_not_wall_clock(
        self, started, tenant_a, as_tenant
    ):
        """A case filed Friday afternoon is not 60% consumed by Sunday, and
        reporting that it is would page somebody every weekend."""
        with as_tenant(tenant_a):
            fifty = started.deadlines.get(kind=SLADeadline.Kind.WARNING, threshold_percent=50)
        assert fifty.fires_at.astimezone(LAGOS).hour == 13  # four business hours in

    def test_starting_writes_an_audit_record(self, started, tenant_a, as_tenant):
        with as_tenant(tenant_a):
            record = AuditRecord.objects.get(event_type="sla.started")
        assert record.subject_id == "dsp_1"
        assert record.payload["clock_id"] == started.pk


class TestPauseDiscipline:
    def test_a_pause_without_a_reason_is_refused(self, started, tenant_a, as_tenant):
        with as_tenant(tenant_a), pytest.raises(ReasonRequired, match="abusable"):
            clock_service.pause(clock=started, reason="", actor_type="user", actor_id="agt_1")

    def test_a_whitespace_reason_is_not_a_reason(self, started, tenant_a, as_tenant):
        with as_tenant(tenant_a), pytest.raises(ReasonRequired):
            clock_service.pause(clock=started, reason="   ", actor_type="user", actor_id="agt_1")

    def test_a_resume_without_a_reason_is_refused(self, started, tenant_a, as_tenant):
        with as_tenant(tenant_a):
            clock_service.pause(
                clock=started, reason="awaiting proof of payment", actor_type="user", actor_id="a"
            )
            with pytest.raises(ReasonRequired):
                clock_service.resume(clock=started, reason="", actor_type="user", actor_id="a")

    def test_the_database_itself_refuses_a_reasonless_pause_event(
        self, started, tenant_a, as_tenant
    ):
        """The service guard is the ordinary path. This asserts the constraint
        underneath it, because a reason enforced only in a service is one
        refactor away from being optional."""
        from django.db import IntegrityError, transaction

        with as_tenant(tenant_a), pytest.raises(IntegrityError), transaction.atomic():
            SLAEvent.objects.create(
                tenant=tenant_a,
                clock=started,
                kind=SLAEvent.Kind.PAUSED,
                reason="",
                clock_remaining_seconds=0,
                occurred_at=wednesday(10),
            )

    def test_no_pause_path_exists_that_does_not_require_a_reason(self):
        """Introspects the service rather than calling it, so a future overload
        that skips the check fails here."""
        import inspect

        source = inspect.getsource(clock_service.pause)
        assert "ReasonRequired" in source
        signature = inspect.signature(clock_service.pause)
        assert signature.parameters["reason"].default is inspect.Parameter.empty, (
            "reason must be a required argument — a default makes it optional at "
            "every call site that forgets it"
        )


class TestPausingAndResuming:
    def test_a_pause_pushes_the_deadline_out_by_the_paused_business_time(
        self, started, tenant_a, as_tenant
    ):
        with as_tenant(tenant_a):
            before = started.deadlines.get(kind=SLADeadline.Kind.RESOLUTION).fires_at
            clock_service.pause(
                clock=started,
                reason="awaiting customer statement",
                actor_type="user",
                actor_id="agt_1",
                at=wednesday(10),
            )
            clock_service.resume(
                clock=started,
                reason="customer responded",
                actor_type="user",
                actor_id="agt_1",
                at=wednesday(12),
            )
            after = started.deadlines.get(kind=SLADeadline.Kind.RESOLUTION).fires_at
            calendar = clock_service.calendar_for(started)

        # The deadline moves by two hours of *business* time, which is eighteen
        # hours of wall clock here because the office closes in between. Asserting
        # on wall clock would be asserting the bug this engine exists to avoid.
        assert business_time_between(before, after, calendar) == timedelta(hours=2)

    def test_every_event_records_the_clock_state_at_that_moment(self, started, tenant_a, as_tenant):
        """The field that makes a breach explainable six months later: the record
        says not just that a case was paused, but how close to breaching it was."""
        with as_tenant(tenant_a):
            clock_service.pause(
                clock=started,
                reason="awaiting customer",
                actor_type="user",
                actor_id="agt_1",
                at=wednesday(10),
            )
            event = SLAEvent.objects.get(kind=SLAEvent.Kind.PAUSED)
        # Seven of the eight business hours remained when it was paused.
        assert event.clock_remaining_seconds == 7 * 3600

    def test_pausing_a_paused_clock_is_refused(self, started, tenant_a, as_tenant):
        with as_tenant(tenant_a):
            clock_service.pause(clock=started, reason="first", actor_type="user", actor_id="a")
            with pytest.raises(ClockStateError, match="paused"):
                clock_service.pause(clock=started, reason="again", actor_type="user", actor_id="a")

    def test_resuming_a_running_clock_is_refused(self, started, tenant_a, as_tenant):
        with as_tenant(tenant_a), pytest.raises(ClockStateError, match="running"):
            clock_service.resume(
                clock=started, reason="nothing to resume", actor_type="user", actor_id="a"
            )

    def test_pause_intervals_accumulate_and_are_all_subtracted(self, started, tenant_a, as_tenant):
        with as_tenant(tenant_a):
            before = started.deadlines.get(kind=SLADeadline.Kind.RESOLUTION).fires_at
            for pause_at, resume_at in ((10, 11), (13, 14)):
                clock_service.pause(
                    clock=started,
                    reason="awaiting",
                    actor_type="user",
                    actor_id="a",
                    at=wednesday(pause_at),
                )
                clock_service.resume(
                    clock=started,
                    reason="received",
                    actor_type="user",
                    actor_id="a",
                    at=wednesday(resume_at),
                )
            after = started.deadlines.get(kind=SLADeadline.Kind.RESOLUTION).fires_at
            calendar = clock_service.calendar_for(started)

        assert business_time_between(before, after, calendar) == timedelta(hours=2)
        assert len(started.paused_intervals) == 2

    def test_stopping_removes_pending_deadlines_but_keeps_history(
        self, started, tenant_a, as_tenant
    ):
        with as_tenant(tenant_a):
            clock_service.stop(clock=started, actor_type="user", actor_id="agt_1")
            assert started.state == SLAClock.State.STOPPED
            assert started.deadlines.filter(fired_at__isnull=True).count() == 0
            assert started.events.filter(kind=SLAEvent.Kind.STOPPED).exists()


class TestPolicyVersionImmutability:
    def test_a_policy_version_cannot_be_edited(self, tenant_a, make_policy, as_tenant):
        """ADR-0004. Editing would retroactively change the standard every open
        case is judged against, and the number the case actually ran on would
        stop existing anywhere."""
        version = make_policy(tenant_a)
        with as_tenant(tenant_a):
            version.resolution_hours = 48
            with pytest.raises(PermissionError, match="immutable"):
                version.save()
