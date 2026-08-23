"""Run the SLA sweep by hand. This is §11.5 step 4.

Ordinarily Celery beat calls the task once a minute. This command exists for the
one moment it matters most: the scheduler stalled, clocks stopped advancing, and
an operator has to replay the window it missed while the incident is still open.

It is safe to run, and that safety is structural rather than a matter of care.
Unfired deadline rows with a past `fires_at` *are* the missed notifications
(ADR-0007), and each notification carries an idempotency key derived from what it
is about rather than when it was generated — so a replay cannot page anyone twice.
"""

from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from disputeshield.sla import sweeper


class Command(BaseCommand):
    help = "Fire due SLA deadlines. Idempotent; safe to replay over an outage window."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--catch-up",
            action="store_true",
            help="Replay an outage window. Identical behaviour — the flag exists so "
            "the runbook step and the shell history say what was being done.",
        )
        parser.add_argument("--to", dest="until", help="Sweep as at this ISO instant.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would fire without firing it or notifying anyone.",
        )

    def handle(self, *args, **options) -> None:
        now = _parse(options.get("until")) or timezone.now()

        if options["dry_run"]:
            from disputeshield.models import SLAClock, SLADeadline

            due = (
                SLADeadline.objects.all_tenants()
                .filter(fired_at__isnull=True, fires_at__lte=now)
                .exclude(clock__state=SLAClock.State.STOPPED)
                .count()
            )
            self.stdout.write(f"{due} deadline(s) due as at {now.isoformat()} (dry run)")
            return

        result = sweeper.sweep(now=now)
        self.stdout.write(
            self.style.SUCCESS(
                f"fired {result.fired}, notifications {result.notifications_created}, "
                f"{result.duration_ms}ms"
            )
        )
        if options["catch_up"] and result.fired:
            self.stdout.write(
                "Next: annotate every breach in the gap with its systems cause "
                "(§11.5 step 5). A breach with a documented cause is defensible; "
                "an unexplained one is not."
            )


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError("--to must carry a timezone offset; a naive instant is ambiguous.")
    return moment
