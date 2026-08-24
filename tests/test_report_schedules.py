"""Monthly delivery of the regulatory export.

The interesting question about a scheduled compliance job is not whether it fires
at the right time. It is what it does about the times it did not fire, because a
monthly report that silently skips a month is the failure the feature exists to
prevent — and a scheduler that reports success while delivering nothing is worse
than no scheduler at all.

So most of this file is about the awkward cases: a runner that was down for two
months, a month that keeps failing, recipients that were deactivated after the
schedule was created, and the same runner firing twice.

Every address here is under a reserved domain, as in `test_report_delivery.py`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from django.core import mail
from django.utils import timezone

from disputeshield.models import Agent, NotificationOutbox, ReportRecipient, ReportSchedule
from disputeshield.notifications import dispatcher
from disputeshield.reports import schedules

pytestmark = pytest.mark.django_db

ALLOWED = "compliance@example.test"
ALSO_ALLOWED = "supervision@example.test"

CREATED_AT = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
MARCH = date(2026, 3, 1)
APRIL = date(2026, 4, 1)
MAY = date(2026, 5, 1)

# March's export is due on 5 April; April's on 5 May.
AFTER_MARCH_IS_DUE = datetime(2026, 4, 5, 9, 0, tzinfo=UTC)
AFTER_APRIL_IS_DUE = datetime(2026, 5, 5, 9, 0, tzinfo=UTC)


@pytest.fixture
def a_scheduled_tenant(tenant_a, as_tenant):
    with as_tenant(tenant_a):
        for address in (ALLOWED, ALSO_ALLOWED):
            ReportRecipient.objects.create(
                tenant=tenant_a,
                address=address,
                label="Sample",
                added_by="agt_1",
                reason="Sample recipient for tests.",
            )
        schedule = ReportSchedule.objects.create(
            tenant=tenant_a,
            name="Monthly supervisory export",
            recipients=[ALLOWED],
            day_of_month=5,
            hour=6,
            timezone_name="UTC",
            created_by="agt_1",
            reason="Standing supervisory arrangement.",
        )
        # `created_at` is auto_now_add; the schedule's first owed period is
        # anchored on it, so the tests pin it rather than depending on today.
        ReportSchedule.objects.filter(pk=schedule.pk).update(created_at=CREATED_AT)
        schedule.refresh_from_db()
    return tenant_a, schedule


class TestWhichMonthsAreOwed:
    def test_the_current_month_is_never_owed(self, a_scheduled_tenant, as_tenant):
        """An export of a period still accepting cases changes every time it is
        built, which makes the delivery refuse and the document worthless."""
        tenant, schedule = a_scheduled_tenant
        with as_tenant(tenant):
            owed = schedules.periods_owed(schedule, now=datetime(2026, 4, 20, tzinfo=UTC))
        assert APRIL not in owed

    def test_a_month_is_owed_only_after_its_due_date(self, a_scheduled_tenant, as_tenant):
        tenant, schedule = a_scheduled_tenant
        with as_tenant(tenant):
            before = schedules.periods_owed(schedule, now=datetime(2026, 4, 4, 23, tzinfo=UTC))
            after = schedules.periods_owed(schedule, now=AFTER_MARCH_IS_DUE)
        assert before == []
        assert after == [MARCH]

    def test_the_first_period_is_the_month_the_schedule_was_created_in(
        self, a_scheduled_tenant, as_tenant
    ):
        """Created in March means the first report covers March, not April."""
        tenant, schedule = a_scheduled_tenant
        with as_tenant(tenant):
            assert schedules.periods_owed(schedule, now=AFTER_MARCH_IS_DUE) == [MARCH]

    def test_two_missed_months_are_both_owed(self, a_scheduled_tenant, as_tenant):
        """Catch-up falls out of asking what is owed rather than what is due now."""
        tenant, schedule = a_scheduled_tenant
        with as_tenant(tenant):
            assert schedules.periods_owed(schedule, now=AFTER_APRIL_IS_DUE) == [MARCH, APRIL]

    def test_a_delivered_month_is_no_longer_owed(self, a_scheduled_tenant, as_tenant):
        tenant, schedule = a_scheduled_tenant
        schedule.last_period_start = MARCH
        with as_tenant(tenant):
            schedule.save(update_fields=["last_period_start"])
            assert schedules.periods_owed(schedule, now=AFTER_APRIL_IS_DUE) == [APRIL]

    def test_the_month_is_the_schedules_own_not_utcs(self, a_scheduled_tenant, as_tenant):
        """A firm's March does not start when UTC's does."""
        tenant, schedule = a_scheduled_tenant
        schedule.timezone_name = "Pacific/Kiritimati"  # UTC+14
        with as_tenant(tenant):
            schedule.save(update_fields=["timezone_name"])
        start, end = schedules.month_bounds(MARCH, "Pacific/Kiritimati")

        assert start.isoformat() == "2026-03-01T00:00:00+14:00"
        assert end.isoformat() == "2026-04-01T00:00:00+14:00"
        # Half-open: the instant April begins belongs to April.
        assert start < end

    def test_a_dormant_schedule_does_not_wake_up_and_mail_a_decade(
        self, a_scheduled_tenant, as_tenant
    ):
        tenant, schedule = a_scheduled_tenant
        with as_tenant(tenant):
            owed = schedules.periods_owed(schedule, now=datetime(2036, 1, 5, 9, tzinfo=UTC))
        assert len(owed) == schedules.MAX_PERIODS_PER_RUN


class TestRunning:
    def test_a_due_month_is_queued_and_delivered(self, a_scheduled_tenant, as_tenant):
        _tenant, _schedule = a_scheduled_tenant

        result = schedules.run_due(now=AFTER_MARCH_IS_DUE)
        assert result.queued == 1

        dispatcher.dispatch()
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [ALLOWED]
        assert "2026-03-01 to 2026-04-01" in mail.outbox[0].subject

    def test_a_month_is_marked_delivered_only_once_it_was_sent(self, a_scheduled_tenant, as_tenant):
        """Progress depends on the outcome, not on the attempt.

        A schedule that queues twelve exports a year and delivers none must not be
        able to look healthy.
        """
        tenant, schedule = a_scheduled_tenant

        schedules.run_due(now=AFTER_MARCH_IS_DUE)
        with as_tenant(tenant):
            schedule.refresh_from_db()
        assert schedule.last_period_start is None, "queued is not delivered"

        dispatcher.dispatch()
        schedules.run_due(now=AFTER_MARCH_IS_DUE)
        with as_tenant(tenant):
            schedule.refresh_from_db()
        assert schedule.last_period_start == MARCH

    def test_running_twice_does_not_send_twice(self, a_scheduled_tenant, as_tenant):
        _tenant, _schedule = a_scheduled_tenant

        schedules.run_due(now=AFTER_MARCH_IS_DUE)
        schedules.run_due(now=AFTER_MARCH_IS_DUE)
        dispatcher.dispatch()
        dispatcher.dispatch()

        assert len(mail.outbox) == 1

    def test_missed_months_go_out_in_order_one_at_a_time(self, a_scheduled_tenant, as_tenant):
        """Two months owed must not both be queued at once.

        The recipient would receive them out of order, and the second would read
        as a correction of the first.
        """
        tenant, schedule = a_scheduled_tenant

        schedules.run_due(now=AFTER_APRIL_IS_DUE)
        with as_tenant(tenant):
            assert NotificationOutbox.objects.filter(event_type="report.regulatory").count() == 1
        dispatcher.dispatch()
        assert "2026-03-01" in mail.outbox[0].subject

        schedules.run_due(now=AFTER_APRIL_IS_DUE)
        dispatcher.dispatch()
        assert "2026-04-01" in mail.outbox[1].subject

        schedules.run_due(now=AFTER_APRIL_IS_DUE)
        with as_tenant(tenant):
            schedule.refresh_from_db()
        assert schedule.last_period_start == APRIL

    def test_an_inactive_schedule_does_nothing(self, a_scheduled_tenant, as_tenant):
        tenant, schedule = a_scheduled_tenant
        with as_tenant(tenant):
            schedule.is_active = False
            schedule.save(update_fields=["is_active"])

        assert schedules.run_due(now=AFTER_APRIL_IS_DUE).queued == 0
        assert not mail.outbox

    def test_another_tenants_schedule_is_not_visible(self, a_scheduled_tenant, tenant_b, as_tenant):
        tenant, _schedule = a_scheduled_tenant
        with as_tenant(tenant_b):
            assert not ReportSchedule.objects.exists()
        with as_tenant(tenant):
            assert ReportSchedule.objects.exists()


class TestWhenItCannotDeliver:
    def test_deactivating_every_recipient_blocks_rather_than_skips(
        self, a_scheduled_tenant, as_tenant
    ):
        """A schedule in this state looks active and delivers nothing.

        The month has to stay owed and the reason has to be on the record, or the
        firm finds out when a supervisor asks.
        """
        from disputeshield.models import AuditRecord

        tenant, schedule = a_scheduled_tenant
        with as_tenant(tenant):
            ReportRecipient.objects.filter(address=ALLOWED).update(is_active=False)

        result = schedules.run_due(now=AFTER_MARCH_IS_DUE)

        assert result.queued == 0
        assert result.skipped_inactive == 1
        assert not mail.outbox
        with as_tenant(tenant):
            schedule.refresh_from_db()
            assert schedule.last_period_start is None, "the month is still owed"
            assert AuditRecord.objects.filter(event_type="report.schedule_blocked").exists()
            assert schedules.periods_owed(schedule, now=AFTER_MARCH_IS_DUE) == [MARCH]

    def test_a_failed_delivery_is_retried_with_a_fresh_promise(self, a_scheduled_tenant, as_tenant):
        """A parked delivery's promise is spent: the bundle it described is gone.

        The schedule opens a new delivery rather than editing the old one's
        recorded promise, so the trail reads as two attempts.
        """
        tenant, _schedule = a_scheduled_tenant

        schedules.run_due(now=AFTER_MARCH_IS_DUE)
        with as_tenant(tenant):
            NotificationOutbox.objects.filter(event_type="report.regulatory").update(
                status=NotificationOutbox.Status.FAILED, last_error="BundleChanged: ..."
            )

        result = schedules.run_due(now=AFTER_MARCH_IS_DUE)

        assert result.retried == 1
        with as_tenant(tenant):
            rows = NotificationOutbox.objects.filter(event_type="report.regulatory")
            assert rows.count() == 2
            assert len({row.idempotency_key for row in rows}) == 2

    def test_a_period_is_abandoned_after_its_attempts_and_recorded(
        self, a_scheduled_tenant, as_tenant
    ):
        from disputeshield.models import AuditRecord

        tenant, schedule = a_scheduled_tenant

        for _ in range(schedules.MAX_ATTEMPTS_PER_PERIOD):
            schedules.run_due(now=AFTER_MARCH_IS_DUE)
            with as_tenant(tenant):
                NotificationOutbox.objects.filter(event_type="report.regulatory").update(
                    status=NotificationOutbox.Status.FAILED, last_error="BundleChanged: ..."
                )

        result = schedules.run_due(now=AFTER_MARCH_IS_DUE)

        assert result.abandoned == 1
        with as_tenant(tenant):
            schedule.refresh_from_db()
            assert [entry["period"] for entry in schedule.failed_periods] == ["2026-03-01"]
            assert AuditRecord.objects.filter(
                event_type="report.schedule_abandoned_period"
            ).exists()

    def test_an_abandoned_month_does_not_block_the_next_one(self, a_scheduled_tenant, as_tenant):
        """One stuck month must not become a silent, total outage of the schedule."""
        tenant, schedule = a_scheduled_tenant
        with as_tenant(tenant):
            schedule.failed_periods = [{"period": "2026-03-01", "attempts": 3, "last_error": "x"}]
            schedule.save(update_fields=["failed_periods"])

        result = schedules.run_due(now=AFTER_APRIL_IS_DUE)
        dispatcher.dispatch()

        assert result.queued == 1
        assert "2026-04-01" in mail.outbox[0].subject


class TestThroughTheApi:
    @pytest.fixture
    def compliance_client(self, client_for, make_agent):
        def _make(tenant):
            officer = make_agent(tenant, email="adaeze@example.com", role=Agent.Role.COMPLIANCE)
            return client_for(tenant, agent=officer)

        return _make

    def test_compliance_creates_a_schedule(self, a_scheduled_tenant, compliance_client):
        tenant, _schedule = a_scheduled_tenant
        response = compliance_client(tenant).post(
            "/v1/reports/schedules",
            {
                "name": "Quarterly board pack feed",
                "recipients": [ALSO_ALLOWED],
                "day_of_month": 3,
                "hour": 7,
                "timezone": "Europe/London",
                "reason": "Standing arrangement with the supervisor.",
            },
            format="json",
        )

        assert response.status_code == 201
        body = response.json()
        assert body["recipients"] == [ALSO_ALLOWED]
        assert body["timezone"] == "Europe/London"
        assert body["last_period_delivered"] is None

    def test_a_day_that_does_not_exist_in_every_month_is_refused(
        self, a_scheduled_tenant, compliance_client
    ):
        """Sliding silently to the last day makes a deadline mean a different date
        in February."""
        tenant, _schedule = a_scheduled_tenant
        response = compliance_client(tenant).post(
            "/v1/reports/schedules",
            {"name": "x", "recipients": [ALLOWED], "day_of_month": 31, "reason": "x"},
            format="json",
        )
        assert response.status_code == 400
        assert "February" in response.json()["error"]["message"]

    def test_an_unregistered_recipient_is_refused_at_creation(
        self, a_scheduled_tenant, compliance_client
    ):
        """A schedule that cannot deliver should fail while somebody is looking at
        it, not silently every month at 6am."""
        tenant, _schedule = a_scheduled_tenant
        response = compliance_client(tenant).post(
            "/v1/reports/schedules",
            {"name": "x", "recipients": ["nobody@example.invalid"], "reason": "x"},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["error"]["type"] == "recipient_not_allowed"

    def test_an_unknown_timezone_is_refused(self, a_scheduled_tenant, compliance_client):
        tenant, _schedule = a_scheduled_tenant
        response = compliance_client(tenant).post(
            "/v1/reports/schedules",
            {"name": "x", "recipients": [ALLOWED], "timezone": "Mars/Olympus", "reason": "x"},
            format="json",
        )
        assert response.status_code == 400

    def test_an_agent_cannot_create_a_schedule(self, a_scheduled_tenant, client_for, make_agent):
        """A standing instruction that a period leaves every month is the same
        decision as sending one, made once for every future month."""
        tenant, _schedule = a_scheduled_tenant
        agent = make_agent(tenant, email="ngozi@example.com", role=Agent.Role.AGENT)
        response = client_for(tenant, agent=agent).post(
            "/v1/reports/schedules",
            {"name": "x", "recipients": [ALLOWED], "reason": "x"},
            format="json",
        )
        assert response.status_code == 404

    def test_the_derived_state_the_dashboard_renders_comes_from_the_server(
        self, a_scheduled_tenant, compliance_client
    ):
        """`periods_owed` and `is_overdue` are computed by the same code the
        runner uses.

        A second implementation of the month arithmetic in the dashboard would
        eventually disagree with the one that actually sends the mail, and a
        dashboard saying a schedule is healthy while the runner thinks a month is
        owed is worse than no dashboard.
        """
        tenant, schedule = a_scheduled_tenant

        listed = compliance_client(tenant).get("/v1/reports/schedules").json()["data"]
        target = next(s for s in listed if s["id"] == schedule.pk)

        # The fixture's schedule was created in March 2026 and has delivered
        # nothing, so by now it owes months and is long past the grace window.
        assert target["periods_owed"], "the runner would find months owed"
        assert target["is_overdue"] is True
        assert target["last_period_delivered"] is None

    def test_an_inactive_schedule_owes_nothing(
        self, a_scheduled_tenant, compliance_client, as_tenant
    ):
        """A deactivated schedule is not behind; it is stopped.

        Reporting it as overdue would put a permanent alarm on the dashboard for
        a schedule somebody deliberately switched off.
        """
        tenant, schedule = a_scheduled_tenant
        with as_tenant(tenant):
            ReportSchedule.objects.filter(pk=schedule.pk).update(is_active=False)

        listed = compliance_client(tenant).get("/v1/reports/schedules").json()["data"]
        target = next(s for s in listed if s["id"] == schedule.pk)

        assert target["periods_owed"] == []
        assert target["is_overdue"] is False
        assert target["is_active"] is False

    def test_a_new_schedule_owes_nothing_yet(self, a_scheduled_tenant, compliance_client):
        """Its first period is the month it was created in, which has not closed."""
        tenant, _schedule = a_scheduled_tenant
        created = (
            compliance_client(tenant)
            .post(
                "/v1/reports/schedules",
                {"name": "Fresh", "recipients": [ALLOWED], "reason": "New arrangement."},
                format="json",
            )
            .json()
        )

        assert created["periods_owed"] == []
        assert created["is_overdue"] is False

    def test_failed_periods_are_surfaced_not_buried(
        self, a_scheduled_tenant, compliance_client, as_tenant
    ):
        tenant, schedule = a_scheduled_tenant
        with as_tenant(tenant):
            schedule.failed_periods = [{"period": "2026-03-01", "attempts": 3, "last_error": "x"}]
            schedule.save(update_fields=["failed_periods"])

        listed = compliance_client(tenant).get("/v1/reports/schedules").json()["data"]
        target = next(s for s in listed if s["id"] == schedule.pk)

        assert target["failed_periods"][0]["period"] == "2026-03-01"

    def test_deactivating_keeps_the_row(self, a_scheduled_tenant, compliance_client):
        tenant, schedule = a_scheduled_tenant
        client = compliance_client(tenant)

        response = client.delete(f"/v1/reports/schedules/{schedule.pk}")

        assert response.status_code == 200
        assert response.json()["is_active"] is False
        assert any(
            s["id"] == schedule.pk for s in client.get("/v1/reports/schedules").json()["data"]
        )


class TestTheCommand:
    def test_dry_run_reports_what_is_owed_without_sending(self, a_scheduled_tenant, as_tenant):
        import io

        from django.core.management import call_command

        tenant, _schedule = a_scheduled_tenant
        out = io.StringIO()
        call_command(
            "disputeshield_run_report_schedules",
            "--dry-run",
            "--as-at=2026-05-05T09:00:00Z",
            stdout=out,
        )

        assert "2026-03-01" in out.getvalue()
        assert not mail.outbox
        with as_tenant(tenant):
            assert not NotificationOutbox.objects.filter(event_type="report.regulatory").exists()


class TestTheDoctorCheck:
    """A schedule that looks active while nothing is running it.

    A deployment with the worker but no beat is a configuration a monthly report
    can hide in for a long time — the first person to notice is a supervisor
    asking where the return is.
    """

    def _report(self) -> str:
        import io

        from django.core.management import call_command

        out = io.StringIO()
        call_command("disputeshield_doctor", stdout=out)
        return next(line for line in out.getvalue().splitlines() if "report schedules" in line)

    def test_a_schedule_with_nothing_owed_passes(self, a_scheduled_tenant, as_tenant):
        tenant, schedule = a_scheduled_tenant
        with as_tenant(tenant):
            # Created this month, so its first period has not closed yet.
            ReportSchedule.objects.filter(pk=schedule.pk).update(created_at=timezone.now())
        assert self._report().startswith("ok")

    def test_a_long_overdue_schedule_fails(self, a_scheduled_tenant):
        """The fixture's schedule was created in March 2026 and never delivered."""
        line = self._report()
        assert line.startswith("FAIL")
        assert "run_schedules" in line

    def test_an_inactive_schedule_is_not_overdue(self, a_scheduled_tenant, as_tenant):
        tenant, schedule = a_scheduled_tenant
        with as_tenant(tenant):
            ReportSchedule.objects.filter(pk=schedule.pk).update(is_active=False)
        assert self._report().startswith("ok")
