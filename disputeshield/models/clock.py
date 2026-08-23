from __future__ import annotations

from django.db import models

from disputeshield.identifiers import clock_id, deadline_id, notification_id, sla_event_id
from disputeshield.tenancy.managers import TenantScopedModel


class SLAClock(TenantScopedModel):
    """The regulatory clock running against one subject.

    Separate from the case it belongs to, because the engine is built and tested
    before the case model exists (docs/ROADMAP.md, phase 2 before phase 3). The
    `Dispute` in phase 3 references a clock; the clock never reaches back.

    `paused_intervals` are stored as an ordered list of [start, end] ISO pairs
    rather than derived from SLAEvent rows on every read. The events remain the
    evidence — this is a materialised view of them, reconciled nightly, for the
    same reason deadlines are materialised (ADR-0007): the sweep must not pay for
    a join per case per minute.
    """

    class State(models.TextChoices):
        RUNNING = "running", "Running"
        PAUSED = "paused", "Paused"
        STOPPED = "stopped", "Stopped"

    id = models.CharField(primary_key=True, max_length=32, default=clock_id, editable=False)
    subject_type = models.CharField(max_length=32, default="dispute")
    subject_id = models.CharField(max_length=64, db_index=True)

    policy_version = models.ForeignKey(
        "disputeshield.SLAPolicyVersion", on_delete=models.PROTECT, related_name="+"
    )

    started_at = models.DateTimeField()
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)

    state = models.CharField(max_length=16, choices=State.choices, default=State.RUNNING)
    paused_at = models.DateTimeField(null=True, blank=True)
    paused_intervals = models.JSONField(default=list)

    class Meta:
        db_table = "disputeshield_slaclock"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "subject_type", "subject_id"], name="uniq_clock_per_subject"
            )
        ]
        indexes = [models.Index(fields=["tenant", "state"])]

    def __str__(self) -> str:
        return f"clock for {self.subject_type}:{self.subject_id} ({self.state})"


class SLAEvent(TenantScopedModel):
    """Every clock event: started, paused, resumed, warned, breached, stopped.

    `clock_remaining_seconds` is recorded at the moment of the event. That single
    field is what makes a breach explainable six months later — without it, the
    record says a case was paused but not how close to breaching it was when
    somebody paused it.
    """

    class Kind(models.TextChoices):
        STARTED = "started", "Started"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        PAUSED = "paused", "Paused"
        RESUMED = "resumed", "Resumed"
        WARNED = "warned", "Warning threshold crossed"
        BREACHED = "breached", "Breached"
        STOPPED = "stopped", "Stopped"
        DEADLINES_RECOMPUTED = "deadlines_recomputed", "Deadlines recomputed"

    id = models.CharField(primary_key=True, max_length=32, default=sla_event_id, editable=False)
    clock = models.ForeignKey(SLAClock, on_delete=models.PROTECT, related_name="events")
    kind = models.CharField(max_length=32, choices=Kind.choices)

    # §4.4/C3: a pausable clock is an abusable clock, so a pause without a reason
    # is not a thing the model can represent. Enforced in the service and by a
    # check constraint, because a reason enforced only in a view is one refactor
    # from being optional.
    reason = models.TextField(blank=True)

    actor_type = models.CharField(max_length=16, default="system")
    actor_id = models.CharField(max_length=64, blank=True)
    clock_remaining_seconds = models.IntegerField()
    occurred_at = models.DateTimeField()

    class Meta:
        db_table = "disputeshield_slaevent"
        ordering = ["clock_id", "occurred_at"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(kind__in=["paused", "resumed"]) | ~models.Q(reason=""),
                name="pause_and_resume_carry_a_reason",
            )
        ]
        indexes = [models.Index(fields=["tenant", "kind", "occurred_at"])]

    def __str__(self) -> str:
        return f"{self.kind} @{self.occurred_at.isoformat()}"


class SLADeadline(TenantScopedModel):
    """A materialised instant at which something is due to fire (ADR-0007).

    The sweep selects on `fires_at` with a partial index over unfired rows, so its
    cost is proportional to events due rather than to cases open. At the §11.9
    load target that is the difference between the compliance clock being
    reliable and it being least reliable exactly when a tenant has most cases.

    It also makes catch-up provably correct: unfired rows with a past `fires_at`
    *are* the missed notifications, which is why the §11.5 runbook can promise
    that catch-up sends only what was actually missed.
    """

    class Kind(models.TextChoices):
        ACKNOWLEDGEMENT = "acknowledgement", "Acknowledgement due"
        RESOLUTION = "resolution", "Resolution due"
        WARNING = "warning", "Warning threshold"
        AUTO_CLOSE = "auto_close", "Auto-close due"
        REOPEN_WINDOW = "reopen_window", "Reopen window expires"

    id = models.CharField(primary_key=True, max_length=32, default=deadline_id, editable=False)
    clock = models.ForeignKey(SLAClock, on_delete=models.PROTECT, related_name="deadlines")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    threshold_percent = models.PositiveSmallIntegerField(null=True, blank=True)

    fires_at = models.DateTimeField()
    fired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "disputeshield_sladeadline"
        constraints = [
            models.UniqueConstraint(
                fields=["clock", "kind", "threshold_percent"],
                name="uniq_deadline_per_clock_kind_threshold",
            )
        ]
        indexes = [
            # Partial: the sweep only ever looks at what has not fired.
            models.Index(
                fields=["fires_at"],
                condition=models.Q(fired_at__isnull=True),
                name="idx_pending_deadlines",
            ),
            models.Index(fields=["tenant", "clock", "kind"]),
        ]

    def __str__(self) -> str:
        label = f"{self.kind}"
        if self.threshold_percent is not None:
            label += f" {self.threshold_percent}%"
        return f"{label} at {self.fires_at.isoformat()}"


class NotificationOutbox(TenantScopedModel):
    """Recorded before it is sent (§4.4, D7).

    The sweep writes this row in the same transaction that marks a deadline
    fired. A separate dispatcher claims and sends. Delivery is at-least-once at
    the transport and exactly-once at the provider, because `idempotency_key` is
    derived deterministically from what the notification is *about* rather than
    from when it was generated.

    That determinism is what makes the §11.5 runbook safe to run during an
    incident: replaying a window cannot produce a second breach page.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    id = models.CharField(primary_key=True, max_length=32, default=notification_id, editable=False)
    idempotency_key = models.CharField(max_length=128)
    channel = models.CharField(max_length=16, default="email")
    event_type = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        db_table = "disputeshield_notificationoutbox"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "idempotency_key"], name="uniq_notification_per_idempotency_key"
            )
        ]
        indexes = [
            models.Index(
                fields=["created_at"],
                condition=models.Q(status="pending"),
                name="idx_pending_notifications",
            )
        ]

    def __str__(self) -> str:
        return f"{self.event_type} → {self.channel} ({self.status})"


class SweepHeartbeat(models.Model):
    """The dead-man's switch (§11.3, §11.4).

    A single row. Not tenant-scoped, because a stalled sweep is an outage of the
    compliance function for every tenant at once, and §11.5 is emphatic that this
    is invisible from the outside: the API stays up and the dashboard keeps
    rendering while every clock silently stops advancing.
    """

    singleton = models.BooleanField(primary_key=True, default=True)
    last_swept_at = models.DateTimeField()
    last_duration_ms = models.PositiveIntegerField(default=0)
    last_fired_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "disputeshield_sweepheartbeat"

    def __str__(self) -> str:
        return f"last swept {self.last_swept_at.isoformat()}"
