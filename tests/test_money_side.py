"""The scheme clock, provider connectors and the exposure view (phase 9).

The headline gate: **the scheme clock and the regulatory clock breach
independently**, asserted with a case where one is comfortable and the other is
not. They run concurrently, under different rules, and a product that treats them
as one clock loses money on the one it is not watching.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from disputeshield.connectors import registry
from disputeshield.connectors.base import ProviderTransaction
from disputeshield.connectors.crypto import (
    CredentialUnreadable,
    decrypt_credential,
    encrypt_credential,
)
from disputeshield.disputes import representment as representment_service
from disputeshield.disputes import service
from disputeshield.finance import exposure
from disputeshield.models import (
    AuditRecord,
    ProviderCall,
    ProviderConnector,
    ReasonCode,
    Representment,
    SettlementConfirmation,
    SLADeadline,
)
from disputeshield.models.dispute import Outcome, Status
from disputeshield.sla import clock as clock_service
from disputeshield.sla import sweeper

pytestmark = pytest.mark.django_db

UTC = UTC
CHARGEBACK_AT = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)


@pytest.fixture
def reason_code(tenant_a, as_tenant):
    with as_tenant(tenant_a):
        return ReasonCode.objects.create(
            tenant=tenant_a,
            scheme=ReasonCode.Scheme.VISA,
            code="13.1",
            title="Merchandise or services not received",
            response_window_days=30,
            evidence_requirements=[
                {"key": "delivery_proof", "label": "Proof of delivery", "required": True},
                {"key": "auth_record", "label": "Authorisation record", "required": True},
                {"key": "customer_comms", "label": "Customer correspondence", "required": False},
            ],
        )


@pytest.fixture
def card_case(tenant_a, make_dispute, make_policy, as_tenant):
    """Filed at the chargeback instant, so both clocks start from the same point.

    Filing at "now" while the scheme clock starts in the past would put the pause
    below entirely before the regulatory clock began, where it subtracts nothing —
    and the test would pass for the wrong reason.
    """
    version = make_policy(tenant_a, category="card_chargeback", resolution_hours=8)
    return make_dispute(
        tenant_a,
        policy_version=version,
        category="card_chargeback",
        customer_ref="usr_card",
        submitted_at=CHARGEBACK_AT,
    )


class TestTheTwoClocks:
    def test_the_scheme_deadline_is_not_moved_by_a_pause(
        self, tenant_a, card_case, reason_code, as_tenant
    ):
        """A card scheme does not care that the firm is waiting on the customer."""
        with as_tenant(tenant_a):
            representment_service.open_representment(
                dispute=card_case,
                reason_code=reason_code,
                chargeback_reference="CB-1",
                chargeback_at=CHARGEBACK_AT,
                actor_id="agt_1",
            )
            before = card_case.clock.deadlines.get(
                kind=SLADeadline.Kind.SCHEME_REPRESENTMENT
            ).fires_at
            regulatory_before = card_case.clock.deadlines.get(
                kind=SLADeadline.Kind.RESOLUTION
            ).fires_at

            clock_service.pause(
                clock=card_case.clock,
                reason="awaiting the customer's receipt",
                actor_type="user",
                actor_id="agt_1",
                at=CHARGEBACK_AT + timedelta(hours=1),
            )
            clock_service.resume(
                clock=card_case.clock,
                reason="received",
                actor_type="user",
                actor_id="agt_1",
                at=CHARGEBACK_AT + timedelta(hours=5),
            )

            after = card_case.clock.deadlines.get(
                kind=SLADeadline.Kind.SCHEME_REPRESENTMENT
            ).fires_at
            regulatory_after = card_case.clock.deadlines.get(
                kind=SLADeadline.Kind.RESOLUTION
            ).fires_at

        assert after == before, "the pause moved the scheme's deadline"
        assert regulatory_after > regulatory_before, "the pause did not move ours"

    def test_they_breach_independently(self, tenant_a, card_case, reason_code, as_tenant):
        """The gate: one comfortable, one not, at the same instant."""
        with as_tenant(tenant_a):
            representment_service.open_representment(
                dispute=card_case,
                reason_code=reason_code,
                chargeback_reference="CB-1",
                chargeback_at=CHARGEBACK_AT,
                actor_id="agt_1",
            )

        # Two days later: the regulatory window (8 business hours from Wednesday
        # morning) has gone; the scheme's 30-day window is nowhere near.
        result = sweeper.sweep(now=CHARGEBACK_AT + timedelta(days=2))
        assert result.fired > 0

        with as_tenant(tenant_a):
            regulatory = card_case.clock.deadlines.get(kind=SLADeadline.Kind.RESOLUTION)
            scheme = card_case.clock.deadlines.get(kind=SLADeadline.Kind.SCHEME_REPRESENTMENT)

        assert regulatory.fired_at is not None, "the regulatory window did not breach"
        assert scheme.fired_at is None, "the scheme window breached with 29 days to run"

    def test_they_alert_independently(self, tenant_a, card_case, reason_code, as_tenant):
        """Different event types, so §11.4 can page on one without the other."""
        from disputeshield.models import NotificationOutbox

        with as_tenant(tenant_a):
            representment_service.open_representment(
                dispute=card_case,
                reason_code=reason_code,
                chargeback_reference="CB-1",
                chargeback_at=CHARGEBACK_AT,
                actor_id="agt_1",
            )

        sweeper.sweep(now=CHARGEBACK_AT + timedelta(days=40))

        with as_tenant(tenant_a):
            events = set(NotificationOutbox.objects.values_list("event_type", flat=True))
        assert "sla.resolution" in events
        assert "sla.scheme_representment" in events

    def test_the_scheme_window_is_wall_clock(self, tenant_a, card_case, reason_code, as_tenant):
        """A scheme observes neither business hours nor holidays."""
        with as_tenant(tenant_a):
            representment = representment_service.open_representment(
                dispute=card_case,
                reason_code=reason_code,
                chargeback_reference="CB-1",
                chargeback_at=CHARGEBACK_AT,
                actor_id="agt_1",
            )
        assert representment.respond_by == CHARGEBACK_AT + timedelta(days=30)


class TestRepresentmentPacks:
    def test_an_incomplete_pack_is_refused(self, tenant_a, card_case, reason_code, as_tenant):
        """The expensive failure is an acquirer rejecting it after the window has
        closed. Better to refuse while there is still time to gather it."""
        with as_tenant(tenant_a):
            representment = representment_service.open_representment(
                dispute=card_case,
                reason_code=reason_code,
                chargeback_reference="CB-1",
                chargeback_at=CHARGEBACK_AT,
                actor_id="agt_1",
            )
            with pytest.raises(representment_service.EvidenceIncomplete, match="delivery_proof"):
                representment_service.build_pack(representment=representment, actor_id="agt_1")

    def test_a_complete_pack_exports_and_is_reproducible(
        self, tenant_a, card_case, reason_code, as_tenant
    ):
        with as_tenant(tenant_a):
            representment = representment_service.open_representment(
                dispute=card_case,
                reason_code=reason_code,
                chargeback_reference="CB-1",
                chargeback_at=CHARGEBACK_AT,
                actor_id="agt_1",
            )
            for key in ("delivery_proof", "auth_record"):
                representment_service.attach_evidence(
                    representment=representment,
                    key=key,
                    value=f"{key}-evidence",
                    actor_id="agt_1",
                )
            assert representment.status == Representment.Status.READY

            first = representment_service.build_pack(representment=representment, actor_id="agt_1")
            second = representment_service.build_pack(representment=representment, actor_id="agt_1")

        assert first.as_json() == second.as_json()
        assert first.content_digest == second.content_digest

    def test_evidence_outside_the_schemes_checklist_is_refused(
        self, tenant_a, card_case, reason_code, as_tenant
    ):
        """The scheme decides what a representment contains, not us."""
        with as_tenant(tenant_a):
            representment = representment_service.open_representment(
                dispute=card_case,
                reason_code=reason_code,
                chargeback_reference="CB-1",
                chargeback_at=CHARGEBACK_AT,
                actor_id="agt_1",
            )
            with pytest.raises(ValueError, match="not part of"):
                representment_service.attach_evidence(
                    representment=representment, key="a_hunch", value="x", actor_id="agt_1"
                )

    def test_the_record_states_that_we_did_not_submit(
        self, tenant_a, card_case, reason_code, as_tenant
    ):
        """Submission is the acquirer's channel and the fintech's decision."""
        with as_tenant(tenant_a):
            representment = representment_service.open_representment(
                dispute=card_case,
                reason_code=reason_code,
                chargeback_reference="CB-1",
                chargeback_at=CHARGEBACK_AT,
                actor_id="agt_1",
            )
            for key in ("delivery_proof", "auth_record"):
                representment_service.attach_evidence(
                    representment=representment, key=key, value="x", actor_id="agt_1"
                )
            representment_service.build_pack(representment=representment, actor_id="agt_1")

            representment_service.record_submission(
                representment=representment,
                submitted_at=CHARGEBACK_AT + timedelta(days=2),
                actor_id="agt_1",
            )
            exported = AuditRecord.objects.get(event_type="representment.exported")
            recorded = AuditRecord.objects.get(
                event_type="representment.submission_recorded_by_fintech"
            )

        assert exported.payload["submitted_by_disputeshield"] is False
        assert recorded.payload["submitted_by_disputeshield"] is False


class TestConnectors:
    @pytest.fixture
    def connector(self, tenant_a, as_tenant):
        with as_tenant(tenant_a):
            return ProviderConnector.objects.create(
                tenant=tenant_a,
                provider=ProviderConnector.Provider.PAYSTACK,
                base_url="https://api.paystack.test",
                credential_ciphertext=encrypt_credential(tenant_a, "sk_test_secret"),
                credential_key_ref="local://dev",
                created_by="agt_1",
            )

    def test_a_credential_round_trips_within_its_tenant(self, tenant_a, connector, as_tenant):
        with as_tenant(tenant_a):
            assert decrypt_credential(connector) == "sk_test_secret"

    def test_a_credential_cannot_be_read_with_another_tenants_key(
        self, tenant_a, tenant_b, connector, as_tenant
    ):
        """The property that makes a per-tenant key worth having."""
        with as_tenant(tenant_a):
            connector.tenant_id = tenant_b.pk
            with pytest.raises(CredentialUnreadable):
                decrypt_credential(connector)

    def test_the_credential_is_never_in_the_repr(self, tenant_a, connector, as_tenant):
        with as_tenant(tenant_a):
            client = registry.build(connector)
        assert "sk_test_secret" not in repr(client)

    def test_context_is_fetched_and_the_call_is_audited(
        self, tenant_a, card_case, connector, as_tenant, monkeypatch
    ):
        """A customer's security team asking "what did you ask our provider about
        me?" gets an answer from the record."""
        monkeypatch.setattr(
            registry.StubConnector,
            "fixtures",
            {"TXN-1": ProviderTransaction(reference="TXN-1", status="failed", amount_minor=500)},
        )
        with as_tenant(tenant_a):
            result = registry.fetch_context(dispute=card_case, reference="TXN-1")

            assert result.available is True
            assert result.transaction.status == "failed"
            assert result.timeline

            call = ProviderCall.objects.get()
            assert call.method == "GET"
            assert call.ok is True
            record = AuditRecord.objects.get(event_type="provider.called")

        assert record.payload["provider"] == "paystack"
        # The request that was made, never the credential that made it.
        assert "sk_test_secret" not in str(record.payload)

    def test_a_provider_outage_degrades_the_case_and_never_blocks_it(
        self, tenant_a, card_case, connector, as_tenant, monkeypatch, make_dispute, make_policy
    ):
        """§8.6 principle 1: a case must be filable whether or not a third party
        is reachable."""
        monkeypatch.setattr(registry.StubConnector, "unavailable", True)

        with as_tenant(tenant_a):
            result = registry.fetch_context(dispute=card_case, reference="TXN-1")
            assert result.available is False
            assert result.reason

            failure = AuditRecord.objects.get(event_type="provider.call_failed")
            assert failure.payload["ok"] is False

        # Filing still works during the outage.
        version = make_policy(tenant_a, category="card_chargeback")
        filed = make_dispute(
            tenant_a,
            policy_version=version,
            category="card_chargeback",
            customer_ref="usr_during_outage",
        )
        assert filed.reference.startswith("DS-")

    def test_a_case_with_no_transaction_reference_says_so(
        self, tenant_a, make_dispute, connector, as_tenant
    ):
        dispute = make_dispute(tenant_a, customer_ref="usr_no_txn")
        with as_tenant(tenant_a):
            result = registry.fetch_context(dispute=dispute)
        assert result.available is False
        assert "no transaction reference" in result.reason


class TestExposure:
    @pytest.fixture
    def resolved_with_refund(self, tenant_a, make_dispute, make_policy, as_tenant):
        version = make_policy(tenant_a)
        cases = [
            make_dispute(tenant_a, policy_version=version, customer_ref=f"usr_{n}")
            for n in range(4)
        ]
        with as_tenant(tenant_a):
            for case in cases[:2]:
                for step in (Status.ACKNOWLEDGED, Status.INVESTIGATING):
                    service.transition(
                        dispute=case, to=step, actor_type="user", actor_id="agt_1", reason="x"
                    )
                case.amount_minor = 5_000_000
                case.save(update_fields=["amount_minor"])
                service.resolve(
                    dispute=case,
                    outcome=Outcome.UPHELD,
                    notes="Reversal confirmed.",
                    refund_amount_minor=5_000_000,
                    actor_type="user",
                    actor_id="agt_1",
                )
            for case in cases[2:]:
                case.amount_minor = 1_000_000
                case.save(update_fields=["amount_minor"])
        return cases

    def test_value_under_dispute_counts_only_open_cases(
        self, tenant_a, resolved_with_refund, as_tenant
    ):
        with as_tenant(tenant_a):
            rows = exposure.under_dispute()
        assert rows[0]["cases"] == 2
        assert rows[0]["amount_minor"] == 2_000_000

    def test_amounts_stay_in_integer_minor_units(self, tenant_a, resolved_with_refund, as_tenant):
        """A finance view reporting rounded major units is one somebody
        reconciles against a ledger and finds off by cents."""
        with as_tenant(tenant_a):
            rows = exposure.under_dispute()
        assert isinstance(rows[0]["amount_minor"], int)

    def test_the_uphold_rate_is_measured_not_assumed(
        self, tenant_a, resolved_with_refund, as_tenant
    ):
        with as_tenant(tenant_a):
            projection = exposure.expected_loss()
        assert projection["uphold_rate"] == 1.0
        assert projection["sample_size"] == 2
        assert projection["expected_minor"] == 2_000_000

    def test_with_no_history_the_projection_is_absent_rather_than_guessed(
        self, tenant_a, make_dispute, as_tenant
    ):
        """A made-up number in a provisioning view is worse than an absent one."""
        make_dispute(tenant_a, customer_ref="usr_new")
        with as_tenant(tenant_a):
            projection = exposure.expected_loss()
        assert projection["uphold_rate"] is None
        assert projection["expected_minor"] is None

    def test_the_unreconciled_delta_is_reported_rather_than_hidden(
        self, tenant_a, resolved_with_refund, as_tenant
    ):
        """DisputeShield knows what was promised; only the ledger knows what was
        paid. The gap is the interesting number."""
        period = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))

        with as_tenant(tenant_a):
            before = exposure.reconcile(period_from=period[0], period_to=period[1])
            assert before.promised_minor == 10_000_000
            assert before.settled_minor == 0
            assert before.delta_minor == 10_000_000
            assert before.unreconciled_cases == 2

            SettlementConfirmation.objects.create(
                tenant=tenant_a,
                dispute=resolved_with_refund[0],
                reference="LEDGER-1",
                amount_minor=5_000_000,
                settled_at=resolved_with_refund[0].resolved_at,
            )
            after = exposure.reconcile(period_from=period[0], period_to=period[1])

        assert after.settled_minor == 5_000_000
        assert after.delta_minor == 5_000_000
        assert after.unreconciled_cases == 1

    def test_a_negative_delta_is_a_finding_not_an_error(
        self, tenant_a, resolved_with_refund, as_tenant
    ):
        """More paid than promised is its own problem, and netting it away would
        hide it."""
        period = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
        with as_tenant(tenant_a):
            SettlementConfirmation.objects.create(
                tenant=tenant_a,
                dispute=resolved_with_refund[0],
                reference="LEDGER-1",
                amount_minor=12_000_000,
                settled_at=resolved_with_refund[0].resolved_at,
            )
            result = exposure.reconcile(period_from=period[0], period_to=period[1])
        assert result.delta_minor == -2_000_000

    def test_an_unknown_grouping_is_refused(self, tenant_a, as_tenant):
        with as_tenant(tenant_a), pytest.raises(ValueError, match="category"):
            exposure.under_dispute(group_by="customer")

    def test_exposure_never_crosses_a_tenant(
        self, tenant_a, tenant_b, resolved_with_refund, as_tenant
    ):
        with as_tenant(tenant_b):
            assert exposure.under_dispute() == []
