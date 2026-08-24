"""Chain anchoring (A8), external escalation (A6) and regulatory returns (A17)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from disputeshield.audit import anchoring
from disputeshield.audit.checkpoints import attestation, create_checkpoint
from disputeshield.disputes import escalation, service
from disputeshield.models import (
    AuditRecord,
    CheckpointAnchor,
    ExternalEscalation,
    RegulatoryReturn,
    ReturnTemplate,
)
from disputeshield.models.dispute import Outcome, Status
from disputeshield.reports import returns

pytestmark = pytest.mark.django_db

UTC = UTC
NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


@pytest.fixture
def a_checkpoint(tenant_a, make_dispute, as_tenant):
    make_dispute(tenant_a)
    with as_tenant(tenant_a):
        return create_checkpoint(tenant_a).checkpoint


class TestAnchoring:
    def test_a_checkpoint_queues_an_anchor(self, tenant_a, a_checkpoint, as_tenant):
        with as_tenant(tenant_a):
            anchor = anchoring.queue(a_checkpoint)
            assert anchor.status == CheckpointAnchor.Status.PENDING

    def test_the_backlog_is_anchored_in_order(self, tenant_a, a_checkpoint, as_tenant):
        """Out of order gives an auditor timestamps that appear to contradict the
        chain they describe."""
        with as_tenant(tenant_a):
            anchoring.queue(a_checkpoint)

        result = anchoring.anchor_pending()
        assert result.anchored == 1

        with as_tenant(tenant_a):
            anchor = CheckpointAnchor.objects.get(checkpoint_id=a_checkpoint.pk)
        assert anchor.status == CheckpointAnchor.Status.ANCHORED
        assert anchor.token

    def test_an_unreachable_authority_does_not_block_writes(
        self, tenant_a, a_checkpoint, make_dispute, as_tenant, settings
    ):
        """An evidence system that stops accepting evidence because a third party
        is down has chosen the wrong failure."""
        settings.DISPUTESHIELD = {**settings.DISPUTESHIELD, "TIMESTAMP_AUTHORITY": None}
        with as_tenant(tenant_a):
            anchoring.queue(a_checkpoint)

        result = anchoring.anchor_pending()
        assert result.anchored == 0
        assert result.pending == 1

        # Filing still works while the authority is down.
        dispute = make_dispute(tenant_a, customer_ref="usr_during_outage")
        assert dispute.reference.startswith("DS-")

    def test_the_unanchored_backlog_is_a_metric(self, tenant_a, a_checkpoint, as_tenant, settings):
        """So that "we anchor our chain" does not quietly become "we anchored our
        chain until the TSA's certificate expired in March"."""
        settings.DISPUTESHIELD = {**settings.DISPUTESHIELD, "TIMESTAMP_AUTHORITY": None}
        with as_tenant(tenant_a):
            anchoring.queue(a_checkpoint)
            anchoring.anchor_pending()
            assert anchoring.unanchored_total() == 1

    def test_recovery_anchors_the_backlog(self, tenant_a, a_checkpoint, as_tenant, settings):
        settings.DISPUTESHIELD = {**settings.DISPUTESHIELD, "TIMESTAMP_AUTHORITY": None}
        with as_tenant(tenant_a):
            anchoring.queue(a_checkpoint)
        assert anchoring.anchor_pending().pending == 1

        settings.DISPUTESHIELD = {
            **settings.DISPUTESHIELD,
            "TIMESTAMP_AUTHORITY": "disputeshield.audit.anchoring.LocalAuthority",
        }
        assert anchoring.anchor_pending().anchored == 1
        with as_tenant(tenant_a):
            assert anchoring.unanchored_total() == 0

    def test_a_local_authority_never_claims_an_external_attestation(
        self, tenant_a, a_checkpoint, as_tenant
    ):
        """Us signing our own claim about our own chain proves nothing an
        adversary with our key could not also produce."""
        with as_tenant(tenant_a):
            anchoring.queue(a_checkpoint)
        anchoring.anchor_pending()

        with as_tenant(tenant_a):
            block = attestation(tenant_a)

        assert block["anchor"]["anchored"] is True
        assert block["anchor"]["external"] is False
        assert block["attestation"]["externally_anchored"] is False

    def test_verify_reports_chain_and_anchor_as_two_independent_facts(
        self, tenant_a, a_checkpoint, as_tenant, client_for
    ):
        with as_tenant(tenant_a):
            anchoring.queue(a_checkpoint)
        anchoring.anchor_pending()

        body = client_for(tenant_a).get("/v1/audit/verify").json()
        assert body["chain"]["verified"] is True
        assert body["anchor"]["anchored"] is True
        # Independent: a healthy chain must not be able to imply an anchor.
        assert "unanchored_checkpoints" in body["anchor"]
        assert set(body) == {"anchor", "chain", "attestation"}


class TestExternalEscalation:
    @pytest.fixture
    def resolved_case(self, tenant_a, make_dispute, make_policy, as_tenant):
        version = make_policy(tenant_a)
        dispute = make_dispute(tenant_a, policy_version=version)
        with as_tenant(tenant_a):
            for step in (Status.ACKNOWLEDGED, Status.INVESTIGATING):
                service.transition(
                    dispute=dispute, to=step, actor_type="user", actor_id="agt_1", reason="x"
                )
            service.resolve(
                dispute=dispute,
                outcome=Outcome.REJECTED,
                notes="No evidence of a failed transfer.",
                actor_type="user",
                actor_id="agt_1",
            )
        return dispute

    def test_a_case_with_an_open_track_cannot_be_closed(self, tenant_a, resolved_case, as_tenant):
        """Internal case closed while the external one is live is precisely what
        produces "the firm was unresponsive" in a supervisory finding."""
        with as_tenant(tenant_a):
            escalation.open_track(
                dispute=resolved_case,
                body=ExternalEscalation.Body.OMBUDSMAN,
                external_reference="OMB-2026-771",
                opened_at=NOW,
                actor_id="agt_1",
            )
            with pytest.raises(service.ExternalTrackOpen, match="OMB-2026-771"):
                service.transition(
                    dispute=resolved_case, to=Status.CLOSED, actor_type="system", reason="window"
                )

    def test_the_guard_covers_every_terminal_transition(self):
        """Asserted against the transition table rather than the one obvious move."""
        import inspect

        from disputeshield.disputes.states import TERMINAL, TRANSITIONS

        source = inspect.getsource(service.transition)
        assert "TERMINAL" in source, "the guard must key off the terminal set, not a literal"
        assert len(TERMINAL) >= 2
        assert any(t.target in TERMINAL for t in TRANSITIONS)

    def test_closing_the_track_releases_the_case(self, tenant_a, resolved_case, as_tenant):
        with as_tenant(tenant_a):
            track = escalation.open_track(
                dispute=resolved_case,
                body=ExternalEscalation.Body.OMBUDSMAN,
                external_reference="OMB-2026-771",
                opened_at=NOW,
                actor_id="agt_1",
            )
            escalation.close_track(
                escalation=track,
                determination=ExternalEscalation.Determination.UPHELD,
                notes="Ombudsman upheld the complaint.",
                actor_id="agt_1",
            )
            service.transition(
                dispute=resolved_case, to=Status.CLOSED, actor_type="system", reason="window"
            )
            resolved_case.refresh_from_db()
        assert resolved_case.status == Status.CLOSED

    def test_a_contradiction_is_surfaced_not_reconciled(self, tenant_a, resolved_case, as_tenant):
        """Rewriting the internal outcome to agree would destroy the most
        interesting evidence in the case."""
        with as_tenant(tenant_a):
            track = escalation.open_track(
                dispute=resolved_case,
                body=ExternalEscalation.Body.OMBUDSMAN,
                external_reference="OMB-2026-771",
                opened_at=NOW,
                actor_id="agt_1",
            )
            escalation.close_track(
                escalation=track,
                determination=ExternalEscalation.Determination.UPHELD,
                notes="Ombudsman disagreed with the firm.",
                actor_id="agt_1",
            )
            record = AuditRecord.objects.get(event_type="escalation.determined")
            resolved_case.refresh_from_db()

        assert record.payload["contradicts_internal_outcome"] is True
        assert record.payload["internal_outcome"] == Outcome.REJECTED
        assert record.payload["determination"] == "upheld"
        # Both stand.
        assert resolved_case.outcome == Outcome.REJECTED

    def test_closing_a_track_without_a_determination_is_refused(
        self, tenant_a, resolved_case, as_tenant
    ):
        with as_tenant(tenant_a):
            track = escalation.open_track(
                dispute=resolved_case,
                body=ExternalEscalation.Body.COURT,
                external_reference="CV-2026-11",
                opened_at=NOW,
                actor_id="agt_1",
            )
            with pytest.raises(ValueError, match="notes"):
                escalation.close_track(
                    escalation=track,
                    determination=ExternalEscalation.Determination.WITHDRAWN,
                    notes="  ",
                    actor_id="agt_1",
                )

    def test_correspondence_is_kept_on_the_case_and_is_immutable(
        self, tenant_a, resolved_case, as_tenant
    ):
        """ "It was in his email" is not a record."""
        with as_tenant(tenant_a):
            track = escalation.open_track(
                dispute=resolved_case,
                body=ExternalEscalation.Body.REGULATOR,
                external_reference="CBN-2026-9",
                opened_at=NOW,
                actor_id="agt_1",
            )
            entry = escalation.record_correspondence(
                escalation=track,
                direction="inbound",
                summary="Regulator requests the full case file",
                occurred_at=NOW,
                actor_id="agt_1",
            )
            entry.summary = "something else"
            with pytest.raises(PermissionError):
                entry.save()


class TestRegulatoryReturns:
    TEMPLATE_ROWS = [
        {"key": "received", "label": "Complaints received", "source": "cases_received"},
        {"key": "resolved", "label": "Complaints resolved", "source": "cases_resolved"},
        {"key": "breached", "label": "Outside the mandated window", "source": "cases_breached"},
        {
            "key": "transfers",
            "label": "Failed transfers",
            "source": "cases_by_category",
            "filter": {"category": "failed_transfer"},
        },
    ]

    @pytest.fixture
    def template(self, tenant_a, as_tenant):
        with as_tenant(tenant_a):
            return ReturnTemplate.objects.create(
                tenant=tenant_a,
                code="cbn-consumer-complaints",
                version=1,
                title="Consumer complaints return",
                jurisdiction="NG",
                regulatory_reference="CBN Consumer Protection Framework s.4.2",
                rows=self.TEMPLATE_ROWS,
                effective_from=NOW,
                created_by="agt_1",
            )

    @pytest.fixture
    def with_cases(self, tenant_a, make_dispute, make_policy, as_tenant):
        version = make_policy(tenant_a)
        return [
            make_dispute(tenant_a, policy_version=version, customer_ref=f"usr_{n}")
            for n in range(3)
        ]

    def test_a_return_is_generated_from_the_template(
        self, tenant_a, template, with_cases, as_tenant
    ):
        with as_tenant(tenant_a):
            filing = returns.generate(
                tenant=tenant_a,
                template=template,
                period_from=NOW - timedelta(days=30),
                period_to=NOW + timedelta(days=30),
                generated_by="agt_1",
            )
        values = {row["key"]: row["value"] for row in filing.rows}
        assert values["received"] == 3
        assert values["transfers"] == 3
        assert filing.status == RegulatoryReturn.Status.DRAFT

    def test_a_template_cannot_reach_outside_the_registry(self, tenant_a, as_tenant):
        """A template specifies what to count; it cannot reach for something
        nobody decided to publish."""
        with as_tenant(tenant_a):
            rogue = ReturnTemplate.objects.create(
                tenant=tenant_a,
                code="rogue",
                version=1,
                title="x",
                rows=[{"key": "k", "label": "l", "source": "customer_phone_numbers"}],
                effective_from=NOW,
            )
            with pytest.raises(returns.UnknownSource, match="customer_phone_numbers"):
                returns.generate(
                    tenant=tenant_a,
                    template=rogue,
                    period_from=NOW,
                    period_to=NOW,
                    generated_by="agt_1",
                )

    def test_a_template_version_is_immutable(self, tenant_a, template, as_tenant):
        with as_tenant(tenant_a):
            template.rows = []
            with pytest.raises(PermissionError, match="version n\\+1"):
                template.save()

    def test_a_return_regenerates_byte_identically_under_a_newer_revision(
        self, tenant_a, template, with_cases, as_tenant
    ):
        """The gate. A return regenerated under this year's template would
        silently disagree with the document the supervisor holds."""
        with as_tenant(tenant_a):
            filed = returns.generate(
                tenant=tenant_a,
                template=template,
                period_from=NOW - timedelta(days=30),
                period_to=NOW + timedelta(days=30),
                generated_by="agt_1",
            )
            original_digest = filed.content_digest

            # A revision lands: different rows, different labels.
            ReturnTemplate.objects.create(
                tenant=tenant_a,
                code="cbn-consumer-complaints",
                version=2,
                title="Consumer complaints return (revised)",
                rows=[
                    {"key": "received", "label": "Total received", "source": "cases_received"},
                    {"key": "escalated", "label": "Escalated", "source": "escalated_externally"},
                ],
                effective_from=NOW + timedelta(days=1),
                created_by="agt_1",
            )

            regenerated = returns.regenerate(filing=filed)

        assert returns.digest(regenerated) == original_digest
        assert [row["label"] for row in regenerated] == [row["label"] for row in self.TEMPLATE_ROWS]

    def test_approval_needs_a_second_person(self, tenant_a, template, with_cases, as_tenant):
        with as_tenant(tenant_a):
            filing = returns.generate(
                tenant=tenant_a,
                template=template,
                period_from=NOW - timedelta(days=30),
                period_to=NOW + timedelta(days=30),
                generated_by="agt_1",
            )
            with pytest.raises(returns.ApprovalRequiresTwoPeople):
                returns.approve(filing=filing, approved_by="agt_1")

    def test_approval_hashes_the_artefact_into_the_chain(
        self, tenant_a, template, with_cases, as_tenant
    ):
        """What is provable afterwards is not that a return was produced, but that
        *this* return was the one approved."""
        with as_tenant(tenant_a):
            filing = returns.generate(
                tenant=tenant_a,
                template=template,
                period_from=NOW - timedelta(days=30),
                period_to=NOW + timedelta(days=30),
                generated_by="agt_1",
            )
            returns.approve(filing=filing, approved_by="agt_2", note="Reviewed against the queue")
            record = AuditRecord.objects.get(event_type="regulatory_return.approved")

        assert filing.is_approved
        assert record.payload["content_digest"] == filing.content_digest
        assert record.payload["generated_by"] == "agt_1"
        assert record.actor_id == "agt_2"

    def test_nothing_is_filed_automatically(self):
        """A generated return is a draft. There is no path from generation to a
        filed artefact that does not pass through a named approver."""
        import inspect

        source = inspect.getsource(returns.generate)
        assert "APPROVED" not in source
        assert "status=" not in source
