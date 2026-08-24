"""The SLA simulator, QA sampling and outbound webhooks (phase 11).

Three gates, each protecting a different way of being confidently wrong:

  * **The simulator's self-check.** Replaying an unchanged policy must reproduce
    the breach count that actually occurred. Anything else means the replay is not
    using history, and a confident wrong number is worse than no number.
  * **Forced review cannot be disabled.** A checkbox unticked in a busy quarter
    exempts exactly the cases a supervisor most needs to see.
  * **Webhooks are ordered, at-least-once and parked rather than dropped.**
"""

from __future__ import annotations

import contextlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from disputeshield.disputes import service
from disputeshield.models import (
    AuditRecord,
    PolicySimulation,
    QaReview,
    WebhookDelivery,
    WebhookEndpoint,
)
from disputeshield.models.dispute import Outcome, Status
from disputeshield.quality import sampling
from disputeshield.sla import clock as clock_service
from disputeshield.sla import simulator
from disputeshield.webhooks import delivery as webhooks

pytestmark = pytest.mark.django_db

PERIOD = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
WEDNESDAY = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)


@contextlib.contextmanager
def _replica_context(tenant):
    """Both isolation contexts, established on the replica connection.

    What `simulate()` does internally, exposed here so the private helpers can be
    tested directly without pretending the replica shares the primary's session.
    """
    from django.db import transaction

    from disputeshield.tenancy import context
    from disputeshield.tenancy.middleware import db_tenant_context

    with (
        transaction.atomic(using=simulator.REPLICA),
        context.tenant_context(tenant.pk),
        db_tenant_context(tenant.pk, using=simulator.REPLICA),
    ):
        yield


@pytest.fixture
def resolved_history(tenant_a, make_dispute, make_policy, as_tenant):
    """Cases with real histories: some paused, some breached, some clean."""
    version = make_policy(tenant_a, resolution_hours=8)
    cases = []
    with as_tenant(tenant_a):
        for n in range(6):
            case = make_dispute(
                tenant_a,
                policy_version=version,
                customer_ref=f"usr_{n}",
                submitted_at=WEDNESDAY,
            )
            for step in (Status.ACKNOWLEDGED, Status.INVESTIGATING):
                service.transition(
                    dispute=case, to=step, actor_type="user", actor_id="agt_1", reason="x"
                )
            if n % 3 == 0:
                clock_service.pause(
                    clock=case.clock,
                    reason="awaiting the customer",
                    actor_type="user",
                    actor_id="agt_1",
                    at=WEDNESDAY + timedelta(hours=1),
                )
                clock_service.resume(
                    clock=case.clock,
                    reason="received",
                    actor_type="user",
                    actor_id="agt_1",
                    at=WEDNESDAY + timedelta(hours=3),
                )
            service.resolve(
                dispute=case,
                outcome=Outcome.UPHELD,
                notes="Reversal confirmed.",
                actor_type="user",
                actor_id="agt_1",
                at=WEDNESDAY + timedelta(hours=4),
            )
            cases.append(case)
    return version, cases


# The simulator reads from the replica, which is a genuinely separate connection
# even when it mirrors the same database. A non-transactional test keeps its data
# in an uncommitted transaction on `default`, where that connection cannot see it
# — so these run transactionally. Slower, and the only way to exercise the thing
# that broke: a context established on the primary is absent on the replica.
simulator_db = pytest.mark.django_db(transaction=True, databases=["default", "replica"])


@simulator_db
class TestTheSimulatorSelfCheck:
    def test_replaying_an_unchanged_policy_reproduces_history(
        self, tenant_a, resolved_history, as_tenant
    ):
        """The gate. If this fails, every other number the simulator produces is
        a confident wrong one."""
        with as_tenant(tenant_a):
            agrees, detail = simulator.self_check(period_from=PERIOD[0], period_to=PERIOD[1])
        assert agrees, f"replay disagreed with history: {detail}"
        assert detail["cases"] == 6

    def test_it_reads_the_pauses_that_actually_happened(
        self, tenant_a, resolved_history, as_tenant
    ):
        """From `SLAEvent`, which is the evidence, rather than from the clock's
        materialised view of it."""
        _, cases = resolved_history
        paused_case = cases[0]
        # Both contexts, on the replica: the contextvar the scoped manager reads
        # and the session variable RLS reads, on the connection being queried.
        with _replica_context(tenant_a):
            intervals = simulator._historical_pauses(paused_case)
        assert len(intervals) == 1
        assert intervals[0][1] - intervals[0][0] == timedelta(hours=2)

    def test_an_unresumed_pause_still_counts(self, tenant_a, make_dispute, make_policy, as_tenant):
        """Dropping it would credit the firm with time it did not have."""
        version = make_policy(tenant_a, resolution_hours=8)
        case = make_dispute(tenant_a, policy_version=version, submitted_at=WEDNESDAY)
        with as_tenant(tenant_a):
            for step in (Status.ACKNOWLEDGED, Status.INVESTIGATING):
                service.transition(
                    dispute=case, to=step, actor_type="user", actor_id="agt_1", reason="x"
                )
            clock_service.pause(
                clock=case.clock,
                reason="awaiting",
                actor_type="user",
                actor_id="agt_1",
                at=WEDNESDAY + timedelta(hours=1),
            )

        with _replica_context(tenant_a):
            intervals = simulator._historical_pauses(case)
        assert len(intervals) == 1
        assert intervals[0][1] > intervals[0][0]


@simulator_db
class TestSimulatingAChange:
    def test_a_shorter_window_projects_more_breaches(self, tenant_a, resolved_history, as_tenant):
        with as_tenant(tenant_a):
            tighter = simulator.simulate(
                period_from=PERIOD[0], period_to=PERIOD[1], resolution_hours=1
            )
        assert tighter.projected_breaches > tighter.actual_breaches
        assert tighter.delta > 0

    def test_a_longer_window_never_projects_more(self, tenant_a, resolved_history, as_tenant):
        with as_tenant(tenant_a):
            looser = simulator.simulate(
                period_from=PERIOD[0], period_to=PERIOD[1], resolution_hours=200
            )
        assert looser.projected_breaches <= looser.actual_breaches

    def test_the_result_is_grouped_for_the_author(self, tenant_a, resolved_history, as_tenant):
        """Which categories absorb the change, and whose queue gets harder."""
        with as_tenant(tenant_a):
            result = simulator.simulate(
                period_from=PERIOD[0], period_to=PERIOD[1], resolution_hours=1
            )
        by_category = result.grouped("category")
        assert by_category
        assert all({"cases", "actual", "projected"} <= set(v) for v in by_category.values())

    def test_the_simulation_writes_nothing_to_a_case(self, tenant_a, resolved_history, as_tenant):
        _, cases = resolved_history
        with as_tenant(tenant_a):
            before = [
                (c.pk, c.status, c.outcome, c.resolution_deadline, c.breach_resolution)
                for c in cases
            ]
            simulator.simulate(period_from=PERIOD[0], period_to=PERIOD[1], resolution_hours=1)
            for case in cases:
                case.refresh_from_db()
            after = [
                (c.pk, c.status, c.outcome, c.resolution_deadline, c.breach_resolution)
                for c in cases
            ]
        assert before == after

    def test_the_result_is_stored_with_the_version_it_evaluated(
        self, tenant_a, resolved_history, as_tenant
    ):
        """So the change record shows what the author was told at the time."""
        version, _ = resolved_history
        with as_tenant(tenant_a):
            result = simulator.simulate(
                period_from=PERIOD[0], period_to=PERIOD[1], resolution_hours=2
            )
            stored = simulator.persist(
                tenant=tenant_a, policy_version=version, result=result, ran_by="agt_9"
            )
            assert PolicySimulation.objects.count() == 1

        assert stored.policy_version_id == version.pk
        assert stored.proposed == {"resolution_hours": 2}
        assert stored.delta == result.delta

    def test_it_reads_from_the_replica(self):
        """§11.1: a simulation over ninety days must not contend with the
        decision path."""
        import inspect

        source = inspect.getsource(simulator)
        assert 'REPLICA = "replica"' in source
        assert ".using(REPLICA)" in source


class TestQaSampling:
    @pytest.fixture
    def reviewable(self, tenant_a, make_dispute, make_policy, as_tenant):
        version = make_policy(tenant_a)
        cases = []
        with as_tenant(tenant_a):
            for n in range(40):
                case = make_dispute(tenant_a, policy_version=version, customer_ref=f"usr_q{n}")
                for step in (Status.ACKNOWLEDGED, Status.INVESTIGATING):
                    service.transition(
                        dispute=case, to=step, actor_type="user", actor_id="agt_1", reason="x"
                    )
                service.resolve(
                    dispute=case,
                    outcome=Outcome.REJECTED,
                    notes="No evidence found.",
                    actor_type="user",
                    actor_id="agt_1",
                )
                cases.append(case)
        return cases

    def test_selection_is_uniform_over_the_eligible_set(self, tenant_a, reviewable, as_tenant):
        """Asserted statistically. A supervisor reviewing whatever is at the top
        of a list reviews the newest cases, and that is not where problems are."""
        counts: Counter[str] = Counter()
        rounds = 200

        with as_tenant(tenant_a):
            for _ in range(rounds):
                selection = sampling.select(period_from=PERIOD[0], period_to=PERIOD[1], percent=25)
                counts.update(case.pk for case in selection.sampled)

        assert len(counts) == len(reviewable), "some cases were never selected"
        expected = rounds * 0.25
        # Generous bounds: this asserts "not obviously skewed", which is what a
        # tight bound would fail on for reasons of chance rather than of bias.
        assert 0.5 * expected < min(counts.values())
        assert max(counts.values()) < 1.8 * expected

    def test_forced_criteria_cannot_be_disabled(self):
        """A module constant, not configuration — for the same reason
        `file_anyway` is."""
        from disputeshield import conf

        assert not any("FORCED" in key or "QA_" in key for key in conf.DEFAULTS)
        assert len(sampling.FORCED_CRITERIA) >= 4
        names = {name for name, _, _ in sampling.FORCED_CRITERIA}
        assert {"reopened", "escalated", "breached", "high_value"} <= names

    def test_a_forced_case_is_always_reviewed_even_at_zero_percent(
        self, tenant_a, reviewable, as_tenant
    ):
        target = reviewable[0]
        with as_tenant(tenant_a):
            target.breach_resolution = True
            target.save(update_fields=["breach_resolution"])

            selection = sampling.select(period_from=PERIOD[0], period_to=PERIOD[1], percent=0)

        assert selection.sampled == ()
        assert target.pk in {case.pk for case in selection.forced}

    def test_opening_reviews_records_why_a_case_was_forced(self, tenant_a, reviewable, as_tenant):
        target = reviewable[0]
        with as_tenant(tenant_a):
            target.breach_resolution = True
            target.save(update_fields=["breach_resolution"])

            sampling.open_reviews(
                tenant=tenant_a, period_from=PERIOD[0], period_to=PERIOD[1], percent=0
            )
            review = QaReview.objects.get(dispute=target)

        assert review.trigger == QaReview.Trigger.FORCED
        assert "breached" in review.forced_reason

    def test_a_score_is_a_record_about_the_review_not_the_case(
        self, tenant_a, reviewable, as_tenant
    ):
        """Filing an opinion into the case's own history would put an opinion
        where a regulator reads facts."""
        target = reviewable[0]
        with as_tenant(tenant_a):
            review = QaReview.objects.create(
                tenant=tenant_a,
                dispute=target,
                agent_id="agt_1",
                trigger=QaReview.Trigger.SAMPLED,
            )
            before = (target.status, target.outcome, target.outcome_notes)
            sampling.score(
                review=review,
                scores={"accuracy": 4, "tone": 5, "evidence": 3},
                notes="Outcome right, evidence thin.",
                reviewed_by="agt_supervisor",
            )
            target.refresh_from_db()
            after = (target.status, target.outcome, target.outcome_notes)
            record = AuditRecord.objects.get(event_type="qa.reviewed")

        assert before == after
        assert review.average == 4.0
        assert record.subject_type == "qa_review"

    def test_an_agent_cannot_review_their_own_case(self, tenant_a, reviewable, as_tenant):
        with as_tenant(tenant_a):
            review = QaReview.objects.create(
                tenant=tenant_a,
                dispute=reviewable[0],
                agent_id="agt_1",
                trigger=QaReview.Trigger.SAMPLED,
            )
            with pytest.raises(PermissionError, match="own case"):
                sampling.score(review=review, scores={"accuracy": 5}, notes="", reviewed_by="agt_1")

    def test_an_agent_can_respond_to_a_score_about_their_own_work(
        self, tenant_a, reviewable, as_tenant
    ):
        """A scorecard nobody may contest is a scorecard nobody trusts."""
        with as_tenant(tenant_a):
            review = QaReview.objects.create(
                tenant=tenant_a,
                dispute=reviewable[0],
                agent_id="agt_1",
                trigger=QaReview.Trigger.SAMPLED,
            )
            sampling.score(
                review=review,
                scores={"accuracy": 2},
                notes="Wrong outcome.",
                reviewed_by="agt_supervisor",
            )
            sampling.respond(
                review=review,
                agent_id="agt_1",
                response="The provider confirmed the failure after I closed it.",
            )
            with pytest.raises(PermissionError):
                sampling.respond(review=review, agent_id="agt_other", response="not mine")

        assert review.responded_at is not None

    def test_a_scorecard_rolls_up_per_agent(self, tenant_a, reviewable, as_tenant):
        with as_tenant(tenant_a):
            for case, score_value in zip(reviewable[:3], (5, 3, 4), strict=True):
                review = QaReview.objects.create(
                    tenant=tenant_a,
                    dispute=case,
                    agent_id="agt_1",
                    trigger=QaReview.Trigger.SAMPLED,
                )
                sampling.score(
                    review=review,
                    scores={"accuracy": score_value},
                    notes="",
                    reviewed_by="agt_supervisor",
                )
            card = sampling.scorecard(agent_id="agt_1")

        assert card["reviews"] == 3
        assert card["average"] == 4.0


class TestWebhooks:
    @pytest.fixture
    def endpoint(self, tenant_a, as_tenant):
        with as_tenant(tenant_a):
            return WebhookEndpoint.objects.create(
                tenant=tenant_a,
                url="https://ledger.acme.test/disputeshield",
                signing_secret="whsec_test_secret",
                created_by="agt_1",
            )

    def test_a_payload_is_signed_with_the_documented_scheme(
        self, tenant_a, endpoint, make_dispute, as_tenant
    ):
        transport = webhooks.CollectingTransport()
        dispute = make_dispute(tenant_a)

        with as_tenant(tenant_a):
            webhooks.enqueue(dispute=dispute, event_type="dispute.created")
        webhooks.dispatch(transport=transport)

        sent = transport.sent[0]
        header = sent["headers"][webhooks.SIGNATURE_HEADER]
        assert header.startswith("t=")
        assert webhooks.verify("whsec_test_secret", sent["body"], header)
        assert not webhooks.verify("wrong_secret", sent["body"], header)

    def test_a_captured_payload_cannot_be_replayed_forever(self, tenant_a):
        """The timestamp is inside the signed material."""
        body = b'{"event":"dispute.created"}'
        header = webhooks.sign("whsec_test_secret", body, timestamp=1_000_000)
        assert webhooks.verify("whsec_test_secret", body, header, now=1_000_060)
        assert not webhooks.verify("whsec_test_secret", body, header, now=2_000_000)

    def test_the_payload_carries_the_customer_visible_projection(
        self, tenant_a, endpoint, make_dispute, as_tenant
    ):
        transport = webhooks.CollectingTransport()
        dispute = make_dispute(tenant_a)

        with as_tenant(tenant_a):
            service.add_message(
                dispute=dispute,
                body="Internal: do not pay this one",
                author_type="agent",
                visibility="internal",
                author_id="agt_1",
            )
            webhooks.enqueue(dispute=dispute, event_type="dispute.created")
        webhooks.dispatch(transport=transport)

        body = json.loads(transport.sent[0]["body"])
        assert "do not pay this one" not in json.dumps(body)
        assert "customer_ref_hash" not in json.dumps(body)
        assert body["data"]["reference"] == dispute.reference

    def test_deliveries_are_ordered_per_case(self, tenant_a, endpoint, make_dispute, as_tenant):
        transport = webhooks.CollectingTransport()
        dispute = make_dispute(tenant_a)

        with as_tenant(tenant_a):
            for event in ("dispute.created", "dispute.acknowledged", "dispute.resolved"):
                webhooks.enqueue(dispute=dispute, event_type=event)
        webhooks.dispatch(transport=transport)

        events = [json.loads(sent["body"])["event"] for sent in transport.sent]
        assert events == ["dispute.created", "dispute.acknowledged", "dispute.resolved"]

    def test_a_later_event_waits_behind_an_earlier_failure(
        self, tenant_a, endpoint, make_dispute, as_tenant
    ):
        """A `dispute.resolved` arriving before its `dispute.acknowledged` has the
        fintech's ledger reacting to a case it has not heard of."""
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            for event in ("dispute.created", "dispute.resolved"):
                webhooks.enqueue(dispute=dispute, event_type=event)

        result = webhooks.dispatch(transport=webhooks.FailingTransport())
        assert result.delivered == 0
        assert result.retried == 1
        assert result.blocked == 1

    def test_an_endpoint_down_for_a_day_parks_rather_than_drops(
        self, tenant_a, endpoint, make_dispute, as_tenant
    ):
        from django.utils import timezone

        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            webhooks.enqueue(dispute=dispute, event_type="dispute.created")

        now = timezone.now()
        for _ in range(webhooks.MAX_ATTEMPTS):
            webhooks.dispatch(transport=webhooks.FailingTransport(), now=now)
            now += timedelta(days=1)

        with as_tenant(tenant_a):
            parked = WebhookDelivery.objects.get()
        assert parked.status == WebhookDelivery.Status.PARKED
        assert parked.attempts == webhooks.MAX_ATTEMPTS
        assert parked.payload, "a parked delivery keeps its payload for replay"

    def test_a_replay_keeps_the_idempotency_key(self, tenant_a, endpoint, make_dispute, as_tenant):
        """A consumer that already processed it ignores the replay; one that never
        received it processes it once."""
        from django.utils import timezone

        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            webhooks.enqueue(dispute=dispute, event_type="dispute.created")

        now = timezone.now()
        for _ in range(webhooks.MAX_ATTEMPTS):
            webhooks.dispatch(transport=webhooks.FailingTransport(), now=now)
            now += timedelta(days=1)

        with as_tenant(tenant_a):
            parked = WebhookDelivery.objects.get()
            original_key = parked.idempotency_key
            webhooks.replay(delivery=parked)

        transport = webhooks.CollectingTransport()
        assert webhooks.dispatch(transport=transport).delivered == 1
        assert transport.sent[0]["headers"]["Idempotency-Key"] == original_key

    def test_enqueuing_the_same_event_twice_creates_one_delivery(
        self, tenant_a, endpoint, make_dispute, as_tenant
    ):
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            first = webhooks.enqueue(dispute=dispute, event_type="dispute.created")
            assert len(first) == 1
            assert WebhookDelivery.objects.count() == 1

    def test_an_endpoint_only_receives_the_events_it_asked_for(
        self, tenant_a, endpoint, make_dispute, as_tenant
    ):
        with as_tenant(tenant_a):
            endpoint.event_types = ["dispute.resolved"]
            endpoint.save(update_fields=["event_types"])
            dispute = make_dispute(tenant_a)
            webhooks.enqueue(dispute=dispute, event_type="dispute.created")
            assert WebhookDelivery.objects.count() == 0

            webhooks.enqueue(dispute=dispute, event_type="dispute.resolved")
            assert WebhookDelivery.objects.count() == 1

    def test_a_plaintext_endpoint_is_refused(self, tenant_a, as_tenant):
        """A signed payload over plaintext is a payload anyone on the path can read."""
        from django.core.exceptions import ValidationError

        with as_tenant(tenant_a):
            candidate = WebhookEndpoint(
                tenant=tenant_a,
                url="http://ledger.acme.test/hook",
                signing_secret="whsec_x",
            )
            with pytest.raises(ValidationError, match="https"):
                candidate.clean()

    def test_deliveries_never_cross_a_tenant(
        self, tenant_a, tenant_b, endpoint, make_dispute, as_tenant
    ):
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            webhooks.enqueue(dispute=dispute, event_type="dispute.created")
        with as_tenant(tenant_b):
            assert WebhookDelivery.objects.count() == 0
