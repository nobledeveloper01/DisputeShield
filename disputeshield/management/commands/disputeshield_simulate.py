"""Build a demo tenant that actually looks like a working queue (amplifier A19).

Every §3.1 persona is blocked without this. Tunde cannot test an integration
whose central behaviour is a 72-hour clock. Adaeze cannot evaluate a dashboard
with an empty queue. Ibrahim cannot rehearse the §11.5 runbook without an
incident to rehearse against.

The demo therefore contains the four things that make the product legible: a
**breach**, a **pause**, a **reopening** and a **mass incident**. A demo of the
happy path demonstrates a ticketing system.

Time is the hard part: a 72-hour SLA cannot be observed in a five-minute demo. So
a sandbox tenant carries a clock offset — a dangerous capability, refused at the
model layer for a live tenant, and asserted impossible there by a blocking CI gate.
"""

from __future__ import annotations

import json
import time
from datetime import time as clock_time
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from disputeshield.disputes import mass_events, service
from disputeshield.models import (
    BusinessCalendar,
    Dispute,
    MassEvent,
    SLAPolicy,
    SLAPolicyVersion,
    Tenant,
)
from disputeshield.models.dispute import Outcome, Status
from disputeshield.sla import clock as clock_service
from disputeshield.sla import sweeper
from disputeshield.tenancy import context
from disputeshield.tenancy.middleware import db_tenant_context


class Command(BaseCommand):
    help = "Create a sandbox tenant with a queue worth looking at."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--slug", default="sandbox")
        parser.add_argument("--cases", type=int, default=24)
        parser.add_argument(
            "--offset-days",
            type=int,
            default=5,
            help="Backdate the sandbox clock so long windows are observable.",
        )

    def handle(self, *args, **options) -> None:
        from django.db import transaction

        started = time.perf_counter()

        with transaction.atomic():
            tenant, _ = Tenant.objects.get_or_create(
                slug=options["slug"],
                defaults={
                    "name": "Sandbox",
                    # Refused for a live tenant at the model layer. The sandbox is
                    # the only place a clock may be moved.
                    "environment": Tenant.Environment.TEST,
                    "clock_offset_seconds": -options["offset_days"] * 86_400,
                },
            )
            if tenant.is_live:
                raise CommandError(
                    f"{tenant.slug!r} is a live tenant. The simulator only builds "
                    "sandbox tenants — moving a live clock would move a regulatory "
                    "deadline."
                )

            with context.tenant_context(tenant.pk), db_tenant_context(tenant.pk):
                version = self._policy(tenant)
                summary = self._build(tenant, version, options["cases"])

        elapsed = time.perf_counter() - started
        self.stdout.write(json.dumps({**summary, "seconds": round(elapsed, 1)}))

    def _policy(self, tenant) -> SLAPolicyVersion:
        calendar, fresh = BusinessCalendar.objects.get_or_create(
            tenant=tenant, name="Sandbox hours", defaults={"timezone_name": "Africa/Lagos"}
        )
        if fresh:
            from disputeshield.models import BusinessHoursWindow

            for weekday in range(5):
                BusinessHoursWindow.objects.create(
                    calendar=calendar,
                    weekday=weekday,
                    opens_at=clock_time(9, 0),
                    closes_at=clock_time(17, 0),
                )

        policy, _ = SLAPolicy.objects.get_or_create(tenant=tenant, category="failed_transfer")
        return policy.current_version or SLAPolicyVersion.objects.create(
            tenant=tenant,
            policy=policy,
            version=1,
            calendar=calendar,
            resolution_hours=8,
            warning_thresholds=[50, 80, 95],
            regulatory_reference="CBN Consumer Protection Framework s.4.2",
        )

    def _build(self, tenant, version, count: int) -> dict:
        now = timezone.now()
        offset = timedelta(seconds=tenant.clock_offset_seconds)

        cases: list[Dispute] = []
        for n in range(count):
            case = service.file_dispute(
                tenant=tenant,
                customer_ref=f"usr_sandbox_{n}",
                category="failed_transfer",
                description=(
                    f"Transfer to GTBank failed but I was debited. Reference SBX-{n:04d}."
                ),
                policy_version=version,
                transaction_ref=f"SBX-{n:04d}",
                amount_minor=250_000 * (n % 7 + 1),
                # Backdated, so a long window is observable now.
                submitted_at=now + offset + timedelta(hours=n),
                actor_type="system",
            )
            cases.append(case)

        for case in cases[: count // 2]:
            for step in (Status.ACKNOWLEDGED, Status.INVESTIGATING):
                service.transition(
                    dispute=case,
                    to=step,
                    actor_type="user",
                    actor_id="agt_demo",
                    reason="picked up",
                )

        # A pause: the clock stopped, with a reason, visible in the record.
        paused = cases[0]
        clock_service.pause(
            clock=paused.clock,
            reason="awaiting the customer's bank statement",
            actor_type="user",
            actor_id="agt_demo",
        )

        # A reopening: the customer disputed the outcome.
        reopened = cases[1]
        service.resolve(
            dispute=reopened,
            outcome=Outcome.REJECTED,
            notes="No evidence of a failed transfer found.",
            actor_type="user",
            actor_id="agt_demo",
        )
        service.transition(
            dispute=reopened,
            to=Status.REOPENED,
            actor_type="user",
            actor_id="agt_demo",
            reason="customer supplied the bank statement",
        )

        # A mass incident: one root cause, many cases, resolved individually.
        event = MassEvent.objects.create(
            tenant=tenant,
            title="GTBank rail outage",
            root_cause="Provider timeout handling",
            created_by="agt_demo",
        )
        members = [c for c in cases[2 : count // 2] if c.status == Status.INVESTIGATING]
        for case in members:
            mass_events.add(event=event, dispute=case, actor_id="agt_demo")
        applied = mass_events.apply_outcome(
            event=event,
            outcome=Outcome.UPHELD,
            notes="Provider confirmed the rail failure; reversals issued.",
            actor_id="agt_demo",
        )

        # A breach: the sweep runs against the backdated clock and fires.
        swept = sweeper.sweep()

        return {
            "tenant": tenant.pk,
            "environment": tenant.environment,
            "cases": len(cases),
            "paused": paused.reference,
            "reopened": reopened.reference,
            "mass_event_applied": applied.applied,
            "deadlines_fired": swept.fired,
        }
