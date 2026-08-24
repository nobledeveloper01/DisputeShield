"""Legal hold, retention and erasure (amplifier A7).

§11.7 promises seven-year retention *and* a tested deletion procedure. The moment
a case is in litigation those promises point in opposite directions, and
automated deletion of held material is spoliation of evidence. The gates here:

  * an erasure request against held material is **refused with a recorded
    reason**, and the refusal is itself auditable — refusing silently is its own
    violation;
  * retention sweeps skip held material, and releasing a hold re-enters it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone as django_timezone

from disputeshield.models import AuditRecord, ErasureRequest, LegalHold
from disputeshield.retention import holds, sweep

pytestmark = pytest.mark.django_db

UTC = UTC


@pytest.fixture
def closed_long_ago(tenant_a, make_dispute, make_policy, as_tenant):
    """A case closed eight years ago — past the seven-year window."""
    version = make_policy(tenant_a)
    dispute = make_dispute(tenant_a, policy_version=version, customer_ref="usr_9931")
    with as_tenant(tenant_a):
        dispute.closed_at = django_timezone.now() - timedelta(days=365 * 8)
        dispute.save(update_fields=["closed_at"])
    return dispute


class TestPlacingAHold:
    def test_a_hold_needs_a_matter_and_a_reason(self, tenant_a, closed_long_ago, as_tenant):
        with as_tenant(tenant_a), pytest.raises(ValueError, match="matter reference"):
            holds.place(
                tenant=tenant_a,
                name="x",
                matter_reference="",
                reason="",
                scope=LegalHold.Scope.DISPUTE,
                placed_by="agt_1",
                dispute=closed_long_ago,
            )

    def test_a_hold_that_covers_nothing_is_refused(self, tenant_a, as_tenant):
        """A hold with no target reads as protection while protecting nothing."""
        with as_tenant(tenant_a), pytest.raises(ValidationError, match="covers nothing"):
            holds.place(
                tenant=tenant_a,
                name="empty",
                matter_reference="LIT-1",
                reason="because",
                scope=LegalHold.Scope.CATEGORY,
                placed_by="agt_1",
            )

    def test_placing_a_hold_is_audited(self, tenant_a, closed_long_ago, as_tenant):
        with as_tenant(tenant_a):
            holds.place(
                tenant=tenant_a,
                name="Okafor v Acme",
                matter_reference="LIT-2026-4",
                reason="Claim filed",
                scope=LegalHold.Scope.DISPUTE,
                placed_by="agt_1",
                dispute=closed_long_ago,
            )
            record = AuditRecord.objects.get(event_type="legal_hold.placed")
        assert record.payload["matter_reference"] == "LIT-2026-4"

    @pytest.mark.parametrize(
        ("scope", "target"),
        [
            (LegalHold.Scope.DISPUTE, "dispute"),
            (LegalHold.Scope.CUSTOMER, "customer"),
            (LegalHold.Scope.CATEGORY, "category"),
            (LegalHold.Scope.PERIOD, "period"),
        ],
    )
    def test_every_scope_covers_the_case(self, tenant_a, closed_long_ago, scope, target, as_tenant):
        kwargs = {
            "dispute": {"dispute": closed_long_ago},
            "customer": {"customer_ref_hash": closed_long_ago.customer_ref_hash},
            "category": {"category": closed_long_ago.category},
            "period": {
                "period_from": closed_long_ago.submitted_at - timedelta(days=1),
                "period_to": closed_long_ago.submitted_at + timedelta(days=1),
            },
        }[target]

        with as_tenant(tenant_a):
            hold = holds.place(
                tenant=tenant_a,
                name=f"{scope} hold",
                matter_reference="LIT-1",
                reason="matter",
                scope=scope,
                placed_by="agt_1",
                **kwargs,
            )
            assert hold.covers(closed_long_ago)
            assert holds.check(closed_long_ago).held is True


class TestReleasingAHold:
    @pytest.fixture
    def hold(self, tenant_a, closed_long_ago, as_tenant):
        with as_tenant(tenant_a):
            return holds.place(
                tenant=tenant_a,
                name="Okafor v Acme",
                matter_reference="LIT-2026-4",
                reason="Claim filed",
                scope=LegalHold.Scope.DISPUTE,
                placed_by="agt_1",
                dispute=closed_long_ago,
            )

    def test_releasing_needs_a_second_approver(self, tenant_a, hold, as_tenant):
        with as_tenant(tenant_a), pytest.raises(holds.ReleaseRequiresTwoPeople):
            holds.release(hold=hold, released_by="agt_1", approved_by="", reason="matter closed")

    def test_the_approver_must_be_a_different_person(self, tenant_a, hold, as_tenant):
        """A two-person rule one person can satisfy twice is a one-person rule."""
        with as_tenant(tenant_a), pytest.raises(holds.ReleaseRequiresTwoPeople, match="other"):
            holds.release(
                hold=hold, released_by="agt_1", approved_by="agt_1", reason="matter closed"
            )

    def test_releasing_records_both_people_and_the_reason(self, tenant_a, hold, as_tenant):
        with as_tenant(tenant_a):
            holds.release(
                hold=hold,
                released_by="agt_1",
                approved_by="agt_2",
                reason="Matter settled; counsel confirms release",
            )
            record = AuditRecord.objects.get(event_type="legal_hold.released")

        assert hold.released_by == "agt_1"
        assert hold.release_approved_by == "agt_2"
        assert record.actor_id == "agt_1"
        assert record.payload["approved_by"] == "agt_2"

    def test_a_released_hold_no_longer_covers(self, tenant_a, hold, closed_long_ago, as_tenant):
        with as_tenant(tenant_a):
            holds.release(hold=hold, released_by="agt_1", approved_by="agt_2", reason="settled")
            assert holds.check(closed_long_ago).held is False


class TestRetentionSweep:
    def test_it_reports_rather_than_deletes_by_default(self, tenant_a, closed_long_ago, as_tenant):
        """A retention sweep that deletes on its first accidental invocation is
        the most destructive thing in this codebase."""
        import inspect

        signature = inspect.signature(sweep.sweep)
        assert signature.parameters["dry_run"].default is True

        with as_tenant(tenant_a):
            result = sweep.sweep()
        assert result.examined == 1
        assert result.expired == 1

    def test_a_held_case_is_skipped(self, tenant_a, closed_long_ago, as_tenant):
        with as_tenant(tenant_a):
            holds.place(
                tenant=tenant_a,
                name="Okafor v Acme",
                matter_reference="LIT-2026-4",
                reason="Claim filed",
                scope=LegalHold.Scope.DISPUTE,
                placed_by="agt_1",
                dispute=closed_long_ago,
            )
            result = sweep.sweep()

        assert result.expired == 0
        assert result.skipped_on_hold == 1
        assert result.held_references == ("LIT-2026-4",)

    def test_the_skip_is_recorded(self, tenant_a, closed_long_ago, as_tenant):
        """A case still present after its retention window needs a reason in the
        record, rather than looking like a sweep that missed it."""
        with as_tenant(tenant_a):
            holds.place(
                tenant=tenant_a,
                name="h",
                matter_reference="LIT-1",
                reason="r",
                scope=LegalHold.Scope.DISPUTE,
                placed_by="agt_1",
                dispute=closed_long_ago,
            )
            sweep.sweep()
            record = AuditRecord.objects.get(event_type="retention.skipped_on_hold")
        assert record.payload["matter_references"] == ["LIT-1"]

    def test_releasing_a_hold_re_enters_the_case_into_the_schedule(
        self, tenant_a, closed_long_ago, as_tenant
    ):
        with as_tenant(tenant_a):
            hold = holds.place(
                tenant=tenant_a,
                name="h",
                matter_reference="LIT-1",
                reason="r",
                scope=LegalHold.Scope.DISPUTE,
                placed_by="agt_1",
                dispute=closed_long_ago,
            )
            assert sweep.sweep().skipped_on_hold == 1

            holds.release(hold=hold, released_by="agt_1", approved_by="agt_2", reason="settled")
            after = sweep.sweep()

        assert after.skipped_on_hold == 0
        assert after.expired == 1

    def test_a_case_inside_its_window_is_untouched(self, tenant_a, make_dispute, as_tenant):
        make_dispute(tenant_a)
        with as_tenant(tenant_a):
            assert sweep.sweep().examined == 0


class TestErasureRequests:
    def _request(self, tenant, customer_ref_hash):
        return ErasureRequest.objects.create(
            tenant=tenant,
            customer_ref_hash=customer_ref_hash,
            requested_at=datetime(2026, 8, 19, tzinfo=UTC),
            requested_via="email",
        )

    def test_a_request_against_held_material_is_refused_with_a_reason(
        self, tenant_a, closed_long_ago, as_tenant
    ):
        with as_tenant(tenant_a):
            holds.place(
                tenant=tenant_a,
                name="Okafor v Acme",
                matter_reference="LIT-2026-4",
                reason="Claim filed",
                scope=LegalHold.Scope.DISPUTE,
                placed_by="agt_1",
                dispute=closed_long_ago,
            )
            request = self._request(tenant_a, closed_long_ago.customer_ref_hash)
            decided = holds.decide_erasure(request=request, decided_by="agt_9")

        assert decided.outcome == ErasureRequest.Outcome.REFUSED_LEGAL_HOLD
        assert decided.was_refused
        assert "LIT-2026-4" in decided.outcome_reason
        assert "legal hold" in decided.outcome_reason

    def test_the_refusal_is_auditable(self, tenant_a, closed_long_ago, as_tenant):
        """Refusing a request silently is its own violation."""
        with as_tenant(tenant_a):
            holds.place(
                tenant=tenant_a,
                name="h",
                matter_reference="LIT-1",
                reason="r",
                scope=LegalHold.Scope.CUSTOMER,
                placed_by="agt_1",
                customer_ref_hash=closed_long_ago.customer_ref_hash,
            )
            request = self._request(tenant_a, closed_long_ago.customer_ref_hash)
            holds.decide_erasure(request=request, decided_by="agt_9")

            record = AuditRecord.objects.get(event_type="erasure.refused_legal_hold")

        assert record.actor_id == "agt_9"
        # The words the requester was given, recorded verbatim: a refusal a
        # supervisor cannot read back is a refusal we cannot defend.
        assert record.payload["reason_given"] == request.outcome_reason
        assert record.payload["blocking_holds"] == ["LIT-1"]

    def test_without_a_hold_the_refusal_cites_retention_instead(
        self, tenant_a, closed_long_ago, as_tenant
    ):
        """§11.7 says the procedure must state plainly what is retained under a
        legal-obligation basis. It does."""
        with as_tenant(tenant_a):
            request = self._request(tenant_a, closed_long_ago.customer_ref_hash)
            decided = holds.decide_erasure(request=request, decided_by="agt_9")

        assert decided.outcome == ErasureRequest.Outcome.REFUSED_RETENTION
        assert "seven years" in decided.outcome_reason
        assert "pseudonymised" in decided.outcome_reason
