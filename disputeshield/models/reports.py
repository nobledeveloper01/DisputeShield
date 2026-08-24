from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator, validate_email
from django.db import models

from disputeshield.identifiers import report_recipient_id, report_schedule_id
from disputeshield.tenancy.managers import TenantScopedModel


class ReportRecipient(TenantScopedModel):
    """An address a regulatory export may be sent to. An allowlist, not a field.

    A regulatory export is a disclosure of **every case in the period**. An
    endpoint that emails one to an address supplied in the request body is a data
    exfiltration route with a documented API — a single compromised compliance
    session, or one mistyped domain, sends the whole period somewhere nobody
    intended and nothing stops it. So the address is registered first, as its own
    audited act, and the send can only choose from what is already registered.

    Registration is deliberately the harder half: it is compliance-only, it
    requires a stated reason, and it writes an audit record naming who added the
    address. The send then has nothing interesting to authorise, which is the
    point — the decision was made earlier, by someone accountable, on the record.

    Deactivated rather than deleted, for the reason everything here is: "who was
    allowed to receive our disputes data in March" is a question a supervisor is
    entitled to ask, and a deleted row cannot answer it.
    """

    id = models.CharField(
        primary_key=True, max_length=32, default=report_recipient_id, editable=False
    )

    address = models.EmailField(max_length=254, validators=[validate_email])

    # Who this is, in the tenant's own words: "FCA supervision inbox", "Group
    # compliance archive". Shown wherever the address is, because an address
    # alone does not tell a reviewer whether it should be there.
    label = models.CharField(max_length=128)

    added_by = models.CharField(max_length=64)
    reason = models.CharField(max_length=255)

    is_active = models.BooleanField(default=True)
    deactivated_by = models.CharField(max_length=64, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_reportrecipient"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "address"], name="disputeshield_recipient_unique_per_tenant"
            )
        ]
        indexes = [models.Index(fields=["tenant", "is_active"])]

    def __str__(self) -> str:
        return f"{self.label} <{self.address}>"


class ReportSchedule(TenantScopedModel):
    """A monthly regulatory export, delivered without anybody remembering to ask.

    Two things about the design are load-bearing.

    **It exports a closed month, never the current one.** An export of a period
    that is still accepting cases changes every time it is produced, which makes
    the delivery's digest check refuse and makes the document worthless as a
    record. The period a run covers is always a calendar month that has ended, in
    this schedule's own timezone — a firm's "March" is its own, not UTC's.

    **A month is not marked delivered until it was delivered.** `last_period_start`
    advances on a confirmed send, not on a queued one. A schedule that queues
    twelve exports a year and delivers none must not be able to look healthy, and
    the only way to guarantee that is to make the record of progress depend on
    the outcome rather than on the attempt. The same property gives catch-up for
    free: a runner that was down for two months finds two months owed, because
    nothing ever said they were done.

    Months that could not be delivered after `MAX_ATTEMPTS_PER_PERIOD` tries are
    recorded in `failed_periods` and stepped over. Stepping over is deliberate —
    blocking every future month behind one stuck month turns a single bad period
    into a silent, total outage of the schedule — and recording it is what stops
    that from being a quiet skip.
    """

    id = models.CharField(
        primary_key=True, max_length=32, default=report_schedule_id, editable=False
    )

    name = models.CharField(max_length=128)

    # Addresses, resolved against `ReportRecipient` at every run rather than
    # linked once. Deactivating a recipient has to stop the mail actually
    # stopping, and a foreign key captured at creation would keep it flowing.
    recipients = models.JSONField(default=list)

    # 1 to 28 only. 29, 30 and 31 do not exist in every month, and the usual
    # workaround — silently sliding to the last day — makes a compliance
    # deadline mean a different date in February. Refusing is clearer than
    # guessing, and every firm can pick a day that exists.
    day_of_month = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(1), MaxValueValidator(28)]
    )
    hour = models.PositiveSmallIntegerField(
        default=6, validators=[MinValueValidator(0), MaxValueValidator(23)]
    )
    timezone_name = models.CharField(max_length=64, default="UTC")

    is_active = models.BooleanField(default=True)

    created_by = models.CharField(max_length=64)
    reason = models.CharField(max_length=255)

    # The last calendar month confirmed delivered, as its first day. Everything
    # after it and closed is owed.
    last_period_start = models.DateField(null=True, blank=True)

    # `[{"period": "2026-03-01", "attempts": 3, "last_error": "..."}]`. Stepped
    # over, never forgotten.
    failed_periods = models.JSONField(default=list)

    deactivated_by = models.CharField(max_length=64, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_reportschedule"
        indexes = [models.Index(fields=["tenant", "is_active"])]

    def __str__(self) -> str:
        return f"{self.name} (day {self.day_of_month}, {self.timezone_name})"
