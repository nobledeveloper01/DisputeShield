"""What is at stake, and what has actually been paid (amplifier A16).

This module makes the product legible to a CFO, which is who signs the enterprise
contract. `refund_amount_minor` already exists on every resolved case, and today
it is recorded and never summed.

**Nothing in this package may reach a payment.** §3.3 puts executing refunds under
permanent *Won't*, and `tests/test_no_money_movement.py` walks the call graph of
every module under `disputeshield/finance/` to assert it reaches no connector, no
HTTP client and no outbound write. That gate is permanent — the credibility of an
evidence system depends on it having no ability to act on the thing it holds
evidence about.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone

from disputeshield.models import Dispute, SettlementConfirmation
from disputeshield.models.dispute import Outcome, Status

OPEN_STATUSES = [
    Status.SUBMITTED,
    Status.ACKNOWLEDGED,
    Status.INVESTIGATING,
    Status.AWAITING_CUSTOMER,
    Status.ESCALATED,
    Status.REOPENED,
]

AGE_BANDS = ((0, 7), (7, 30), (30, 90), (90, None))


@dataclasses.dataclass(frozen=True)
class Reconciliation:
    promised_minor: int
    settled_minor: int
    unreconciled_cases: int

    @property
    def delta_minor(self) -> int:
        """What was promised and not yet paid. The interesting number.

        Reported rather than hidden, and signed: a negative delta means more was
        paid than promised, which is its own finding.
        """
        return self.promised_minor - self.settled_minor


def under_dispute(*, group_by: str = "category") -> list[dict]:
    """Value at stake on open cases.

    Integer minor units throughout. A finance view that reports a rounded major
    unit is a view somebody reconciles against a ledger and finds off by cents.
    """
    if group_by not in {"category", "currency", "provider"}:
        raise ValueError("group_by must be 'category', 'currency' or 'provider'")

    field = {"category": "category", "currency": "currency", "provider": "category"}[group_by]

    rows = (
        Dispute.objects.filter(status__in=OPEN_STATUSES)
        .values(field)
        .annotate(cases=Count("pk"), amount_minor=Sum("amount_minor"))
        .order_by(field)
    )
    return [
        {
            "key": row[field] or "unspecified",
            "cases": row["cases"],
            "amount_minor": row["amount_minor"] or 0,
        }
        for row in rows
    ]


def by_age(*, now: datetime | None = None) -> list[dict]:
    """Open exposure bucketed by how long it has been open.

    Age matters to a CFO differently than it does to compliance: an old open case
    is provisioning that has been sitting on the balance sheet, not just an SLA
    at risk.
    """
    now = now or timezone.now()
    bands = []
    for start, end in AGE_BANDS:
        queryset = Dispute.objects.filter(status__in=OPEN_STATUSES)
        queryset = queryset.filter(submitted_at__lte=now - timedelta(days=start))
        if end is not None:
            queryset = queryset.filter(submitted_at__gt=now - timedelta(days=end))
        aggregate = queryset.aggregate(cases=Count("pk"), amount_minor=Sum("amount_minor"))
        bands.append(
            {
                "from_days": start,
                "to_days": end,
                "cases": aggregate["cases"] or 0,
                "amount_minor": aggregate["amount_minor"] or 0,
            }
        )
    return bands


def expected_loss(*, lookback_days: int = 365, now: datetime | None = None) -> dict:
    """Projected liability on open cases, from this tenant's own history.

    The uphold rate is measured, never assumed — a projection built on an
    industry average tells a CFO about an industry. If there is no history the
    projection is `None` rather than a guess, because a made-up number in a
    provisioning view is worse than an absent one.
    """
    now = now or timezone.now()
    since = now - timedelta(days=lookback_days)

    decided = Dispute.objects.filter(resolved_at__isnull=False, resolved_at__gte=since)
    totals = decided.aggregate(
        decided_count=Count("pk"),
        upheld=Count("pk", filter=Q(outcome__in=[Outcome.UPHELD, Outcome.PARTIAL])),
    )

    if not totals["decided_count"]:
        return {"uphold_rate": None, "open_amount_minor": _open_amount(), "expected_minor": None}

    rate = totals["upheld"] / totals["decided_count"]
    open_amount = _open_amount()
    return {
        "uphold_rate": round(rate, 4),
        "open_amount_minor": open_amount,
        "expected_minor": int(open_amount * rate),
        "sample_size": totals["decided_count"],
    }


def reconcile(*, period_from: datetime, period_to: datetime) -> Reconciliation:
    """What was promised against what the ledger says was paid.

    DisputeShield knows the first. Only the fintech's ledger knows the second, so
    the gap is only visible if they send settlements back — and the gap is the
    whole point of the view.
    """
    promised = (
        Dispute.objects.filter(
            resolved_at__gte=period_from,
            resolved_at__lt=period_to,
            refund_amount_minor__isnull=False,
        ).aggregate(total=Sum("refund_amount_minor"))["total"]
        or 0
    )

    settled = (
        SettlementConfirmation.objects.filter(
            settled_at__gte=period_from, settled_at__lt=period_to
        ).aggregate(total=Sum("amount_minor"))["total"]
        or 0
    )

    unreconciled = (
        Dispute.objects.filter(
            resolved_at__gte=period_from,
            resolved_at__lt=period_to,
            refund_amount_minor__isnull=False,
        )
        .filter(settlements__isnull=True)
        .distinct()
        .count()
    )

    return Reconciliation(
        promised_minor=promised, settled_minor=settled, unreconciled_cases=unreconciled
    )


def summary(*, period_from: datetime, period_to: datetime) -> dict:
    reconciliation = reconcile(period_from=period_from, period_to=period_to)
    return {
        "under_dispute": under_dispute(),
        "by_age": by_age(),
        "expected_loss": expected_loss(),
        "reconciliation": {
            "promised_minor": reconciliation.promised_minor,
            "settled_minor": reconciliation.settled_minor,
            # Surfaced, never netted away.
            "delta_minor": reconciliation.delta_minor,
            "unreconciled_cases": reconciliation.unreconciled_cases,
        },
    }


def _open_amount() -> int:
    return (
        Dispute.objects.filter(status__in=OPEN_STATUSES).aggregate(total=Sum("amount_minor"))[
            "total"
        ]
        or 0
    )
