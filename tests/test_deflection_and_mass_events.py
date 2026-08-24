"""Deflection (A2) and mass-incident mode (A3).

Both features reduce work, and both can be wrong in ways that look like success:

  * Deflection that is wrong is **complaint suppression**, which is the worst
    accusation a regulator can make about a complaints system.
  * Mass resolution that is wrong is **a bulk edit over immutable records**,
    which is precisely what §8.3 forbids.

So the gates here are about the guardrails rather than the happy paths.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from disputeshield.disputes import mass_events, service
from disputeshield.intake import deflection
from disputeshield.models import (
    AuditRecord,
    Incident,
    IncidentSubscription,
    MassEvent,
    MassEventMembership,
)
from disputeshield.models.dispute import Outcome, Status

pytestmark = pytest.mark.django_db

UTC = UTC
NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


@pytest.fixture
def incident(tenant_a, as_tenant):
    with as_tenant(tenant_a):
        return Incident.objects.create(
            tenant=tenant_a,
            title="GTBank transfers failing",
            customer_message=(
                "We know: transfers to GTBank between 09:10 and 11:40 failed. "
                "Reversals are running and we expect them complete by 18:00."
            ),
            started_at=NOW,
            expected_resolution_at=NOW + timedelta(hours=8),
            match_categories=["failed_transfer"],
        )


class TestDeflectionGuardrail:
    """A2's guardrail: deflection may never be the only path."""

    def test_file_anyway_is_present_even_when_nothing_is_deflected(self, tenant_a, as_tenant):
        with as_tenant(tenant_a):
            result = deflection.check(tenant=tenant_a, category="failed_transfer")
        assert result.deflected is False
        assert result.as_dict()["file_anyway"] is True

    def test_file_anyway_is_present_when_something_is_deflected(
        self, tenant_a, incident, as_tenant
    ):
        with as_tenant(tenant_a):
            result = deflection.check(tenant=tenant_a, category="failed_transfer")
        assert result.deflected is True
        assert result.as_dict()["file_anyway"] is True

    def test_there_is_no_configuration_surface_that_can_remove_it(self):
        """Asserted against the shape of the code, not against a configuration
        we happen to have written. A boolean a tenant can set to False during an
        outage is one that will be set to False during an outage.
        """
        from disputeshield import conf
        from disputeshield.models import Incident as IncidentModel

        # Not a settings key.
        assert not any("FILE_ANYWAY" in key for key in conf.DEFAULTS)
        # Not a column on the incident a compliance user edits.
        fields = {f.name for f in IncidentModel._meta.get_fields()}
        assert not any("file_anyway" in name for name in fields)
        # And it is a module constant, so nothing at runtime can rebind it per
        # tenant without editing the source.
        assert deflection.FILE_ANYWAY_ALWAYS_AVAILABLE is True

    def test_a_resolved_incident_stops_deflecting(self, tenant_a, incident, as_tenant):
        """An incident nobody closed would deflect complaints forever."""
        with as_tenant(tenant_a):
            incident.status = Incident.Status.RESOLVED
            incident.save(update_fields=["status"])
            assert deflection.check(tenant=tenant_a, category="failed_transfer").deflected is False

    def test_the_matcher_is_narrow(self, tenant_a, incident, as_tenant):
        """A broad matcher deflects complaints that have nothing to do with the
        outage — which is exactly how deflection becomes suppression."""
        with as_tenant(tenant_a):
            assert deflection.check(tenant=tenant_a, category="card_chargeback").deflected is False

    def test_an_incident_with_no_matcher_matches_nothing(self, tenant_a, as_tenant):
        with as_tenant(tenant_a):
            Incident.objects.create(
                tenant=tenant_a, title="Vague", customer_message="…", started_at=NOW
            )
            assert deflection.check(tenant=tenant_a, category="failed_transfer").deflected is False


class TestDeflectionIsCounted:
    def test_subscribing_records_a_deflection(self, tenant_a, incident, as_tenant):
        with as_tenant(tenant_a):
            deflection.subscribe(tenant=tenant_a, incident=incident, customer_ref_hash="a" * 64)
            assert AuditRecord.objects.filter(event_type="intake.deflected").count() == 1
            assert deflection.deflections_total(tenant=tenant_a) == 1

    def test_subscribing_twice_counts_once(self, tenant_a, incident, as_tenant):
        with as_tenant(tenant_a):
            for _ in range(2):
                deflection.subscribe(tenant=tenant_a, incident=incident, customer_ref_hash="a" * 64)
            assert IncidentSubscription.objects.count() == 1
            assert deflection.deflections_total(tenant=tenant_a) == 1

    def test_the_count_comes_from_the_audit_trail_not_a_counter(
        self, tenant_a, incident, as_tenant
    ):
        """A counter can be reset; an append-only trail cannot. For a number whose
        whole purpose is showing complaints were not suppressed, that matters."""
        import inspect

        source = inspect.getsource(deflection.deflections_total)
        assert "AuditRecord" in source

    def test_a_deflection_records_the_hash_not_the_customer(self, tenant_a, incident, as_tenant):
        with as_tenant(tenant_a):
            deflection.subscribe(tenant=tenant_a, incident=incident, customer_ref_hash="a" * 64)
            record = AuditRecord.objects.get(event_type="intake.deflected")
        assert record.payload["customer_ref_hash"] == "a" * 64


class TestMassEventFanOut:
    @pytest.fixture
    def event_with_cases(self, tenant_a, make_dispute, make_policy, as_tenant):
        version = make_policy(tenant_a, resolution_hours=8)
        cases = [
            make_dispute(tenant_a, policy_version=version, customer_ref=f"usr_{n}")
            for n in range(5)
        ]
        with as_tenant(tenant_a):
            for case in cases:
                for step in (Status.ACKNOWLEDGED, Status.INVESTIGATING):
                    service.transition(
                        dispute=case,
                        to=step,
                        actor_type="user",
                        actor_id="agt_1",
                        reason="picking up",
                    )
            event = MassEvent.objects.create(
                tenant=tenant_a,
                title="GTBank rail outage",
                root_cause="Provider timeout handling",
                created_by="agt_1",
            )
            for case in cases:
                mass_events.add(event=event, dispute=case, actor_id="agt_1")
        return event, cases

    def test_the_fan_out_writes_one_audit_record_per_case(
        self, tenant_a, event_with_cases, as_tenant
    ):
        event, cases = event_with_cases
        with as_tenant(tenant_a):
            before = AuditRecord.objects.filter(event_type="dispute.resolve").count()
            result = mass_events.apply_outcome(
                event=event,
                outcome=Outcome.UPHELD,
                notes="Provider confirmed the rail failure; reversals issued.",
                actor_id="agt_1",
            )
            after = AuditRecord.objects.filter(event_type="dispute.resolve").count()

        assert result.applied == len(cases)
        assert after - before == len(cases), "a batch resolution wrote fewer records than cases"

    def test_the_fan_out_executes_no_bulk_update(self, tenant_a, event_with_cases, as_tenant):
        """§8.3 forbids a bulk-edit surface over auditable records. Asserted by
        reading the SQL, because "we wrote it as a loop" is a claim about intent."""
        event, _ = event_with_cases
        with as_tenant(tenant_a), CaptureQueriesContext(connection) as captured:
            mass_events.apply_outcome(
                event=event, outcome=Outcome.UPHELD, notes="Confirmed.", actor_id="agt_1"
            )

        bulk = [
            q["sql"]
            for q in captured
            if q["sql"].startswith("UPDATE")
            and '"disputeshield_dispute"' in q["sql"]
            and " IN (" in q["sql"]
        ]
        assert not bulk, f"the fan-out issued a bulk UPDATE over cases: {bulk[:2]}"

    def test_each_record_names_the_investigation_it_came_from(
        self, tenant_a, event_with_cases, as_tenant
    ):
        """So a supervisor asking why *this* case was resolved this way finds a
        specific answer rather than "it was part of a batch"."""
        event, cases = event_with_cases
        with as_tenant(tenant_a):
            mass_events.apply_outcome(
                event=event, outcome=Outcome.UPHELD, notes="Confirmed.", actor_id="agt_1"
            )
            record = AuditRecord.objects.filter(
                event_type="dispute.resolve", subject_id=cases[0].pk
            ).first()

        assert record.payload["mass_event_id"] == event.pk
        assert record.payload["mass_event_title"] == "GTBank rail outage"

    def test_every_case_is_resolved_and_its_clock_stopped(
        self, tenant_a, event_with_cases, as_tenant
    ):
        event, cases = event_with_cases
        with as_tenant(tenant_a):
            mass_events.apply_outcome(
                event=event, outcome=Outcome.UPHELD, notes="Confirmed.", actor_id="agt_1"
            )
            for case in cases:
                case.refresh_from_db()
                case.clock.refresh_from_db()
                assert case.status == Status.RESOLVED
                assert case.outcome == Outcome.UPHELD
                assert case.clock.state == "stopped"

    def test_membership_does_not_pause_a_clock(self, tenant_a, event_with_cases, as_tenant):
        """A3's guardrail: no case's clock is paused by group membership."""
        _, cases = event_with_cases
        with as_tenant(tenant_a):
            for case in cases:
                case.clock.refresh_from_db()
                assert case.clock.state == "running"
                assert case.clock.paused_intervals == []

    def test_a_case_in_the_wrong_state_is_skipped_not_forced(
        self, tenant_a, event_with_cases, as_tenant
    ):
        event, cases = event_with_cases
        with as_tenant(tenant_a):
            service.resolve(
                dispute=cases[0],
                outcome=Outcome.REJECTED,
                notes="Resolved individually first.",
                actor_type="user",
                actor_id="agt_2",
            )
            result = mass_events.apply_outcome(
                event=event, outcome=Outcome.UPHELD, notes="Confirmed.", actor_id="agt_1"
            )
            cases[0].refresh_from_db()

        assert cases[0].reference in result.skipped
        assert cases[0].outcome == Outcome.REJECTED, "a mass apply overwrote an individual outcome"
        assert result.applied == len(cases) - 1

    def test_a_reasonless_mass_resolution_is_refused(self, tenant_a, event_with_cases, as_tenant):
        event, _ = event_with_cases
        with as_tenant(tenant_a), pytest.raises(ValueError, match="notes"):
            mass_events.apply_outcome(
                event=event, outcome=Outcome.UPHELD, notes="  ", actor_id="agt_1"
            )


class TestMembership:
    @pytest.fixture
    def event_and_case(self, tenant_a, make_dispute, make_policy, as_tenant):
        version = make_policy(tenant_a)
        case = make_dispute(tenant_a, policy_version=version)
        with as_tenant(tenant_a):
            event = MassEvent.objects.create(
                tenant=tenant_a, title="Rail outage", created_by="agt_1"
            )
            membership = mass_events.add(event=event, dispute=case, actor_id="agt_1")
        return event, case, membership

    def test_removing_a_case_preserves_what_happened_while_it_was_a_member(
        self, tenant_a, event_and_case, as_tenant
    ):
        """The membership is closed, never deleted: that a case was once grouped
        with four thousand others is part of how it was handled."""
        _, case, membership = event_and_case
        with as_tenant(tenant_a):
            mass_events.remove(
                membership=membership,
                actor_id="agt_1",
                reason="different root cause on closer inspection",
            )
            membership.refresh_from_db()

            assert MassEventMembership.objects.filter(pk=membership.pk).exists()
            assert membership.removed_at is not None
            assert membership.added_by == "agt_1"
            assert membership.removal_reason.startswith("different root cause")

            events = set(
                AuditRecord.objects.filter(subject_id=case.pk).values_list("event_type", flat=True)
            )
        assert {"mass_event.case_added", "mass_event.case_removed"} <= events

    def test_a_removed_case_is_not_touched_by_the_fan_out(
        self, tenant_a, event_and_case, as_tenant
    ):
        event, case, membership = event_and_case
        with as_tenant(tenant_a):
            for step in (Status.ACKNOWLEDGED, Status.INVESTIGATING):
                service.transition(
                    dispute=case, to=step, actor_type="user", actor_id="agt_1", reason="x"
                )
            mass_events.remove(membership=membership, actor_id="agt_1", reason="not this one")
            result = mass_events.apply_outcome(
                event=event, outcome=Outcome.UPHELD, notes="Confirmed.", actor_id="agt_1"
            )
            case.refresh_from_db()

        assert result.applied == 0
        assert case.status == Status.INVESTIGATING

    def test_removing_without_a_reason_is_refused(self, tenant_a, event_and_case, as_tenant):
        _, _, membership = event_and_case
        with as_tenant(tenant_a), pytest.raises(ValueError, match="reason"):
            mass_events.remove(membership=membership, actor_id="agt_1", reason="")

    def test_adding_a_case_twice_is_idempotent(self, tenant_a, event_and_case, as_tenant):
        event, case, _ = event_and_case
        with as_tenant(tenant_a):
            mass_events.add(event=event, dispute=case, actor_id="agt_1")
            assert MassEventMembership.objects.filter(mass_event=event, dispute=case).count() == 1
            assert AuditRecord.objects.filter(event_type="mass_event.case_added").count() == 1
