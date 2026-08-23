"""Business calendars, as plain values.

Deliberately free of Django. The deadline arithmetic in `deadlines.py` is the
most consequential code in the product — every subtle bug in it is a compliance
breach nobody notices until an auditor does — so it operates on values that can
be constructed in a test in one line, without a database, without a fixture, and
without a migration.

`from_model()` is the only bridge to the ORM, and it lives here rather than in
the models so that the arithmetic never imports Django even indirectly.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

UTC = UTC

MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY, SUNDAY = range(7)
WEEKDAYS = (MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY)


class ImpossibleCalendar(ValueError):
    """A calendar that can never accumulate business time.

    Raised loudly rather than looped over. A calendar with no open days is a
    configuration mistake, and the alternative to raising is a sweep that hangs —
    which in this product means the compliance clock stops, silently, for every
    tenant sharing that worker.
    """


@dataclasses.dataclass(frozen=True, slots=True)
class Interval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Intervals are absolute instants; naive datetimes are refused.")

    @property
    def duration(self) -> timedelta:
        return max(self.end - self.start, timedelta(0))

    def intersect(self, other: Interval) -> Interval | None:
        start = max(self.start, other.start)
        end = min(self.end, other.end)
        return Interval(start, end) if end > start else None


@dataclasses.dataclass(frozen=True, slots=True)
class BusinessCalendar:
    """When a tenant is open, in the tenant's own timezone.

    `hours` maps weekday (Monday=0) to an (opens, closes) pair. A weekday absent
    from the mapping is closed. `holidays` are dates in the calendar's timezone,
    not UTC — a public holiday is a local calendar day, and treating it as a UTC
    day shifts it by hours for any tenant that is not on UTC.
    """

    timezone_name: str = "UTC"
    hours: dict[int, tuple[time, time]] = dataclasses.field(default_factory=dict)
    holidays: frozenset[date] = dataclasses.field(default_factory=frozenset)
    always_open: bool = False

    def __post_init__(self) -> None:
        if self.always_open:
            return
        if not self.hours:
            raise ImpossibleCalendar("A calendar with no open weekdays can never elapse.")
        for weekday, (opens, closes) in self.hours.items():
            if not 0 <= weekday <= 6:
                raise ValueError(f"weekday must be 0-6, got {weekday}")
            if opens >= closes:
                raise ValueError(
                    f"weekday {weekday}: opens {opens} is not before closes {closes}. "
                    "Overnight shifts are expressed as two calendar days, not a wrapped one."
                )

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @classmethod
    def continuous(cls, timezone_name: str = "UTC") -> BusinessCalendar:
        """A 24/7 clock. `business_hours_only = False` in §4.4's policy."""
        return cls(timezone_name=timezone_name, always_open=True)

    @classmethod
    def standard_week(
        cls,
        timezone_name: str = "UTC",
        opens: time = time(9, 0),
        closes: time = time(17, 0),
        holidays: frozenset[date] | None = None,
    ) -> BusinessCalendar:
        return cls(
            timezone_name=timezone_name,
            hours=dict.fromkeys(WEEKDAYS, (opens, closes)),
            holidays=holidays or frozenset(),
        )

    @classmethod
    def from_model(cls, calendar) -> BusinessCalendar:
        """Adapt a persisted BusinessCalendar. The only ORM bridge in this module."""
        return cls(
            timezone_name=calendar.timezone_name,
            hours={
                window.weekday: (window.opens_at, window.closes_at)
                for window in calendar.windows.all()
            },
            holidays=frozenset(calendar.holidays.values_list("observed_on", flat=True)),
            always_open=calendar.always_open,
        )

    # -- the segment walk ------------------------------------------------------

    def segments_from(self, moment: datetime, *, max_days: int = 366):
        """Yield business-time intervals in UTC, in order, starting at `moment`.

        Day by day rather than in one span, even when the calendar is continuous,
        because a local day is not always 24 hours long. Resolving each day's
        boundaries in local time and converting each to UTC is what makes a DST
        transition fall out of the arithmetic instead of having to be special-cased.
        """
        if moment.tzinfo is None:
            raise ValueError("segments_from requires an aware datetime")

        local_date = moment.astimezone(self.tz).date()
        days_without_business = 0

        for offset in range(max_days + 1):
            current = local_date + timedelta(days=offset)
            segment = self._segment_for(current)

            if segment is None:
                days_without_business += 1
                if days_without_business > 365:
                    raise ImpossibleCalendar(
                        f"No business time in 365 days from {local_date} — the calendar "
                        "is closed every day, or every day is a holiday."
                    )
                continue

            days_without_business = 0
            clipped = segment.intersect(Interval(moment, datetime.max.replace(tzinfo=UTC)))
            if clipped is not None and clipped.duration > timedelta(0):
                yield clipped

    def _segment_for(self, local_day: date) -> Interval | None:
        if local_day in self.holidays:
            return None

        if self.always_open:
            opens, closes = time(0, 0), time(0, 0)
            start_local = datetime.combine(local_day, opens, tzinfo=self.tz)
            end_local = datetime.combine(local_day + timedelta(days=1), closes, tzinfo=self.tz)
        else:
            window = self.hours.get(local_day.weekday())
            if window is None:
                return None
            opens, closes = window
            start_local = datetime.combine(local_day, opens, tzinfo=self.tz)
            end_local = datetime.combine(local_day, closes, tzinfo=self.tz)

        start = start_local.astimezone(UTC)
        end = end_local.astimezone(UTC)
        return Interval(start, end) if end > start else None
