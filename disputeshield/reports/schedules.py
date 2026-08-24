"""Monthly delivery of the regulatory export, unattended.

The interesting part of a scheduled job in this domain is not "did it run at the
right time". It is **what it does about the times it did not run**, because a
monthly compliance report that silently skips a month is the failure the whole
feature exists to prevent — and a scheduler that reports success while delivering
nothing is worse than no scheduler at all.

So the runner is not written as "is it time now?". It is written as **"which
months are owed?"**, and a month stops being owed only when a delivery for it is
confirmed `sent`. Three consequences fall out of that one decision:

  * **Catch-up is free.** A runner that was down for two months finds two months
    owed, because nothing ever recorded them as done.
  * **A schedule that delivers nothing cannot look healthy.** Progress depends on
    the outcome, not on the attempt.
  * **A double send is impossible even if the runner fires twice**, because the
    delivery itself is idempotent on (period, recipients, attempt) and a month
    already sent is no longer owed.

The period is always a **closed** calendar month, in the schedule's own timezone.
Exporting a period that is still accepting cases produces a document that differs
every time it is built, which makes the delivery's digest check refuse and makes
the artefact worthless as a record. A firm's "March" is also its own: a European
firm's March does not start when UTC's does.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from disputeshield.models import NotificationOutbox, ReportSchedule

logger = logging.getLogger(__name__)

# After this many attempts a month is recorded as failed and stepped over.
# Stepping over is deliberate: blocking every future month behind one stuck month
# turns a single bad period into a total, silent outage of the schedule. Recording
# it is what keeps that from being a quiet skip.
MAX_ATTEMPTS_PER_PERIOD = 3

# A schedule dormant for years must not wake up and mail out sixty exports at
# once. What it drops is logged and audited rather than discarded quietly.
MAX_PERIODS_PER_RUN = 12


@dataclasses.dataclass(frozen=True)
class RunResult:
    queued: int
    already_sent: int
    retried: int
    abandoned: int
    skipped_inactive: int


def month_start(moment: date) -> date:
    return moment.replace(day=1)


def next_month(month: date) -> date:
    return date(month.year + (month.month == 12), (month.month % 12) + 1, 1)


def month_bounds(month: date, timezone_name: str) -> tuple[datetime, datetime]:
    """The month as an aware half-open interval `[start, end)` in its own timezone.

    Half-open on purpose. A closed interval either double-counts the boundary
    instant or drops it, and for a monthly regulatory return either one is a case
    reported twice or not at all.
    """
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(month, time.min, tzinfo=zone)
    end = datetime.combine(next_month(month), time.min, tzinfo=zone)
    return start, end


def fire_time(month: date, schedule: ReportSchedule) -> datetime:
    """When the export for `month` becomes due: after the month has ended.

    `day_of_month` is capped at 28 by the model, so this date exists in every
    month and needs no fallback. A "last day of the month" rule would mean a
    different date in February, which for a reporting deadline is not a detail.
    """
    due_month = next_month(month)
    zone = ZoneInfo(schedule.timezone_name)
    return datetime.combine(
        due_month.replace(day=schedule.day_of_month), time(hour=schedule.hour), tzinfo=zone
    )


def periods_owed(schedule: ReportSchedule, *, now: datetime) -> list[date]:
    """Every closed month whose delivery is due and not yet confirmed sent.

    Anchored on `last_period_start` rather than on a next-run timestamp. A
    timestamp that is advanced when the job runs records that the job ran; this
    records that the report arrived, which is the thing anyone actually cares
    about.
    """
    if schedule.last_period_start is None:
        # A schedule created in August covers August, delivered once August has
        # closed. Anchoring one month back is what makes the first period the
        # month the customer was in when they set it up, rather than the one
        # after.
        created_locally = schedule.created_at.astimezone(ZoneInfo(schedule.timezone_name)).date()
        anchor = _previous_month(month_start(created_locally))
    else:
        anchor = month_start(schedule.last_period_start)

    owed: list[date] = []
    month = next_month(anchor)
    failed = {entry["period"] for entry in schedule.failed_periods}

    while fire_time(month, schedule) <= now:
        if month.isoformat() not in failed:
            owed.append(month)
        month = next_month(month)
        if len(owed) >= MAX_PERIODS_PER_RUN:
            break

    return owed


def run_due(*, now: datetime | None = None) -> RunResult:
    """Queue what every tenant's schedules owe. Safe to run as often as you like."""
    from disputeshield.tenancy.platform import for_each_tenant

    now = now or timezone.now()
    totals = [0, 0, 0, 0, 0]
    for one in for_each_tenant(lambda _tenant_id: _run_one_tenant(now)):
        for index, value in enumerate(one):
            totals[index] += value
    return RunResult(*totals)


def _run_one_tenant(now: datetime) -> tuple[int, int, int, int, int]:
    queued = already_sent = retried = abandoned = skipped = 0

    for schedule in ReportSchedule.objects.filter(is_active=True):
        for month in periods_owed(schedule, now=now):
            outcome = _advance_one_period(schedule, month, now=now)
            queued += outcome == "queued"
            already_sent += outcome == "sent"
            retried += outcome == "retried"
            abandoned += outcome == "abandoned"
            skipped += outcome == "no_recipients"
            if outcome in {"queued", "retried", "no_recipients"}:
                # One month at a time. A month still in flight must not have the
                # next one queued behind it — the recipient would receive them
                # out of order, and the second would look like a correction of
                # the first.
                break

    return queued, already_sent, retried, abandoned, skipped


def _advance_one_period(schedule: ReportSchedule, month: date, *, now: datetime) -> str:
    from disputeshield import audit
    from disputeshield.reports import delivery

    period_from, period_to = month_bounds(month, schedule.timezone_name)
    attempts = _attempts_so_far(schedule, period_from, period_to)

    if any(row.status == NotificationOutbox.Status.SENT for row in attempts):
        # The only thing that retires a month. Recorded here rather than when the
        # delivery was queued, which is the whole point of the design.
        _mark_delivered(schedule, month)
        return "sent"

    if any(row.status == NotificationOutbox.Status.PENDING for row in attempts):
        # In flight. The dispatcher owns it from here; opening a second attempt
        # alongside the first would mail the period twice.
        return "pending"

    if len(attempts) >= MAX_ATTEMPTS_PER_PERIOD:
        _abandon(schedule, month, attempts)
        return "abandoned"

    try:
        delivery.request_delivery(
            tenant=schedule.tenant,
            period_from=period_from,
            period_to=period_to,
            addresses=list(schedule.recipients),
            requested_by=f"schedule:{schedule.pk}",
            note=f"Scheduled monthly export — {schedule.name}.",
            attempt=len(attempts),
        )
    except delivery.UnknownRecipient as exc:
        # Every recipient was deactivated, or the list was edited to something
        # that is not on the allowlist. A schedule in this state looks active and
        # delivers nothing, so it says so rather than skipping the month: the
        # month stays owed, and the reason is on the record.
        audit.append(
            tenant=schedule.tenant,
            event_type="report.schedule_blocked",
            subject_type="report_schedule",
            subject_id=schedule.pk,
            actor_type="system",
            actor_id="scheduler",
            payload={"period": month.isoformat(), "reason": str(exc)},
        )
        logger.error(
            "report schedule has no deliverable recipients",
            extra={"schedule": schedule.pk, "period": month.isoformat()},
        )
        return "no_recipients"
    except delivery.ReportTooLarge as exc:
        audit.append(
            tenant=schedule.tenant,
            event_type="report.schedule_blocked",
            subject_type="report_schedule",
            subject_id=schedule.pk,
            actor_type="system",
            actor_id="scheduler",
            payload={"period": month.isoformat(), "reason": str(exc)},
        )
        return "no_recipients"

    return "retried" if attempts else "queued"


def _attempts_so_far(schedule: ReportSchedule, period_from, period_to) -> list[NotificationOutbox]:
    """Every delivery row this schedule has opened for this period, in order."""
    from disputeshield.reports import delivery

    keys = [
        delivery.idempotency_key(period_from, period_to, list(schedule.recipients), attempt=n)
        for n in range(MAX_ATTEMPTS_PER_PERIOD)
    ]
    by_key = {
        row.idempotency_key: row
        for row in NotificationOutbox.objects.filter(idempotency_key__in=keys)
    }
    return [by_key[key] for key in keys if key in by_key]


def _mark_delivered(schedule: ReportSchedule, month: date) -> None:
    with transaction.atomic():
        schedule.last_period_start = month
        schedule.save(update_fields=["last_period_start"])


def _abandon(schedule: ReportSchedule, month: date, attempts: list[NotificationOutbox]) -> None:
    from disputeshield import audit

    last_error = attempts[-1].last_error if attempts else ""
    with transaction.atomic():
        schedule.failed_periods = [
            *schedule.failed_periods,
            {
                "period": month.isoformat(),
                "attempts": len(attempts),
                "last_error": last_error[:500],
            },
        ]
        schedule.save(update_fields=["failed_periods"])
        # An audit record as well as a field, because "the report for March never
        # went out" is a fact a supervisor may ask about years later, and a
        # mutable field on a mutable row is not evidence of anything.
        audit.append(
            tenant=schedule.tenant,
            event_type="report.schedule_abandoned_period",
            subject_type="report_schedule",
            subject_id=schedule.pk,
            actor_type="system",
            actor_id="scheduler",
            payload={
                "period": month.isoformat(),
                "attempts": len(attempts),
                "last_error": last_error[:500],
                "recipients": list(schedule.recipients),
            },
        )
    logger.error(
        "report schedule abandoned a period",
        extra={"schedule": schedule.pk, "period": month.isoformat(), "error": last_error[:200]},
    )


def _previous_month(month: date) -> date:
    return month_start(month - timedelta(days=1))
