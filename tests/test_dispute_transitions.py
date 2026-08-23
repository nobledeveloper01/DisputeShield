"""§3.4 — every transition, enumerated from the table rather than hand-listed.

The gate this file exists for: **every** transition writes an audit record naming
the actor, the reason and the state of the SLA clock at that instant. Driving the
assertion from `TRANSITIONS` means a transition added in a later phase is covered
the moment it is added, rather than only if somebody remembered to write a test
beside it.
"""

from __future__ import annotations

import pytest

from disputeshield.disputes import service
from disputeshield.disputes.states import TERMINAL, TRANSITIONS, ClockEffect, IllegalTransition
from disputeshield.models import AuditRecord, SLAClock
from disputeshield.models.dispute import Outcome, Status

pytestmark = pytest.mark.django_db


def drive_to(dispute, target: str, *, as_tenant, tenant) -> None:
    """Walk the shortest legal path to a state, so each test starts where it means to."""
    routes = {
        Status.SUBMITTED: [],
        Status.ACKNOWLEDGED: [Status.ACKNOWLEDGED],
        Status.INVESTIGATING: [Status.ACKNOWLEDGED, Status.INVESTIGATING],
        Status.AWAITING_CUSTOMER: [
            Status.ACKNOWLEDGED,
            Status.INVESTIGATING,
            Status.AWAITING_CUSTOMER,
        ],
        Status.ESCALATED: [Status.ACKNOWLEDGED, Status.INVESTIGATING, Status.ESCALATED],
        Status.RESOLVED: [Status.ACKNOWLEDGED, Status.INVESTIGATING, Status.RESOLVED],
        Status.REOPENED: [
            Status.ACKNOWLEDGED,
            Status.INVESTIGATING,
            Status.RESOLVED,
            Status.REOPENED,
        ],
    }
    for step in routes[target]:
        service.transition(
            dispute=dispute,
            to=step,
            actor_type="user",
            actor_id="agt_1",
            reason="driving to a start state",
        )


class TestTheTransitionTable:
    def test_every_transition_writes_an_audit_record_with_actor_reason_and_clock(
        self, tenant_a, make_dispute, as_tenant
    ):
        """The phase 3 exit gate, driven from the table itself."""
        uncovered = []

        for rule in TRANSITIONS:
            if rule.source in TERMINAL:
                continue
            dispute = make_dispute(tenant_a, customer_ref=f"usr_{rule.source}_{rule.target}")
            with as_tenant(tenant_a):
                try:
                    drive_to(dispute, rule.source, as_tenant=as_tenant, tenant=tenant_a)
                except (IllegalTransition, KeyError):
                    uncovered.append(f"{rule.source} unreachable")
                    continue

                actor_type = "system" if "user" not in rule.actor_types else "user"
                before = AuditRecord.objects.count()
                service.transition(
                    dispute=dispute,
                    to=rule.target,
                    actor_type=actor_type,
                    actor_id="" if actor_type == "system" else "agt_1",
                    reason="a recorded reason",
                )
                record = AuditRecord.objects.order_by("-sequence").first()

                assert AuditRecord.objects.count() > before, f"{rule.key} wrote no audit record"
                assert record.event_type == f"dispute.{rule.trigger}"
                assert record.payload["from"] == rule.source
                assert record.payload["to"] == rule.target
                assert record.payload["reason"] == "a recorded reason"
                assert "clock_remaining_seconds" in record.payload, (
                    f"{rule.key} recorded no clock state — that field is what makes a "
                    "breach explainable six months later"
                )
                assert record.actor_type == actor_type

        assert not uncovered, uncovered

    def test_every_transition_that_pauses_the_clock_requires_a_reason(self):
        """A clock effect changes what the firm owes the customer."""
        for rule in TRANSITIONS:
            if rule.clock_effect in {ClockEffect.PAUSE, ClockEffect.RESUME, ClockEffect.STOP}:
                if rule.trigger == "close":
                    continue  # expiry of the reopen window; the reason is the rule itself
                assert rule.requires_reason, (
                    f"{rule.key} changes the clock without requiring a reason"
                )

    def test_the_table_has_no_transition_out_of_a_terminal_state(self):
        for rule in TRANSITIONS:
            assert rule.source not in TERMINAL, f"{rule.key} leaves a terminal state"

    def test_auto_close_may_only_be_performed_by_the_system(self):
        """A human closing a case for silence and recording it as automatic would
        misattribute a decision to the system."""
        rule = next(r for r in TRANSITIONS if r.trigger == "auto_close")
        assert rule.actor_types == ("system",)


class TestIllegalMoves:
    def test_an_undefined_transition_is_refused_with_the_legal_options(
        self, tenant_a, make_dispute, as_tenant
    ):
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a), pytest.raises(IllegalTransition, match="may move to"):
            service.transition(
                dispute=dispute,
                to=Status.CLOSED,
                actor_type="user",
                actor_id="agt_1",
                reason="skipping the whole lifecycle",
            )

    def test_a_reasonless_escalation_is_refused(self, tenant_a, make_dispute, as_tenant):
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            drive_to(dispute, Status.INVESTIGATING, as_tenant=as_tenant, tenant=tenant_a)
            with pytest.raises(service.ReasonRequired):
                service.transition(
                    dispute=dispute,
                    to=Status.ESCALATED,
                    actor_type="user",
                    actor_id="agt_1",
                    reason="  ",
                )

    def test_a_user_cannot_perform_an_auto_close(self, tenant_a, make_dispute, as_tenant):
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            drive_to(dispute, Status.AWAITING_CUSTOMER, as_tenant=as_tenant, tenant=tenant_a)
            with pytest.raises(service.ActorNotPermitted):
                service.transition(
                    dispute=dispute,
                    to=Status.AUTO_CLOSED,
                    actor_type="user",
                    actor_id="agt_1",
                    reason="customer went quiet",
                )


class TestClockEffects:
    def test_requesting_information_pauses_the_clock(self, tenant_a, make_dispute, as_tenant):
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            drive_to(dispute, Status.INVESTIGATING, as_tenant=as_tenant, tenant=tenant_a)
            service.transition(
                dispute=dispute,
                to=Status.AWAITING_CUSTOMER,
                actor_type="user",
                actor_id="agt_1",
                reason="asked for proof of payment",
            )
            dispute.clock.refresh_from_db()
        assert dispute.clock.state == SLAClock.State.PAUSED

    def test_the_customer_responding_resumes_it(self, tenant_a, make_dispute, as_tenant):
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            drive_to(dispute, Status.AWAITING_CUSTOMER, as_tenant=as_tenant, tenant=tenant_a)
            service.transition(
                dispute=dispute,
                to=Status.INVESTIGATING,
                actor_type="user",
                actor_id="agt_1",
                reason="customer sent the receipt",
            )
            dispute.clock.refresh_from_db()
        assert dispute.clock.state == SLAClock.State.RUNNING
        assert len(dispute.clock.paused_intervals) == 1

    def test_resolving_stops_the_clock_and_records_the_outcome(
        self, tenant_a, make_dispute, as_tenant
    ):
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            drive_to(dispute, Status.INVESTIGATING, as_tenant=as_tenant, tenant=tenant_a)
            service.resolve(
                dispute=dispute,
                outcome=Outcome.UPHELD,
                notes="Reversal confirmed with the provider; credit issued.",
                refund_amount_minor=5_000_000,
                actor_type="user",
                actor_id="agt_1",
            )
            dispute.clock.refresh_from_db()

        assert dispute.status == Status.RESOLVED
        assert dispute.clock.state == SLAClock.State.STOPPED
        assert dispute.refund_amount_minor == 5_000_000

    def test_a_recorded_refund_is_never_executed(self, tenant_a, make_dispute, as_tenant):
        """§3.3 puts moving money under permanent Won't. Phase 9 adds the
        call-graph gate; this asserts the field is inert today."""
        import inspect

        source = inspect.getsource(service)
        for forbidden in ("payout", "transfer(", "charge(", "requests.post", "httpx."):
            assert forbidden not in source, (
                f"the dispute service references {forbidden!r} — nothing here may reach a payment"
            )


class TestPauseAbuseIsVisible:
    def test_every_pause_is_attributable_to_an_agent(self, tenant_a, make_dispute, as_tenant):
        """§4.4: excessive pausing must be visible in breach analysis, by agent.
        That is only possible if every pause names one."""
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            drive_to(dispute, Status.INVESTIGATING, as_tenant=as_tenant, tenant=tenant_a)
            service.transition(
                dispute=dispute,
                to=Status.AWAITING_CUSTOMER,
                actor_type="user",
                actor_id="agt_ngozi",
                reason="awaiting bank statement",
            )
            # Evaluated inside the context, deliberately. A lazy queryset that
            # escapes the tenant scope executes with no RLS context and returns
            # zero rows — a safe failure, but a silent one that reads as "there
            # were no pauses" rather than as "you asked from outside a tenant".
            events = list(dispute.clock.events.filter(kind="paused"))

        assert len(events) == 1
        assert events[0].actor_id == "agt_ngozi"
        assert events[0].reason == "awaiting bank statement"
