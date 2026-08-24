"""Triage, the copilot's grounding, clustering and risk signals.

The adversarial suite lives in `TestGroundingAdversarially`. A drafted reply that
invents a refund date is a commitment made to a customer on the firm's behalf by a
system with no authority to make it — so those cases are individual, and each one
is a sentence somebody could plausibly write.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from disputeshield.intelligence import clustering, copilot, signals, triage
from disputeshield.intelligence.grounding import (
    UngroundedDraft,
    check,
    extract_claims,
)
from disputeshield.models import (
    AuditRecord,
    DisputeMessage,
    RiskSignal,
    RootCauseCluster,
    Suggestion,
)

pytestmark = pytest.mark.django_db

UTC = UTC
NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


class TestGroundingAdversarially:
    """Each case is a sentence somebody could plausibly write."""

    SOURCES = [
        "I was debited 50,000 but the transfer failed.",
        "Your case reference is DS-2026-8AJNKJ.",
        "2026-08-26T17:00:00+00:00",
    ]

    @pytest.mark.parametrize(
        ("draft", "expected_kind"),
        [
            ("We will refund you by Friday.", "commitment"),
            ("You will receive ₦75,000 shortly.", "amount"),
            ("This will be resolved within 3 days.", "date"),
            ("Rest assured this is being handled.", "commitment"),
            ("Your money will be credited tomorrow.", "commitment"),
            ("We guarantee a resolution on 30 August.", "commitment"),
            ("Expect the reversal on Monday.", "date"),
            ("A refund of 120,000 has been approved.", "amount"),
        ],
    )
    def test_an_unsupported_claim_is_caught(self, draft, expected_kind):
        result = check(draft, self.SOURCES)
        assert not result.grounded, f"{draft!r} slipped through"
        assert expected_kind in {claim.kind for claim in result.unsupported}

    def test_an_amount_the_customer_stated_is_supported(self):
        """The customer said 50,000; quoting it back is not an invention."""
        assert check("We can see the 50,000 debit on your account.", self.SOURCES).grounded

    def test_an_amount_matches_across_formatting(self):
        """ "50,000" and "50000" are the same number; a formatting difference must
        not make a true statement unsupported."""
        assert check("The 50000 debit is confirmed.", self.SOURCES).grounded

    def test_a_reference_from_the_case_is_supported(self):
        assert check("Your reference is DS-2026-8AJNKJ.", self.SOURCES).grounded

    def test_a_draft_with_no_dated_priced_or_promised_claim_is_grounded(self):
        """Where the commitment boundary sits, stated deliberately.

        The check targets the three things a customer holds the firm to: *when*,
        *how much*, and *that you will*. A reassurance that commits to nothing
        substantive — "we are looking into this" — is not one of them, and
        blocking it would make the copilot unusable for the reply an agent
        actually needs to send most often.

        "We will keep you updated" *is* caught, because "we will" is a promise
        the customer can quote back, even when what follows is mild.
        """
        assert check("We are looking into this for you.", []).grounded
        assert check("Your case is with an agent.", []).grounded
        assert not check("We will keep you updated.", []).grounded

    def test_the_check_is_case_insensitive(self):
        assert not check("WE WILL REFUND YOU.", self.SOURCES).grounded

    def test_a_related_word_nearby_does_not_support_a_promise(self):
        """ "we will refund you" is not made true by the case containing "refund"."""
        sources = ["The customer is asking about a refund."]
        assert not check("We will refund you.", sources).grounded

    def test_claims_are_deduplicated(self):
        claims = extract_claims("We will refund 50,000. We will refund 50,000 today.")
        amounts = [c for c in claims if c.kind == "amount"]
        assert len(amounts) == 1

    def test_an_amount_is_not_grounded_by_digits_that_span_two_sources(self):
        """The defect this pins let the gate pass an invented refund.

        Comparing a claim's digits against every digit in the joined sources
        produces one long string in which almost any short number can be found.
        Here 9000 falls across a case reference and a deadline's microseconds —
        two facts that have nothing to do with an amount and nothing to do with
        each other. The draft promises ₦9,000 and no source supports it.
        """
        sources = [
            "The transfer failed.",
            "DS-2026-GQG4YR",
            "2026-09-04T14:12:55.900042+00:00",
        ]
        assert "9000" in "".join(c for c in "".join(sources) if c.isdigit())

        unsupported = check("We will refund you ₦9,000 on Friday.", sources).unsupported
        assert "amount" in {claim.kind for claim in unsupported}

    def test_an_amount_that_ends_a_sentence_is_read_whole(self):
        """The most ordinary way an amount is written, and it was truncated.

        The trailing boundary guard rejected a number followed by a full stop, so
        a weaker alternative matched a prefix and "₦9,000." became the claim
        "₦9" — which any text containing a 9 appears to support.
        """
        assert [c.text for c in extract_claims("We refunded ₦9,000.")] == ["₦9,000"]
        assert [c.text for c in extract_claims("Total 9,000.50.")] == ["9,000.50"]

    def test_a_number_that_continues_is_still_rejected(self):
        """What the strict guard was protecting, kept."""
        assert not extract_claims("ref 1.234.567 here")
        assert not extract_claims("total 9,000.505 no")

    def test_the_same_amount_written_differently_still_matches(self):
        """Strictness is affordable because values are compared, not digit strings."""
        assert check("We refunded ₦50000.", ["We refunded 50,000"]).grounded
        assert check("We refunded ₦9,000.", ["amount 9,000.00 was paid"]).grounded


class TestCopilot:
    def test_a_grounded_draft_is_accepted_and_recorded(self, tenant_a, make_dispute, as_tenant):
        dispute = make_dispute(
            tenant_a, description="I was debited 50,000 but the transfer failed."
        )
        with as_tenant(tenant_a):
            draft = copilot.draft_reply(
                dispute=dispute,
                body="We can see the 50,000 debit and are investigating.",
                actor_id="agt_1",
            )
            record = AuditRecord.objects.get(event_type="suggestion.proposed")

        assert draft.suggestion.kind == Suggestion.Kind.REPLY_DRAFT
        assert record.payload["model_id"] == copilot.MODEL_ID
        assert record.payload["model_version"] == copilot.MODEL_VERSION
        # The body is on the suggestion, not duplicated into the audit payload.
        assert "50,000" not in str(record.payload)

    def test_an_ungrounded_draft_is_blocked_not_flagged(self, tenant_a, make_dispute, as_tenant):
        """A warning next to a draft is one an agent under queue pressure clicks
        past; a block is a thing they have to resolve."""
        dispute = make_dispute(tenant_a, description="The transfer failed.")
        with as_tenant(tenant_a), pytest.raises(UngroundedDraft, match="cannot be inserted"):
            copilot.draft_reply(
                dispute=dispute, body="We will refund you ₦50,000 on Friday.", actor_id="agt_1"
            )

    def test_a_blocked_draft_creates_no_suggestion(self, tenant_a, make_dispute, as_tenant):
        dispute = make_dispute(tenant_a, description="The transfer failed.")
        with as_tenant(tenant_a):
            with pytest.raises(UngroundedDraft):
                copilot.draft_reply(
                    dispute=dispute, body="We will refund you tomorrow.", actor_id="agt_1"
                )
            assert Suggestion.objects.count() == 0

    def test_retrieval_never_reaches_an_internal_note(self, tenant_a, make_dispute, as_tenant):
        """§10: an internal note is not something the customer may be told back —
        and grounding a draft in one would put it in front of them."""
        from disputeshield.disputes import service

        dispute = make_dispute(tenant_a, description="The transfer failed.")
        with as_tenant(tenant_a):
            service.add_message(
                dispute=dispute,
                body="Internal: customer looks like a repeat claimant, offer nothing",
                author_type=DisputeMessage.AuthorType.AGENT,
                visibility=DisputeMessage.Visibility.INTERNAL,
                author_id="agt_1",
            )
            sources = copilot.retrieve_sources(dispute)

        assert not any("repeat claimant" in source for source in sources)

    def test_the_expected_resolution_date_is_a_supported_source(
        self, tenant_a, make_dispute, as_tenant
    ):
        """The customer was already told this date at filing, so quoting it is
        not an invention."""
        dispute = make_dispute(tenant_a, description="The transfer failed.")
        with as_tenant(tenant_a):
            weekday = dispute.resolution_deadline.strftime("%A")
            draft = copilot.draft_reply(
                dispute=dispute,
                body=f"We expect to come back to you by {weekday}.",
                actor_id="agt_1",
            )
        assert draft.body

    def test_a_preview_reports_what_would_block(self, tenant_a, make_dispute, as_tenant):
        dispute = make_dispute(tenant_a, description="The transfer failed.")
        with as_tenant(tenant_a):
            blocked = copilot.would_be_blocked(
                dispute=dispute, body="We will refund you ₦9,000 on Friday."
            )
        assert any("commitment" in item for item in blocked)
        assert any("amount" in item for item in blocked)


class TestTriage:
    def test_a_category_is_proposed_with_its_reasoning(self, tenant_a, make_dispute, as_tenant):
        dispute = make_dispute(
            tenant_a, description="My airtime recharge failed but I was debited."
        )
        with as_tenant(tenant_a):
            suggestions = triage.propose(dispute=dispute)
            category = next(s for s in suggestions if s.kind == Suggestion.Kind.CATEGORY)

        assert category.value == "failed_airtime"
        assert "airtime" in category.rationale
        assert category.model_id == triage.MODEL_ID

    def test_the_suggestion_never_touches_the_case(self, tenant_a, make_dispute, as_tenant):
        """The gate, observed rather than only asserted structurally."""
        dispute = make_dispute(
            tenant_a, description="My airtime recharge failed.", category="other"
        )
        with as_tenant(tenant_a):
            triage.propose(dispute=dispute)
            dispute.refresh_from_db()
        assert dispute.category == "other"
        assert dispute.priority == "normal"

    def test_accepting_and_overriding_are_both_recorded(self, tenant_a, make_dispute, as_tenant):
        dispute = make_dispute(tenant_a, description="My card was charged twice at a POS.")
        with as_tenant(tenant_a):
            (suggestion, *_rest) = triage.propose(dispute=dispute)
            triage.decide(suggestion=suggestion, chosen_value=suggestion.value, actor_id="agt_1")
            assert suggestion.disposition == Suggestion.Disposition.ACCEPTED
            assert suggestion.was_correct is True
            assert AuditRecord.objects.filter(event_type="suggestion.accepted").exists()

    def test_an_override_is_the_training_signal(self, tenant_a, make_dispute, as_tenant):
        dispute = make_dispute(tenant_a, description="My card was charged twice at a POS.")
        with as_tenant(tenant_a):
            (suggestion, *_rest) = triage.propose(dispute=dispute)
            triage.decide(
                suggestion=suggestion, chosen_value="unauthorised_debit", actor_id="agt_1"
            )
            record = AuditRecord.objects.get(event_type="suggestion.overridden")

        assert suggestion.was_correct is False
        assert record.payload["suggested"] == suggestion.value
        assert record.payload["chosen"] == "unauthorised_debit"
        assert record.payload["model_version"] == triage.MODEL_VERSION

    def test_accuracy_is_exported_and_absent_before_any_decision(
        self, tenant_a, make_dispute, as_tenant
    ):
        """An untested model is not a perfect one."""
        with as_tenant(tenant_a):
            assert triage.accuracy()["accuracy"] is None

            dispute = make_dispute(tenant_a, description="My airtime recharge failed.")
            (suggestion, *_rest) = triage.propose(dispute=dispute)
            triage.decide(suggestion=suggestion, chosen_value=suggestion.value, actor_id="agt_1")
            measured = triage.accuracy()

        assert measured["decided"] == 1
        assert measured["accuracy"] == 1.0

    def test_a_description_with_no_signal_proposes_nothing(self, tenant_a, make_dispute, as_tenant):
        """Silence beats a confident guess: a wrong category starts the wrong
        regulatory clock."""
        dispute = make_dispute(tenant_a, description="Hello, please help me.")
        with as_tenant(tenant_a):
            assert triage.propose(dispute=dispute) == ()


class TestClustering:
    @pytest.fixture
    def cases(self, tenant_a, make_dispute, make_policy, as_tenant):
        version = make_policy(tenant_a)
        built = []
        for n in range(4):
            built.append(
                make_dispute(
                    tenant_a,
                    policy_version=version,
                    customer_ref=f"usr_{n}",
                    description="The GTBank reversal never arrived after the timeout.",
                    transaction_ref=f"NIPX-{n:04d}",
                )
            )
        built.append(
            make_dispute(
                tenant_a,
                policy_version=version,
                customer_ref="usr_other",
                description="My card was declined at a shop.",
                transaction_ref="CARD-0001",
            )
        )
        return built

    def test_a_cluster_carries_its_membership_and_evidence(self, tenant_a, cases, as_tenant):
        """A hypothesis presented with the confidence of a fact gets acted on
        wrongly, so a cluster shows exactly what it is claiming."""
        with as_tenant(tenant_a):
            clusters = clustering.compute(lookback_days=365)

        assert clusters
        prefix = next(c for c in clusters if c.basis == "transaction_prefix")
        assert prefix.case_count == 4
        assert prefix.evidence["prefix"] == "NIPX"
        assert len(prefix.evidence["references"]) == 4

    def test_clustering_modifies_no_case(self, tenant_a, cases, as_tenant):
        with as_tenant(tenant_a):
            before = [(c.pk, c.category, c.status, c.priority) for c in cases]
            clustering.compute(lookback_days=365)
            for case in cases:
                case.refresh_from_db()
            after = [(c.pk, c.category, c.status, c.priority) for c in cases]
        assert before == after

    def test_a_snapshot_can_be_persisted_without_touching_a_case(self, tenant_a, cases, as_tenant):
        with as_tenant(tenant_a):
            clusters = clustering.compute(lookback_days=365)
            clustering.persist(clusters, tenant=tenant_a)
            stored = RootCauseCluster.objects.all()
            assert stored.count() == len(clusters)
            assert all(row.model_version == clustering.MODEL_VERSION for row in stored)

    def test_a_term_in_almost_every_case_is_not_a_cause(
        self, tenant_a, make_dispute, make_policy, as_tenant
    ):
        """It is describing the product, not a cause."""
        version = make_policy(tenant_a)
        for n in range(5):
            make_dispute(
                tenant_a,
                policy_version=version,
                customer_ref=f"usr_g{n}",
                description="complaint about something",
            )
        with as_tenant(tenant_a):
            labels = [c.label for c in clustering.compute(lookback_days=365)]
        assert not any("complaint" in label for label in labels)


class TestRiskSignals:
    def test_a_repeat_claimant_is_surfaced_with_its_evidence(
        self, tenant_a, make_dispute, make_policy, as_tenant
    ):
        version = make_policy(tenant_a)
        cases = [
            make_dispute(tenant_a, policy_version=version, customer_ref="usr_repeat")
            for _ in range(6)
        ]
        with as_tenant(tenant_a):
            findings = signals.evaluate(dispute=cases[-1])
            repeat = next(f for f in findings if f.kind == RiskSignal.Kind.REPEAT_CLAIMANT)

        assert repeat.evidence["count"] == 5
        # So an agent can check the claim rather than take it.
        assert repeat.evidence["references"]

    def test_an_ordinary_customer_raises_no_signal(
        self, tenant_a, make_dispute, make_policy, as_tenant
    ):
        """A signal that fires often is one an agent learns to ignore."""
        version = make_policy(tenant_a)
        dispute = make_dispute(tenant_a, policy_version=version, customer_ref="usr_normal")
        with as_tenant(tenant_a):
            assert signals.evaluate(dispute=dispute) == ()

    def test_recording_a_signal_changes_nothing_about_the_case(
        self, tenant_a, make_dispute, make_policy, as_tenant
    ):
        """The gate, observed: no priority, no status, no outcome, no clock."""
        version = make_policy(tenant_a)
        cases = [
            make_dispute(tenant_a, policy_version=version, customer_ref="usr_repeat")
            for _ in range(6)
        ]
        target = cases[-1]

        with as_tenant(tenant_a):
            before = (
                target.priority,
                target.status,
                target.outcome,
                target.resolution_deadline,
                target.clock.state,
            )
            signals.record(dispute=target, findings=signals.evaluate(dispute=target))
            target.refresh_from_db()
            target.clock.refresh_from_db()
            after = (
                target.priority,
                target.status,
                target.outcome,
                target.resolution_deadline,
                target.clock.state,
            )
            assert RiskSignal.objects.filter(dispute=target).exists()

        assert before == after

    def test_recording_the_same_signal_twice_is_idempotent(
        self, tenant_a, make_dispute, make_policy, as_tenant
    ):
        version = make_policy(tenant_a)
        cases = [
            make_dispute(tenant_a, policy_version=version, customer_ref="usr_repeat")
            for _ in range(6)
        ]
        with as_tenant(tenant_a):
            findings = signals.evaluate(dispute=cases[-1])
            signals.record(dispute=cases[-1], findings=findings)
            signals.record(dispute=cases[-1], findings=findings)
            assert RiskSignal.objects.filter(dispute=cases[-1]).count() == len(findings)

    def test_signals_do_not_cross_a_customer(self, tenant_a, make_dispute, make_policy, as_tenant):
        version = make_policy(tenant_a)
        for _ in range(6):
            make_dispute(tenant_a, policy_version=version, customer_ref="usr_repeat")
        other = make_dispute(tenant_a, policy_version=version, customer_ref="usr_innocent")
        with as_tenant(tenant_a):
            assert signals.evaluate(dispute=other) == ()
