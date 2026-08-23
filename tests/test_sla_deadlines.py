"""§4.4 — deadline arithmetic.

The highest-value suite in the project. Every case here is a compliance breach
that would otherwise be found by an auditor rather than by CI.

This file runs twice in CI: once normally, and once under
`TZ=Pacific/Kiritimati`. A deadline that depends on where the server is running
is a deadline that changes when the deployment moves regions.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from disputeshield.sla import (
    BusinessCalendar,
    DeadlineUncomputable,
    ImpossibleCalendar,
    business_time_between,
    compute_deadline,
    elapsed_fraction,
)

UTC = UTC
LAGOS = "Africa/Lagos"
NEW_YORK = "America/New_York"


def at(tz: str, y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> datetime:
    """A local wall-clock instant, as UTC. Tests read in the calendar's own time."""
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def local(moment: datetime, tz: str) -> datetime:
    return moment.astimezone(ZoneInfo(tz))


class TestWithinAndAcrossDays:
    def test_a_window_that_fits_inside_one_business_day(self):
        cal = BusinessCalendar.standard_week(LAGOS)
        start = at(LAGOS, 2026, 8, 19, 10, 0)  # Wednesday
        deadline = compute_deadline(start, timedelta(hours=3), cal)
        assert local(deadline, LAGOS) == datetime(2026, 8, 19, 13, 0, tzinfo=ZoneInfo(LAGOS))

    def test_a_window_starting_before_opening_begins_at_opening(self):
        """A complaint filed at 03:00 does not consume six hours before anyone
        could have looked at it."""
        cal = BusinessCalendar.standard_week(LAGOS)
        start = at(LAGOS, 2026, 8, 19, 3, 0)
        deadline = compute_deadline(start, timedelta(hours=2), cal)
        assert local(deadline, LAGOS).hour == 11

    def test_a_window_starting_after_closing_rolls_to_the_next_open_day(self):
        cal = BusinessCalendar.standard_week(LAGOS)
        start = at(LAGOS, 2026, 8, 19, 22, 0)  # Wednesday night
        deadline = compute_deadline(start, timedelta(hours=1), cal)
        assert local(deadline, LAGOS) == datetime(2026, 8, 20, 10, 0, tzinfo=ZoneInfo(LAGOS))

    def test_a_window_spanning_a_weekend(self):
        cal = BusinessCalendar.standard_week(LAGOS)
        start = at(LAGOS, 2026, 8, 21, 16, 0)  # Friday, one hour before close
        deadline = compute_deadline(start, timedelta(hours=8), cal)
        assert local(deadline, LAGOS) == datetime(2026, 8, 24, 16, 0, tzinfo=ZoneInfo(LAGOS))

    def test_a_window_shorter_than_one_business_day_near_close(self):
        cal = BusinessCalendar.standard_week(LAGOS)
        start = at(LAGOS, 2026, 8, 19, 16, 30)
        deadline = compute_deadline(start, timedelta(minutes=45), cal)
        # 30 minutes on Wednesday, 15 minutes on Thursday morning.
        assert local(deadline, LAGOS) == datetime(2026, 8, 20, 9, 15, tzinfo=ZoneInfo(LAGOS))


class TestHolidays:
    def test_a_holiday_is_skipped_entirely(self):
        cal = BusinessCalendar.standard_week(LAGOS, holidays=frozenset({date(2026, 8, 20)}))
        start = at(LAGOS, 2026, 8, 19, 16, 0)
        deadline = compute_deadline(start, timedelta(hours=2), cal)
        # 1h Wednesday, then Thursday is a holiday, so 1h on Friday morning.
        assert local(deadline, LAGOS) == datetime(2026, 8, 21, 10, 0, tzinfo=ZoneInfo(LAGOS))

    def test_holidays_are_local_dates_not_utc_dates(self):
        """A public holiday is a local calendar day. Treating it as a UTC day
        shifts it by hours for any tenant not on UTC, which silently moves every
        deadline that crosses it.

        New York is UTC-4 in July, so the two instants below fall on the same UTC
        date and on different *local* dates. Only the second is on the holiday.
        """
        cal = BusinessCalendar(
            timezone_name=NEW_YORK, always_open=True, holidays=frozenset({date(2026, 7, 3)})
        )

        # 03:00 UTC on 3 July is 23:00 on 2 July in New York — a working day.
        still_the_second = datetime(2026, 7, 3, 3, 0, tzinfo=UTC)
        assert business_time_between(
            still_the_second, still_the_second + timedelta(hours=1), cal
        ) == timedelta(hours=1)

        # 05:00 UTC on 3 July is 01:00 on 3 July in New York — the holiday.
        the_holiday = datetime(2026, 7, 3, 5, 0, tzinfo=UTC)
        assert business_time_between(
            the_holiday, the_holiday + timedelta(hours=1), cal
        ) == timedelta(0)

    def test_a_calendar_where_every_day_is_a_holiday_raises(self):
        cal = BusinessCalendar.standard_week(
            LAGOS, holidays=frozenset(date(2026, 1, 1) + timedelta(days=n) for n in range(500))
        )
        with pytest.raises(ImpossibleCalendar, match="365 days"):
            compute_deadline(at(LAGOS, 2026, 1, 5, 10, 0), timedelta(hours=1), cal)

    def test_a_calendar_with_no_open_weekdays_is_refused_at_construction(self):
        with pytest.raises(ImpossibleCalendar, match="never elapse"):
            BusinessCalendar(timezone_name=LAGOS, hours={})


class TestDaylightSaving:
    """Both directions. Lagos has no DST, which is exactly why these use New York."""

    def test_spring_forward_loses_an_hour_of_wall_clock_not_of_business_time(self):
        # 2026-03-08: 02:00 -> 03:00 in New York.
        cal = BusinessCalendar(
            timezone_name=NEW_YORK,
            hours={weekday: (time(1, 0), time(5, 0)) for weekday in range(7)},
        )
        start = at(NEW_YORK, 2026, 3, 8, 1, 0)
        deadline = compute_deadline(start, timedelta(hours=3), cal)

        # Three business hours are three real hours, even though the wall clock
        # shows four having passed.
        assert deadline - start == timedelta(hours=3)
        assert business_time_between(start, deadline, cal) == timedelta(hours=3)

    def test_fall_back_repeats_an_hour_of_wall_clock(self):
        # 2026-11-01: 02:00 -> 01:00 in New York.
        cal = BusinessCalendar(
            timezone_name=NEW_YORK,
            hours={weekday: (time(0, 0), time(6, 0)) for weekday in range(7)},
        )
        start = at(NEW_YORK, 2026, 11, 1, 0, 0)
        deadline = compute_deadline(start, timedelta(hours=5), cal)
        assert deadline - start == timedelta(hours=5)

    def test_a_full_business_day_is_the_same_length_across_a_transition(self):
        """The window is measured in business time, so a 9-5 day is eight hours
        in March and eight hours in July — the tenant's obligation does not
        change because the clocks did."""
        cal = BusinessCalendar.standard_week(NEW_YORK)
        march = at(NEW_YORK, 2026, 3, 9, 9, 0)
        july = at(NEW_YORK, 2026, 7, 9, 9, 0)
        for start in (march, july):
            deadline = compute_deadline(start, timedelta(hours=8), cal)
            assert local(deadline, NEW_YORK).hour == 17


class TestPauses:
    def test_a_pause_removes_its_time_from_the_window(self):
        cal = BusinessCalendar.standard_week(LAGOS)
        start = at(LAGOS, 2026, 8, 19, 9, 0)
        pause = (at(LAGOS, 2026, 8, 19, 10, 0), at(LAGOS, 2026, 8, 19, 12, 0))
        deadline = compute_deadline(start, timedelta(hours=4), cal, (pause,))
        # 1h before the pause, 3h after it: 09:00-10:00 then 12:00-15:00.
        assert local(deadline, LAGOS) == datetime(2026, 8, 19, 15, 0, tzinfo=ZoneInfo(LAGOS))

    def test_multiple_pauses_in_one_day(self):
        cal = BusinessCalendar.standard_week(LAGOS)
        start = at(LAGOS, 2026, 8, 19, 9, 0)
        pauses = (
            (at(LAGOS, 2026, 8, 19, 10, 0), at(LAGOS, 2026, 8, 19, 11, 0)),
            (at(LAGOS, 2026, 8, 19, 12, 0), at(LAGOS, 2026, 8, 19, 13, 0)),
        )
        deadline = compute_deadline(start, timedelta(hours=4), cal, pauses)
        assert local(deadline, LAGOS) == datetime(2026, 8, 19, 15, 0, tzinfo=ZoneInfo(LAGOS))

    def test_a_pause_spanning_a_holiday_costs_only_its_business_time(self):
        """The pause covers a holiday, and the holiday was never business time to
        begin with. Subtracting it twice would extend the window by a day."""
        cal = BusinessCalendar.standard_week(LAGOS, holidays=frozenset({date(2026, 8, 20)}))
        start = at(LAGOS, 2026, 8, 19, 15, 0)
        pause = (at(LAGOS, 2026, 8, 19, 16, 0), at(LAGOS, 2026, 8, 21, 10, 0))
        deadline = compute_deadline(start, timedelta(hours=3), cal, (pause,))
        # 1h Wednesday; Thursday is a holiday; the pause ends Friday 10:00, then 2h.
        assert local(deadline, LAGOS) == datetime(2026, 8, 21, 12, 0, tzinfo=ZoneInfo(LAGOS))

    def test_a_pause_spanning_a_weekend_costs_only_its_business_time(self):
        cal = BusinessCalendar.standard_week(LAGOS)
        start = at(LAGOS, 2026, 8, 21, 16, 0)  # Friday
        pause = (at(LAGOS, 2026, 8, 21, 16, 30), at(LAGOS, 2026, 8, 24, 10, 0))
        deadline = compute_deadline(start, timedelta(hours=2), cal, (pause,))
        # 30 min Friday, then from Monday 10:00 a further 90 minutes.
        assert local(deadline, LAGOS) == datetime(2026, 8, 24, 11, 30, tzinfo=ZoneInfo(LAGOS))

    def test_overlapping_pauses_are_merged_not_double_counted(self):
        """Double-subtracting an overlap credits business time that never
        happened — the failure that silently turns a breach into a non-breach."""
        cal = BusinessCalendar.continuous()
        start = datetime(2026, 8, 19, 0, 0, tzinfo=UTC)
        overlapping = (
            (datetime(2026, 8, 19, 1, 0, tzinfo=UTC), datetime(2026, 8, 19, 3, 0, tzinfo=UTC)),
            (datetime(2026, 8, 19, 2, 0, tzinfo=UTC), datetime(2026, 8, 19, 4, 0, tzinfo=UTC)),
        )
        single = (
            (datetime(2026, 8, 19, 1, 0, tzinfo=UTC), datetime(2026, 8, 19, 4, 0, tzinfo=UTC)),
        )
        assert compute_deadline(start, timedelta(hours=2), cal, overlapping) == compute_deadline(
            start, timedelta(hours=2), cal, single
        )

    def test_a_zero_length_pause_changes_nothing(self):
        cal = BusinessCalendar.continuous()
        start = datetime(2026, 8, 19, 0, 0, tzinfo=UTC)
        moment = datetime(2026, 8, 19, 1, 0, tzinfo=UTC)
        assert compute_deadline(start, timedelta(hours=2), cal, ((moment, moment),)) == (
            compute_deadline(start, timedelta(hours=2), cal)
        )


class TestContinuousCalendars:
    def test_a_continuous_calendar_is_plain_wall_clock_arithmetic(self):
        cal = BusinessCalendar.continuous()
        start = datetime(2026, 8, 21, 22, 0, tzinfo=UTC)
        assert compute_deadline(start, timedelta(hours=72), cal) == start + timedelta(hours=72)

    def test_a_continuous_calendar_still_observes_holidays(self):
        cal = BusinessCalendar(
            timezone_name=LAGOS, always_open=True, holidays=frozenset({date(2026, 8, 20)})
        )
        start = at(LAGOS, 2026, 8, 19, 23, 0)
        deadline = compute_deadline(start, timedelta(hours=2), cal)
        assert local(deadline, LAGOS) == datetime(2026, 8, 21, 1, 0, tzinfo=ZoneInfo(LAGOS))


class TestGuards:
    def test_a_naive_datetime_is_refused(self):
        cal = BusinessCalendar.continuous()
        with pytest.raises(ValueError, match="timezone-aware"):
            compute_deadline(datetime(2026, 8, 19, 9, 0), timedelta(hours=1), cal)

    def test_a_non_positive_window_is_refused(self):
        cal = BusinessCalendar.continuous()
        start = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)
        with pytest.raises(ValueError, match="must be positive"):
            compute_deadline(start, timedelta(0), cal)

    def test_a_window_longer_than_the_horizon_raises_rather_than_looping(self):
        cal = BusinessCalendar.standard_week(LAGOS)
        with pytest.raises(DeadlineUncomputable, match="366-day horizon"):
            compute_deadline(at(LAGOS, 2026, 1, 5, 9, 0), timedelta(days=400), cal)

    def test_overnight_windows_are_refused_at_construction(self):
        with pytest.raises(ValueError, match="two calendar days"):
            BusinessCalendar(timezone_name=LAGOS, hours={0: (time(22, 0), time(2, 0))})


class TestElapsedFraction:
    def test_a_weekend_does_not_consume_a_window(self):
        """A case filed Friday afternoon is not 60% consumed by Sunday, and
        reporting that it is would page somebody every weekend."""
        cal = BusinessCalendar.standard_week(LAGOS)
        start = at(LAGOS, 2026, 8, 21, 16, 0)
        deadline = compute_deadline(start, timedelta(hours=8), cal)
        sunday = at(LAGOS, 2026, 8, 23, 12, 0)
        assert elapsed_fraction(start, sunday, deadline, cal) == pytest.approx(1 / 8)

    def test_the_fraction_is_clamped(self):
        cal = BusinessCalendar.continuous()
        start = datetime(2026, 8, 19, 0, 0, tzinfo=UTC)
        deadline = start + timedelta(hours=10)
        assert elapsed_fraction(start, start - timedelta(days=1), deadline, cal) == 0.0
        assert elapsed_fraction(start, start + timedelta(days=5), deadline, cal) == 1.0


class TestDeterminism:
    def test_the_result_does_not_depend_on_the_machines_timezone(self):
        """CI runs this file a second time under TZ=Pacific/Kiritimati. This test
        asserts the same property inside one process, so a failure names the
        cause rather than showing up as a mysteriously different second run."""
        cal = BusinessCalendar.standard_week(NEW_YORK)
        start = at(NEW_YORK, 2026, 3, 6, 16, 0)
        expected = compute_deadline(start, timedelta(hours=10), cal)

        original = os.environ.get("TZ")
        try:
            for zone in ("UTC", "Pacific/Kiritimati", "America/Anchorage", "Asia/Kolkata"):
                os.environ["TZ"] = zone
                time.tzset() if hasattr(time, "tzset") else None
                assert compute_deadline(start, timedelta(hours=10), cal) == expected
        finally:
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original


# -- the property ---------------------------------------------------------------

ZONES = st.sampled_from(["UTC", LAGOS, NEW_YORK, "Asia/Kolkata", "Australia/Sydney"])
INSTANTS = st.datetimes(min_value=datetime(2026, 1, 1), max_value=datetime(2026, 10, 1)).map(
    lambda naive: naive.replace(tzinfo=UTC)
)


@st.composite
def calendars(draw) -> BusinessCalendar:
    if draw(st.booleans()):
        return BusinessCalendar(
            timezone_name=draw(ZONES),
            always_open=True,
            holidays=frozenset(
                draw(
                    st.lists(
                        st.dates(datetime(2026, 1, 1).date(), datetime(2026, 12, 31).date()),
                        max_size=4,
                    )
                )
            ),
        )
    open_days = draw(st.lists(st.integers(0, 6), min_size=1, max_size=7, unique=True))
    opens_hour = draw(st.integers(0, 20))
    length = draw(st.integers(1, 23 - opens_hour))
    return BusinessCalendar(
        timezone_name=draw(ZONES),
        hours={day: (time(opens_hour), time(opens_hour + length)) for day in open_days},
        holidays=frozenset(
            draw(
                st.lists(
                    st.dates(datetime(2026, 1, 1).date(), datetime(2026, 12, 31).date()), max_size=4
                )
            )
        ),
    )


@st.composite
def pause_sets(draw) -> tuple[tuple[datetime, datetime], ...]:
    pauses = []
    for _ in range(draw(st.integers(0, 3))):
        start = draw(INSTANTS)
        pauses.append((start, start + timedelta(minutes=draw(st.integers(1, 5000)))))
    return tuple(pauses)


@settings(max_examples=int(os.environ.get("HYPOTHESIS_EXAMPLES", "400")), deadline=None)
@given(start=INSTANTS, minutes=st.integers(1, 4800), cal=calendars(), pauses=pause_sets())
def test_the_deadline_contains_exactly_the_requested_business_time(start, minutes, cal, pauses):
    """The round trip, for arbitrary calendars, starts, windows and pauses.

    `compute_deadline` walks forward through business time; `business_time_between`
    measures it. They are implemented independently, so agreement between them is
    evidence rather than tautology.
    """
    window = timedelta(minutes=minutes)
    try:
        deadline = compute_deadline(start, window, cal, pauses)
    except (DeadlineUncomputable, ImpossibleCalendar):
        assume(False)
        return

    measured = business_time_between(start, deadline, cal, pauses)
    assert measured == window, (
        f"asked for {window} of business time, the span contains {measured}\n"
        f"  start={start.isoformat()} deadline={deadline.isoformat()}\n"
        f"  calendar={cal}\n  pauses={pauses}"
    )


@settings(max_examples=int(os.environ.get("HYPOTHESIS_EXAMPLES", "200")), deadline=None)
@given(start=INSTANTS, minutes=st.integers(1, 2400), cal=calendars())
def test_a_longer_window_never_produces_an_earlier_deadline(start, minutes, cal):
    """Monotonicity. A violation would mean a tenant who lengthened a policy
    window found their open cases breaching sooner."""
    try:
        shorter = compute_deadline(start, timedelta(minutes=minutes), cal)
        longer = compute_deadline(start, timedelta(minutes=minutes + 30), cal)
    except (DeadlineUncomputable, ImpossibleCalendar):
        assume(False)
        return
    assert longer >= shorter
