"""Starting, pausing, resuming and stopping a regulatory clock.

Every function here writes an `SLAEvent` and an audit record in the same
transaction as the state change, so a clock that moved without a record — or a
record for a move that rolled back — are both impossible rather than unlikely.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from disputeshield import audit
from disputeshield.models import SLAClock, SLADeadline, SLAEvent
from disputeshield.sla.calendar import BusinessCalendar
from disputeshield.sla.deadlines import compute_deadline


class ReasonRequired(ValueError):
    """A pause or resume without a reason.

    §4.4/C3 makes the reason mandatory because a pausable clock is an abusable
    clock: the reason is what turns "this case stopped counting" into something a
    supervisor can evaluate, and what makes excessive pausing visible per agent in
    the breach analysis view.
    """


class ClockStateError(RuntimeError):
    """A transition the state machine does not permit."""


def start(
    *,
    tenant,
    subject_id: str,
    policy_version,
    started_at: datetime | None = None,
    subject_type: str = "dispute",
    actor_type: str = "system",
    actor_id: str = "",
) -> SLAClock:
    """Begin the clock and materialise every deadline it implies (ADR-0007)."""
    started_at = started_at or timezone.now()

    with transaction.atomic():
        clock = SLAClock.objects.create(
            tenant=tenant,
            subject_type=subject_type,
            subject_id=subject_id,
            policy_version=policy_version,
            started_at=started_at,
        )
        _materialise_deadlines(clock)
        _record(
            clock,
            SLAEvent.Kind.STARTED,
            occurred_at=started_at,
            actor_type=actor_type,
            actor_id=actor_id,
            payload={"policy_version": policy_version.pk},
        )
        return clock


def pause(
    *, clock: SLAClock, reason: str, actor_type: str, actor_id: str = "", at=None
) -> SLAClock:
    """Stop the resolution clock while legitimately waiting on the customer."""
    if not reason or not reason.strip():
        raise ReasonRequired(
            "A pause requires a reason. Every pause is an audit record and feeds the "
            "pause-duration metric reported by agent — that is what keeps a pausable "
            "clock from being an abusable one (§4.4)."
        )
    if clock.state != SLAClock.State.RUNNING:
        raise ClockStateError(f"Cannot pause a clock that is {clock.state}.")

    at = at or timezone.now()
    with transaction.atomic():
        clock.state = SLAClock.State.PAUSED
        clock.paused_at = at
        clock.save(update_fields=["state", "paused_at"])
        _record(
            clock,
            SLAEvent.Kind.PAUSED,
            occurred_at=at,
            reason=reason,
            actor_type=actor_type,
            actor_id=actor_id,
        )
        return clock


def resume(
    *, clock: SLAClock, reason: str, actor_type: str, actor_id: str = "", at=None
) -> SLAClock:
    """Restart the clock and push every unfired deadline out by the paused time."""
    if not reason or not reason.strip():
        raise ReasonRequired("A resume requires a reason, for the same purpose as a pause.")
    if clock.state != SLAClock.State.PAUSED:
        raise ClockStateError(f"Cannot resume a clock that is {clock.state}.")

    at = at or timezone.now()
    with transaction.atomic():
        interval = [clock.paused_at.isoformat(), at.isoformat()]
        clock.paused_intervals = [*clock.paused_intervals, interval]
        clock.state = SLAClock.State.RUNNING
        clock.paused_at = None
        clock.save(update_fields=["state", "paused_at", "paused_intervals"])

        # Pause and resume are the only operations that move a deadline, so they
        # are the only place recomputation lives. Nothing recomputes implicitly.
        _materialise_deadlines(clock)
        _record(
            clock,
            SLAEvent.Kind.RESUMED,
            occurred_at=at,
            reason=reason,
            actor_type=actor_type,
            actor_id=actor_id,
            payload={
                "paused_seconds": int((at - datetime.fromisoformat(interval[0])).total_seconds())
            },
        )
        return clock


def stop(*, clock: SLAClock, actor_type: str, actor_id: str = "", at=None) -> SLAClock:
    """The case reached a terminal state. Unfired deadlines stop being due."""
    at = at or timezone.now()
    with transaction.atomic():
        clock.state = SLAClock.State.STOPPED
        clock.stopped_at = at
        clock.save(update_fields=["state", "stopped_at"])
        clock.deadlines.filter(fired_at__isnull=True).exclude(
            kind=SLADeadline.Kind.REOPEN_WINDOW
        ).delete()
        _record(
            clock, SLAEvent.Kind.STOPPED, occurred_at=at, actor_type=actor_type, actor_id=actor_id
        )
        return clock


# -- deadlines -----------------------------------------------------------------


def calendar_for(clock: SLAClock) -> BusinessCalendar:
    version = clock.policy_version
    if not version.business_hours_only:
        return BusinessCalendar.continuous(version.calendar.timezone_name)
    return BusinessCalendar.from_model(version.calendar)


def paused_intervals_of(
    clock: SLAClock, *, at: datetime | None = None
) -> tuple[tuple[datetime, datetime], ...]:
    """Closed pause intervals, plus the open one if the clock is paused right now.

    `at` is the instant being evaluated, and defaults to now. Passing it matters:
    an open interval measured to `now()` while evaluating a *historical* instant
    charges the clock for a pause that had not happened yet at that instant. The
    symptom is a clock that reports zero time remaining the moment it is paused,
    which is both wrong and exactly backwards.
    """
    at = at or timezone.now()
    intervals = [
        (datetime.fromisoformat(start), datetime.fromisoformat(end))
        for start, end in clock.paused_intervals
    ]
    if clock.state == SLAClock.State.PAUSED and clock.paused_at:
        intervals.append((clock.paused_at, max(clock.paused_at, at)))
    return tuple(intervals)


def _materialise_deadlines(clock: SLAClock, *, at: datetime | None = None) -> None:
    version = clock.policy_version
    calendar = calendar_for(clock)
    pauses = paused_intervals_of(clock, at=at)

    ack_window = timedelta(minutes=version.acknowledgement_minutes)
    resolution_window = timedelta(hours=version.resolution_hours)

    wanted: list[tuple[str, int | None, datetime]] = [
        (
            SLADeadline.Kind.ACKNOWLEDGEMENT,
            None,
            compute_deadline(clock.started_at, ack_window, calendar, pauses),
        ),
        (
            SLADeadline.Kind.RESOLUTION,
            None,
            compute_deadline(clock.started_at, resolution_window, calendar, pauses),
        ),
    ]

    for threshold in sorted(set(version.warning_thresholds)):
        if not 0 < threshold < 100:
            continue
        wanted.append(
            (
                SLADeadline.Kind.WARNING,
                threshold,
                compute_deadline(
                    clock.started_at, resolution_window * threshold / 100, calendar, pauses
                ),
            )
        )

    for kind, threshold, fires_at in wanted:
        existing = clock.deadlines.filter(
            kind=kind, threshold_percent=threshold, pausable=True
        ).first()
        if existing is None:
            SLADeadline.objects.create(
                tenant=clock.tenant,
                clock=clock,
                kind=kind,
                threshold_percent=threshold,
                fires_at=fires_at,
                pausable=True,
            )
        elif existing.fired_at is None and existing.fires_at != fires_at:
            # A fired deadline is history and is never moved. Moving one would
            # mean a breach that was recorded could later be un-recorded.
            existing.fires_at = fires_at
            existing.save(update_fields=["fires_at"])


def _record(
    clock: SLAClock,
    kind: str,
    *,
    occurred_at: datetime,
    reason: str = "",
    actor_type: str = "system",
    actor_id: str = "",
    payload: dict | None = None,
) -> SLAEvent:
    remaining = remaining_seconds(clock, at=occurred_at)
    event = SLAEvent.objects.create(
        tenant=clock.tenant,
        clock=clock,
        kind=kind,
        reason=reason,
        actor_type=actor_type,
        actor_id=actor_id,
        clock_remaining_seconds=remaining,
        occurred_at=occurred_at,
    )
    audit.append(
        tenant=clock.tenant,
        event_type=f"sla.{kind}",
        subject_type=clock.subject_type,
        subject_id=clock.subject_id,
        actor_type=actor_type,
        actor_id=actor_id,
        occurred_at=occurred_at,
        payload={
            "clock_id": clock.pk,
            "reason": reason,
            "clock_remaining_seconds": remaining,
            **(payload or {}),
        },
    )
    return event


def remaining_seconds(clock: SLAClock, *, at: datetime | None = None) -> int:
    """Business seconds left before the resolution deadline. Negative once breached."""
    from disputeshield.sla.deadlines import business_time_between

    at = at or timezone.now()
    deadline = clock.deadlines.filter(kind=SLADeadline.Kind.RESOLUTION).first()
    if deadline is None:
        return 0

    calendar = calendar_for(clock)
    pauses = paused_intervals_of(clock, at=at)
    if at <= deadline.fires_at:
        return int(business_time_between(at, deadline.fires_at, calendar, pauses).total_seconds())
    return -int(business_time_between(deadline.fires_at, at, calendar, pauses).total_seconds())
