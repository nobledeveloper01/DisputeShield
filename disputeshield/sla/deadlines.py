"""Deadline arithmetic — pure, side-effect free, and exhaustively tested.

§4.4 makes the case for keeping this pure, and it is worth restating because it
is the reason this file imports nothing from Django: *every subtle bug in this
function is a compliance breach that nobody notices until an auditor does.*

Two functions, deliberately independent of each other:

  * `compute_deadline` walks forward through business time and returns the instant
    at which a window expires.
  * `business_time_between` measures the business time in a span.

They are inverses, and the property test asserts exactly that round trip for
arbitrary calendars, starts, windows and pause intervals. Implementing the
measurement in terms of the walk would make the property vacuous — it would only
prove the code agrees with itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from disputeshield.sla.calendar import UTC, BusinessCalendar, Interval


class DeadlineUncomputable(RuntimeError):
    """The window cannot be satisfied within the calendar's search horizon."""


def compute_deadline(
    start: datetime,
    window: timedelta,
    calendar: BusinessCalendar,
    paused_intervals: tuple[tuple[datetime, datetime], ...] = (),
) -> datetime:
    """The instant at which `window` of business time has elapsed from `start`.

    Business time excludes non-business hours, closed weekdays, holidays and any
    interval during which the clock was paused. A pause does not extend the
    window by its own length — it removes that time from consideration entirely,
    which is the difference between "we were waiting on the customer" and "we
    took longer".

    All arithmetic is in UTC. Calendar boundaries are resolved in the calendar's
    own timezone, which is what makes the result independent of where the server
    happens to be running.
    """
    if window <= timedelta(0):
        raise ValueError(f"window must be positive, got {window}")
    _require_aware(start, "start")

    pauses = _normalise(paused_intervals)
    remaining = window

    for segment in calendar.segments_from(start):
        for usable in _subtract(segment, pauses):
            if usable.duration >= remaining:
                return usable.start + remaining
            remaining -= usable.duration

    raise DeadlineUncomputable(
        f"A {window} window from {start.isoformat()} does not complete within the "
        "calendar's 366-day horizon. Either the window is longer than a year of "
        "business time, or the calendar is open far less than it appears to be."
    )


def business_time_between(
    start: datetime,
    end: datetime,
    calendar: BusinessCalendar,
    paused_intervals: tuple[tuple[datetime, datetime], ...] = (),
) -> timedelta:
    """How much business time separates two instants. Zero if `end` precedes `start`.

    Written independently of `compute_deadline` on purpose: these two are each
    other's check, and a shared implementation would only prove the code is
    self-consistent.
    """
    _require_aware(start, "start")
    _require_aware(end, "end")
    if end <= start:
        return timedelta(0)

    pauses = _normalise(paused_intervals)
    span = Interval(start, end)
    total = timedelta(0)

    for segment in calendar.segments_from(start):
        if segment.start >= end:
            break
        within = segment.intersect(span)
        if within is None:
            continue
        for usable in _subtract(within, pauses):
            total += usable.duration

    return total


def elapsed_fraction(
    start: datetime,
    now: datetime,
    deadline: datetime,
    calendar: BusinessCalendar,
    paused_intervals: tuple[tuple[datetime, datetime], ...] = (),
) -> float:
    """How far through its window a clock is, as a fraction, clamped to [0, 1].

    Warning thresholds (§4.4's 50/80/95) are percentages of *business* time, not
    of wall-clock time. A case filed on Friday afternoon is not 60% consumed by
    Sunday, and reporting that it is would page somebody every weekend.
    """
    total = business_time_between(start, deadline, calendar, paused_intervals)
    if total <= timedelta(0):
        return 1.0
    used = business_time_between(start, now, calendar, paused_intervals)
    return max(0.0, min(1.0, used / total))


# -- internals -----------------------------------------------------------------


def _require_aware(moment: datetime, name: str) -> None:
    if moment.tzinfo is None:
        raise ValueError(
            f"{name} must be timezone-aware. A naive datetime here is an unstated "
            "assumption about the server's timezone, and this function exists "
            "precisely so that no such assumption is made."
        )


def _normalise(
    paused_intervals: tuple[tuple[datetime, datetime], ...],
) -> tuple[Interval, ...]:
    """Sort, validate and merge overlapping pauses.

    Overlaps are merged rather than rejected: a clock paused twice with an
    overlap is a data oddity, but double-subtracting the overlap would credit the
    tenant with business time that never happened — the failure mode that
    silently turns a breach into a non-breach.
    """
    intervals = []
    for start, end in paused_intervals:
        _require_aware(start, "pause start")
        _require_aware(end, "pause end")
        if end > start:
            intervals.append(Interval(start.astimezone(UTC), end.astimezone(UTC)))

    if not intervals:
        return ()

    intervals.sort(key=lambda i: i.start)
    merged = [intervals[0]]
    for interval in intervals[1:]:
        last = merged[-1]
        if interval.start <= last.end:
            merged[-1] = Interval(last.start, max(last.end, interval.end))
        else:
            merged.append(interval)
    return tuple(merged)


def _subtract(segment: Interval, pauses: tuple[Interval, ...]):
    """Yield the parts of `segment` not covered by any pause, in order."""
    cursor = segment.start
    for pause in pauses:
        if pause.end <= cursor:
            continue
        if pause.start >= segment.end:
            break
        if pause.start > cursor:
            yield Interval(cursor, min(pause.start, segment.end))
        cursor = max(cursor, pause.end)
        if cursor >= segment.end:
            return
    if cursor < segment.end:
        yield Interval(cursor, segment.end)
