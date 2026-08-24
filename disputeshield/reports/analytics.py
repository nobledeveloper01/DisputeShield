"""Breach analysis (§6.5, §3.2 D3).

The point of this view is stated in the specification: fix causes rather than
symptoms. So every figure is grouped by something a compliance officer can act
on — a category, an agent, a cause — and pause duration is reported *beside*
breaches rather than in a separate screen, because §4.4's whole argument is that
a pausable clock is an abusable one and the abuse is only visible in the
comparison.

Read-only, and genuinely routed to the replica (§11.1) — an export or an
analytics sweep must never contend with the decision path. Every query below uses
`REPLICA` and every entry point opens `replica_reads`, because a replica is a
different connection and a tenant context established on the primary is absent
there. A docstring claiming the routing while the queries ran on the primary is
exactly the kind of comment that survives review and means nothing.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

from django.db.models import Avg, Count, Q, Sum

from disputeshield.models import Dispute, SLAEvent
from disputeshield.tenancy.platform import replica_reads

REPLICA = "replica"


@dataclasses.dataclass(frozen=True)
class Row:
    key: str
    cases: int
    breached: int
    breach_rate: float
    median_pause_seconds: int

    @property
    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def sla_performance(*, period_from: datetime, period_to: datetime, group_by: str = "category"):
    if group_by not in {"category", "agent"}:
        raise ValueError("group_by must be 'category' or 'agent'")

    field = "category" if group_by == "category" else "assigned_to_id"
    with replica_reads():
        rows = list(
            Dispute.objects.using(REPLICA)
            .filter(submitted_at__gte=period_from, submitted_at__lt=period_to)
            .values(field)
            .annotate(
                cases=Count("pk"),
                breached=Count("pk", filter=Q(breach_resolution=True) | Q(breach_ack=True)),
            )
            .order_by(field)
        )

    pauses = pause_durations(period_from=period_from, period_to=period_to, group_by=group_by)

    return [
        {
            "key": row[field] or "unassigned",
            "cases": row["cases"],
            "breached": row["breached"],
            # Rounded, because a breach rate quoted to fifteen decimal places is
            # a number nobody reads and a diff nobody can compare.
            "breach_rate": round(row["breached"] / row["cases"], 4) if row["cases"] else 0.0,
            "total_pause_seconds": pauses.get(row[field] or "unassigned", 0),
        }
        for row in rows
    ]


def pause_durations(*, period_from: datetime, period_to: datetime, group_by: str) -> dict[str, int]:
    """How long clocks were stopped, by category or by agent.

    Reported by agent on purpose. §4.4 says excessive pausing must be visible in
    the breach analysis view, by agent — a total across the whole queue tells a
    compliance officer that pausing happens, which they already knew.
    """
    events = SLAEvent.objects.filter(
        kind=SLAEvent.Kind.RESUMED,
        occurred_at__gte=period_from,
        occurred_at__lt=period_to,
    ).values("actor_id", "clock__subject_id", "clock_remaining_seconds")

    totals: dict[str, int] = {}
    subjects = {
        case.pk: case
        for case in Dispute.objects.filter(pk__in=[e["clock__subject_id"] for e in events]).only(
            "pk", "category", "assigned_to_id"
        )
    }

    for event in events:
        case = subjects.get(event["clock__subject_id"])
        if case is None:
            continue
        key = case.category if group_by == "category" else (event["actor_id"] or "unassigned")
        totals[key] = totals.get(key, 0) + _paused_seconds(event)
    return totals


def breach_causes(*, period_from: datetime, period_to: datetime) -> list[dict]:
    """Breaches grouped by their recorded cause.

    §11.5 step 5 requires every breach in an incident window to be annotated with
    its systems cause, and this is where that annotation earns its keep: a
    breach with a documented cause is defensible, and this view is where the
    undocumented ones become visible as a group.
    """
    with replica_reads():
        breached = list(
            Dispute.objects.using(REPLICA)
            .filter(submitted_at__gte=period_from, submitted_at__lt=period_to)
            .filter(Q(breach_resolution=True) | Q(breach_ack=True))
            .only("breach_reason")
        )

    causes: dict[str, int] = {}
    for case in breached:
        cause = " ".join((case.breach_reason or "").split()) or "undocumented"
        causes[cause] = causes.get(cause, 0) + 1

    return [
        {"cause": cause, "cases": count}
        for cause, count in sorted(causes.items(), key=lambda item: (-item[1], item[0]))
    ]


def summary(*, period_from: datetime, period_to: datetime) -> dict:
    from disputeshield.models import AuditRecord

    with replica_reads():
        cases = Dispute.objects.using(REPLICA).filter(
            submitted_at__gte=period_from, submitted_at__lt=period_to
        )
        aggregate = cases.aggregate(
            total=Count("pk"),
            breached=Count("pk", filter=Q(breach_resolution=True) | Q(breach_ack=True)),
            refunds=Sum("refund_amount_minor"),
            amount=Sum("amount_minor"),
        )
        resolved_count = cases.filter(resolved_at__isnull=False).count()
        # Which currencies the sums above are made of. `Sum("refund_amount_minor")`
        # adds minor units together without asking whether they are the same unit,
        # so a period holding both NGN and USD cases produces a total that adds
        # kobo to cents. The figure is still reported — narrowing it here would
        # hide cases from a regulatory count — but a caller now has what it needs
        # to refuse to present it as money.
        currencies = sorted(
            value for value in cases.values_list("currency", flat=True).distinct() if value
        )
        deflected = (
            AuditRecord.objects.using(REPLICA)
            .filter(
                event_type="intake.deflected",
                occurred_at__gte=period_from,
                occurred_at__lt=period_to,
            )
            .count()
        )
        average_pause = int(
            SLAEvent.objects.using(REPLICA)
            .filter(
                kind=SLAEvent.Kind.RESUMED,
                occurred_at__gte=period_from,
                occurred_at__lt=period_to,
            )
            .aggregate(v=Avg("clock_remaining_seconds"))["v"]
            or 0
        )

    return {
        "cases": aggregate["total"] or 0,
        "breached": aggregate["breached"] or 0,
        "resolved": resolved_count,
        # §11.2, rendered beside case volume on purpose (amplifier A2): a drop in
        # complaints during an outage must be visibly a deflection rather than
        # silently a suppression. A feature that reduces recorded complaints has
        # to be the most heavily instrumented thing in the product.
        "deflected": deflected,
        # Recorded, never executed (§3.3). This is a sum of what was promised,
        # and phase 9's exposure view is where it gets reconciled against what
        # the fintech's ledger says was paid.
        "recorded_refund_minor": aggregate["refunds"] or 0,
        "disputed_amount_minor": aggregate["amount"] or 0,
        "average_pause_seconds": average_pause,
        "currencies": currencies,
    }


def _paused_seconds(event: dict) -> int:
    return max(0, int(event.get("clock_remaining_seconds") or 0))
