"""Nightly reconciliation of materialised deadlines against the pure function.

ADR-0007 accepts a real cost: deadline state exists in two places — derivable
from `compute_deadline`, and materialised in rows — and they must not diverge.
This is the price of the sweep's watermark design, and paying it in the open is
the difference between a cache and a second source of truth nobody checks.

Divergence is reported, never silently repaired. A stored deadline that no longer
matches the arithmetic may mean a bug, or it may mean a policy was edited and a
backfill is owed — and quietly rewriting the row would destroy the evidence
needed to tell which.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

from disputeshield.models import SLAClock, SLADeadline
from disputeshield.sla.clock import calendar_for, paused_intervals_of
from disputeshield.sla.deadlines import compute_deadline
from disputeshield.tenancy.middleware import db_tenant_context


@dataclasses.dataclass(frozen=True)
class Mismatch:
    clock_id: str
    kind: str
    threshold_percent: int | None
    stored: datetime
    recomputed: datetime

    @property
    def drift(self) -> timedelta:
        return self.recomputed - self.stored


@dataclasses.dataclass(frozen=True)
class ReconcileResult:
    checked: int
    mismatches: tuple[Mismatch, ...]

    @property
    def ok(self) -> bool:
        return not self.mismatches


def reconcile(*, tenant_id: str | None = None) -> ReconcileResult:
    """Check every open clock's materialised deadlines against the arithmetic.

    Tenant by tenant — see `disputeshield/tenancy/platform.py`. A reconciler that
    reads nothing reports a clean bill of health for a database it never looked at.
    """
    from disputeshield.tenancy.platform import for_each_tenant

    if tenant_id:
        from django.db import transaction

        with transaction.atomic(), db_tenant_context(tenant_id):
            return _reconcile_one_tenant()

    checked = 0
    mismatches: list[Mismatch] = []
    for result in for_each_tenant(lambda _tenant_id: _reconcile_one_tenant()):
        checked += result.checked
        mismatches.extend(result.mismatches)
    return ReconcileResult(checked=checked, mismatches=tuple(mismatches))


def _reconcile_one_tenant() -> ReconcileResult:
    clocks = SLAClock.objects.all_tenants().exclude(state=SLAClock.State.STOPPED)

    checked = 0
    mismatches: list[Mismatch] = []

    for clock in clocks.select_related("policy_version", "policy_version__calendar").iterator():
        calendar = calendar_for(clock)
        pauses = paused_intervals_of(clock)  # evaluated as of now, matching the sweep
        version = clock.policy_version
        resolution = timedelta(hours=version.resolution_hours)

        for deadline in clock.deadlines.filter(fired_at__isnull=True):
            checked += 1
            window = _window_for(deadline, version, resolution)
            if window is None:
                continue
            recomputed = compute_deadline(clock.started_at, window, calendar, pauses)
            if recomputed != deadline.fires_at:
                mismatches.append(
                    Mismatch(
                        clock_id=clock.pk,
                        kind=deadline.kind,
                        threshold_percent=deadline.threshold_percent,
                        stored=deadline.fires_at,
                        recomputed=recomputed,
                    )
                )

    return ReconcileResult(checked=checked, mismatches=tuple(mismatches))


def _window_for(deadline: SLADeadline, version, resolution: timedelta) -> timedelta | None:
    if deadline.kind == SLADeadline.Kind.ACKNOWLEDGEMENT:
        return timedelta(minutes=version.acknowledgement_minutes)
    if deadline.kind == SLADeadline.Kind.RESOLUTION:
        return resolution
    if deadline.kind == SLADeadline.Kind.WARNING and deadline.threshold_percent:
        return resolution * deadline.threshold_percent / 100
    return None
