from __future__ import annotations

from django.db import models

from disputeshield.identifiers import calendar_id
from disputeshield.tenancy.managers import TenantScopedModel


class BusinessCalendar(TenantScopedModel):
    """When a tenant is open. Owned by compliance, changed without a deploy (§6.5)."""

    id = models.CharField(primary_key=True, max_length=32, default=calendar_id, editable=False)
    name = models.CharField(max_length=128)
    # IANA name. Calendar boundaries resolve here; all arithmetic stays in UTC.
    timezone_name = models.CharField(max_length=64, default="UTC")
    always_open = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_businesscalendar"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "name"], name="uniq_calendar_name_per_tenant")
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.timezone_name})"


class BusinessHoursWindow(models.Model):
    """One weekday's opening hours. Monday is 0, matching `date.weekday()`."""

    calendar = models.ForeignKey(BusinessCalendar, on_delete=models.PROTECT, related_name="windows")
    weekday = models.PositiveSmallIntegerField()
    opens_at = models.TimeField()
    closes_at = models.TimeField()

    class Meta:
        db_table = "disputeshield_businesshourswindow"
        ordering = ["weekday"]
        constraints = [
            models.UniqueConstraint(fields=["calendar", "weekday"], name="uniq_window_per_weekday"),
            models.CheckConstraint(
                condition=models.Q(opens_at__lt=models.F("closes_at")),
                name="window_opens_before_it_closes",
            ),
        ]

    def __str__(self) -> str:
        return f"weekday {self.weekday}: {self.opens_at}-{self.closes_at}"


class Holiday(models.Model):
    """A closed day, as a date in the calendar's own timezone.

    Local, not UTC. A public holiday is a local calendar day, and storing it as a
    UTC date shifts it by hours for any tenant not on UTC — which silently moves
    every deadline that crosses it.
    """

    calendar = models.ForeignKey(
        BusinessCalendar, on_delete=models.PROTECT, related_name="holidays"
    )
    observed_on = models.DateField()
    name = models.CharField(max_length=128)

    class Meta:
        db_table = "disputeshield_holiday"
        ordering = ["observed_on"]
        constraints = [
            models.UniqueConstraint(fields=["calendar", "observed_on"], name="uniq_holiday_per_day")
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.observed_on})"
