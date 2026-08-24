"""Queue whatever the monthly report schedules owe.

Celery beat calls the task hourly. This command exists for the same reason
`disputeshield_sweep` does: the moment it matters most is when the scheduler has
been down and somebody has to catch up the months it missed while a supervisor is
already asking where the report is.

It is safe to run repeatedly, and that safety is structural rather than a matter
of care. A month stops being owed only when a delivery for it is confirmed
`sent`, and each delivery carries an idempotency key derived from the period, the
recipients and the attempt — so running this ten times cannot mail a period
twice.
"""

from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from disputeshield.reports import schedules


class Command(BaseCommand):
    help = "Queue due monthly regulatory exports. Idempotent; safe to replay."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--as-at", dest="as_at", help="Run as at this ISO instant.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what is owed without queueing or sending anything.",
        )

    def handle(self, *args, **options) -> None:
        now = _parse(options.get("as_at")) or timezone.now()

        if options["dry_run"]:
            self._report_owed(now)
            return

        result = schedules.run_due(now=now)
        self.stdout.write(
            f"queued {result.queued}, retried {result.retried}, "
            f"confirmed {result.already_sent}, abandoned {result.abandoned}, "
            f"blocked {result.skipped_inactive}"
        )
        if result.abandoned or result.skipped_inactive:
            # Not a silent count in a log line. These two mean a report did not
            # go out, which is the failure the schedule exists to prevent.
            self.stderr.write(
                self.style.ERROR(
                    f"{result.abandoned} period(s) abandoned and {result.skipped_inactive} "
                    "blocked — see docs/runbook-report-delivery.md"
                )
            )

    def _report_owed(self, now: datetime) -> None:
        from disputeshield.models import ReportSchedule
        from disputeshield.tenancy.platform import for_each_tenant

        def owed_for_tenant(_tenant_id):
            return [
                (
                    schedule.pk,
                    schedule.name,
                    [m.isoformat() for m in schedules.periods_owed(schedule, now=now)],
                )
                for schedule in ReportSchedule.objects.filter(is_active=True)
            ]

        for rows in for_each_tenant(owed_for_tenant):
            for schedule_id, name, months in rows:
                self.stdout.write(f"{schedule_id} {name}: {', '.join(months) or 'nothing owed'}")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else timezone.make_aware(moment)
