"""Replaying a proposed SLA policy over real history (amplifier A9).

The gate that shapes this entire module: **replay must use the historical
calendar and the historical pause intervals, not today's.** A simulation against
the current calendar is a confident wrong number, which is worse than no number —
a compliance officer shown "this change would have caused 4 breaches" acts on it,
and has no way to know the figure was computed against a holiday list that did
not exist during the period.

So the replay reads each case's own `SLAPolicyVersion` (ADR-0004 made those
immutable), its own calendar as that version referenced it, and its own recorded
pause intervals. The only thing that varies is the window being proposed.

**Zero writes to any case.** The simulation runs against the read replica and
returns a value object; persisting the result writes one `PolicySimulation` row
and nothing else. The self-check below is what makes the whole thing trustworthy:
simulating an *unchanged* policy must reproduce the breach count that actually
occurred.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from disputeshield.models import Dispute, PolicySimulation, SLAEvent
from disputeshield.sla.calendar import BusinessCalendar
from disputeshield.sla.deadlines import DeadlineUncomputable, compute_deadline
from disputeshield.tenancy import context
from disputeshield.tenancy.middleware import db_tenant_context

# Analytics and exports run against the replica only (§11.1). A simulation over
# ninety days of cases must never contend with the decision path.
REPLICA = "replica"


@dataclasses.dataclass(frozen=True)
class CaseOutcome:
    reference: str
    category: str
    agent_id: str
    actual_breach: bool
    projected_breach: bool
    actual_deadline: datetime
    projected_deadline: datetime


@dataclasses.dataclass(frozen=True)
class SimulationResult:
    period_from: datetime
    period_to: datetime
    proposed: dict
    cases: tuple[CaseOutcome, ...]

    @property
    def cases_examined(self) -> int:
        return len(self.cases)

    @property
    def actual_breaches(self) -> int:
        return sum(1 for case in self.cases if case.actual_breach)

    @property
    def projected_breaches(self) -> int:
        return sum(1 for case in self.cases if case.projected_breach)

    @property
    def delta(self) -> int:
        return self.projected_breaches - self.actual_breaches

    def grouped(self, attribute: str) -> dict[str, dict[str, int]]:
        buckets: dict[str, dict[str, int]] = {}
        for case in self.cases:
            key = getattr(case, attribute) or "unassigned"
            bucket = buckets.setdefault(key, {"cases": 0, "actual": 0, "projected": 0})
            bucket["cases"] += 1
            bucket["actual"] += int(case.actual_breach)
            bucket["projected"] += int(case.projected_breach)
        return buckets


def simulate(
    *,
    period_from: datetime,
    period_to: datetime,
    resolution_hours: int | None = None,
    acknowledgement_minutes: int | None = None,
    business_hours_only: bool | None = None,
) -> SimulationResult:
    """Replay the period under a proposed window. Reads only.

    Every parameter defaults to `None`, meaning "leave this as it was". Passing
    nothing at all is the self-check: the result must reproduce history exactly.
    """
    proposed = {
        "resolution_hours": resolution_hours,
        "acknowledgement_minutes": acknowledgement_minutes,
        "business_hours_only": business_hours_only,
    }

    outcomes: list[CaseOutcome] = []

    # The replica is a different connection, so row level security has no context
    # there until one is established on it. Without this the simulation reads zero
    # rows and reports "0 cases examined" — which reads as "nothing to worry
    # about" rather than as a failure.
    with transaction.atomic(using=REPLICA), db_tenant_context(context.require(), using=REPLICA):
        cases = (
            Dispute.objects.using(REPLICA)
            .filter(submitted_at__gte=period_from, submitted_at__lt=period_to)
            .select_related("policy_version", "policy_version__calendar", "clock")
            .order_by("reference")
        )
        for case in cases:
            outcome = _replay(case, proposed)
            if outcome is not None:
                outcomes.append(outcome)

    return SimulationResult(
        period_from=period_from,
        period_to=period_to,
        proposed={k: v for k, v in proposed.items() if v is not None},
        cases=tuple(outcomes),
    )


def _replay(case: Dispute, proposed: dict) -> CaseOutcome | None:
    version = case.policy_version

    # The calendar as *this case's* policy version referenced it. Versions are
    # immutable (ADR-0004), which is what makes the historical calendar
    # recoverable at all — and is why the simulator could not have been built
    # before that decision.
    historical_calendar = _calendar_for(version)
    pauses = _historical_pauses(case)

    actual_window = timedelta(hours=version.resolution_hours)
    proposed_window = timedelta(
        hours=proposed["resolution_hours"]
        if proposed["resolution_hours"] is not None
        else version.resolution_hours
    )

    proposed_calendar = historical_calendar
    if proposed["business_hours_only"] is False:
        proposed_calendar = BusinessCalendar.continuous(historical_calendar.timezone_name)

    try:
        actual_deadline = compute_deadline(
            case.submitted_at, actual_window, historical_calendar, pauses
        )
        projected_deadline = compute_deadline(
            case.submitted_at, proposed_window, proposed_calendar, pauses
        )
    except DeadlineUncomputable:
        return None

    # A case is judged breached against a deadline by when it was actually
    # resolved. An unresolved case is measured against now, which is the same
    # rule the live clock uses.
    settled_at = case.resolved_at or case.closed_at or timezone.now()

    return CaseOutcome(
        reference=case.reference,
        category=case.category,
        agent_id=case.assigned_to_id or "",
        # Read from the record rather than recomputed: what actually happened is
        # what the sweep recorded at the time, not what today's arithmetic says.
        actual_breach=bool(case.breach_resolution),
        projected_breach=settled_at > projected_deadline,
        actual_deadline=actual_deadline,
        projected_deadline=projected_deadline,
    )


def self_check(*, period_from: datetime, period_to: datetime) -> tuple[bool, dict]:
    """Simulate an unchanged policy and compare with what actually happened.

    The gate. If replaying history without changing anything does not reproduce
    the breach count that occurred, the replay is not using history — and every
    number the simulator produces is a confident wrong one.
    """
    result = simulate(period_from=period_from, period_to=period_to)
    agrees = result.projected_breaches == result.actual_breaches
    return agrees, {
        "cases": result.cases_examined,
        "actual": result.actual_breaches,
        "replayed": result.projected_breaches,
        "disagreements": [
            case.reference for case in result.cases if case.actual_breach != case.projected_breach
        ][:20],
    }


def persist(*, tenant, policy_version, result: SimulationResult, ran_by: str) -> PolicySimulation:
    """Store the result beside the version it evaluated.

    So the change record shows what the author was told at the time, rather than
    what the same simulation would say today against different data.
    """
    return PolicySimulation.objects.create(
        tenant=tenant,
        policy_version=policy_version,
        proposed=result.proposed,
        period_from=result.period_from,
        period_to=result.period_to,
        cases_examined=result.cases_examined,
        actual_breaches=result.actual_breaches,
        projected_breaches=result.projected_breaches,
        by_category=result.grouped("category"),
        by_agent=result.grouped("agent_id"),
        ran_at=timezone.now(),
        ran_by=ran_by,
    )


def _calendar_for(version) -> BusinessCalendar:
    if not version.business_hours_only:
        return BusinessCalendar.continuous(version.calendar.timezone_name)
    return BusinessCalendar.from_model(version.calendar)


def _historical_pauses(case: Dispute) -> tuple[tuple[datetime, datetime], ...]:
    """The pauses this case actually had, from its recorded clock events.

    Read from `SLAEvent` rather than from the clock's materialised
    `paused_intervals`, because the events are the evidence and the materialised
    list is a view of them. For a replay that has to be defensible, read the
    evidence.
    """
    events = list(
        SLAEvent.objects.using(REPLICA)
        .filter(
            clock=case.clock_id,
            kind__in=[SLAEvent.Kind.PAUSED, SLAEvent.Kind.RESUMED],
        )
        .order_by("occurred_at", "id")
        .values_list("kind", "occurred_at")
    )

    intervals: list[tuple[datetime, datetime]] = []
    opened_at: datetime | None = None
    for kind, occurred_at in events:
        if kind == SLAEvent.Kind.PAUSED and opened_at is None:
            opened_at = occurred_at
        elif kind == SLAEvent.Kind.RESUMED and opened_at is not None:
            intervals.append((opened_at, occurred_at))
            opened_at = None

    # A pause never resumed ran until the case settled. Dropping it would credit
    # the firm with time it did not have.
    if opened_at is not None:
        intervals.append((opened_at, case.resolved_at or case.closed_at or timezone.now()))

    return tuple(intervals)
