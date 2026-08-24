"""The regulatory export, its attestation, and the breach analysis.

The gate this file exists for: **exporting the same period twice produces
identical bytes**. A supervisor who asks for the same period twice and gets two
different files has been handed a reason to doubt everything else in the bundle.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from disputeshield.audit.checkpoints import (
    attestation,
    create_checkpoint,
    verify_signature,
)
from disputeshield.models import AuditCheckpoint
from disputeshield.reports import analytics, regulatory

pytestmark = pytest.mark.django_db

UTC = UTC
PERIOD_FROM = datetime(2026, 1, 1, tzinfo=UTC)
PERIOD_TO = datetime(2027, 1, 1, tzinfo=UTC)


@pytest.fixture
def a_period_of_cases(tenant_a, make_dispute, make_policy, as_tenant):
    """Three cases with different shapes, so the export has something to order."""
    from disputeshield.disputes import service
    from disputeshield.models.dispute import Outcome, Status

    version = make_policy(tenant_a, resolution_hours=8)
    cases = [
        make_dispute(tenant_a, policy_version=version, customer_ref=f"usr_{n}") for n in range(3)
    ]

    with as_tenant(tenant_a):
        for step in (Status.ACKNOWLEDGED, Status.INVESTIGATING):
            service.transition(
                dispute=cases[0], to=step, actor_type="user", actor_id="agt_1", reason="working"
            )
        service.resolve(
            dispute=cases[0],
            outcome=Outcome.UPHELD,
            notes="Reversal confirmed.",
            refund_amount_minor=5_000_000,
            actor_type="user",
            actor_id="agt_1",
        )
        cases[1].breach_resolution = True
        cases[1].breach_reason = "Beat scheduler stalled; INC-2026-0823"
        cases[1].save(update_fields=["breach_resolution", "breach_reason"])
    return tenant_a, cases


class TestByteReproducibility:
    def test_exporting_the_same_period_twice_is_identical(self, a_period_of_cases, as_tenant):
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            first = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
            second = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)

        assert first.files == second.files
        assert first.manifest["files"] == second.manifest["files"]
        assert first.manifest["signature"] == second.manifest["signature"]

    def test_the_zip_bytes_are_identical_too(self, a_period_of_cases, as_tenant):
        """Archive metadata carries timestamps unless they are pinned."""
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            a = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
            b = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        assert a.as_zip() == b.as_zip()

    def test_the_csv_line_terminator_is_fixed(self, a_period_of_cases, as_tenant):
        """The platform default would make an export produced on Windows differ
        from the same export produced on Linux."""
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            export = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        assert b"\r\n" not in export.files["cases.csv"]

    def test_ordering_is_total_and_stable(self, a_period_of_cases, as_tenant):
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            export = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        rows = export.files["cases.csv"].decode().strip().split("\n")[1:]
        references = [row.split(",")[0] for row in rows]
        assert references == sorted(references)

    def test_amounts_are_integer_minor_units(self, a_period_of_cases, as_tenant):
        """A supervisor reconciling against a ledger needs the same integer the
        ledger holds, not a rendering of it."""
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            export = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        body = export.files["cases.csv"].decode()
        assert "5000000" in body
        assert "50000.00" not in body


class TestTheAttestation:
    def test_the_manifest_publishes_the_file_digests(self, a_period_of_cases, as_tenant):
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            export = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        for name, body in export.files.items():
            assert export.manifest["files"][name] == hashlib.sha256(body).hexdigest()

    def test_a_tampered_file_no_longer_matches_its_digest(self, a_period_of_cases, as_tenant):
        """What a supervisor actually does with the manifest."""
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            export = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        tampered = export.files["cases.csv"].replace(b"upheld", b"rejected")
        assert hashlib.sha256(tampered).hexdigest() != export.manifest["files"]["cases.csv"]

    def test_the_integrity_block_verifies_against_the_chain(self, a_period_of_cases, as_tenant):
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            export = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        assert export.manifest["integrity"]["chain"]["verified"] is True
        assert export.manifest["integrity"]["chain"]["records_checked"] > 0

    def test_a_broken_chain_is_reported_in_the_export(
        self, a_period_of_cases, as_tenant, tamper, raw_sql
    ):
        """An export from a database whose chain does not verify must say so.
        Producing a clean-looking bundle from a tampered history is the single
        worst thing this feature could do."""
        tenant, _ = a_period_of_cases
        with tamper(tenant):
            raw_sql(
                "UPDATE disputeshield_auditrecord SET payload = %s WHERE sequence = 3",
                ['{"tampered": true}'],
            )
        with as_tenant(tenant):
            export = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        assert export.manifest["integrity"]["chain"]["verified"] is False
        assert export.manifest["integrity"]["chain"]["first_break"] == 3

    def test_the_history_carries_each_records_own_hash(self, a_period_of_cases, as_tenant):
        """So a supervisor can spot-check any row against the chain rather than
        trusting the export wholesale."""
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            export = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        assert export.files["history.csv"].count(b"sha256:") > 0


class TestCheckpoints:
    def test_a_checkpoint_is_created_and_signs_itself(self, a_period_of_cases, as_tenant):
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            result = create_checkpoint(tenant)
            assert result.verified
            assert verify_signature(result.checkpoint)

    def test_a_tampered_checkpoint_fails_its_signature(self, a_period_of_cases, as_tenant):
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            result = create_checkpoint(tenant)
            result.checkpoint.head_hash = "sha256:" + "0" * 64
            assert not verify_signature(result.checkpoint)

    def test_a_checkpoint_cannot_be_rewritten(self, a_period_of_cases, as_tenant):
        """A statement that can be edited is one that can be made to agree with a
        chain after the chain was altered."""
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            result = create_checkpoint(tenant)
            result.checkpoint.verified = False
            with pytest.raises(PermissionError):
                result.checkpoint.save()

    def test_a_failed_verification_still_produces_a_checkpoint(
        self, a_period_of_cases, as_tenant, tamper, raw_sql
    ):
        """Silence after a failed check is indistinguishable from the job not
        having run, and §11.4 pages on exactly this condition."""
        tenant, _ = a_period_of_cases
        with tamper(tenant):
            raw_sql(
                "UPDATE disputeshield_auditrecord SET payload = %s WHERE sequence = 3",
                ['{"tampered": true}'],
            )
        with as_tenant(tenant):
            result = create_checkpoint(tenant)
            assert result.checkpoint is not None
            assert result.checkpoint.verified is False
            assert result.checkpoint.failure_detail

    def test_a_second_checkpoint_over_an_unchanged_chain_is_not_created(
        self, a_period_of_cases, as_tenant
    ):
        """Two attestations for one state read as a change to an auditor
        comparing them."""
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            first = create_checkpoint(tenant)
            second = create_checkpoint(tenant)
            assert first.checkpoint.pk == second.checkpoint.pk
            assert AuditCheckpoint.objects.count() == 1

    def test_the_attestation_separates_the_three_claims(self, a_period_of_cases, as_tenant):
        """The chain says nothing was altered. The signature says we computed
        that. The anchor says somebody outside this system agrees the chain
        existed when we say it did. Collapsing them into one boolean is how the
        weakest becomes the headline (phase 8 added the third)."""
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            create_checkpoint(tenant)
            block = attestation(tenant)
        assert set(block) == {"anchor", "chain", "attestation"}
        assert "when the chain existed" in block["attestation"]["note"]


class TestThroughTheApi:
    def test_compliance_can_download_the_bundle(
        self, a_period_of_cases, client_for, make_agent, as_tenant
    ):
        from disputeshield.models import Agent

        tenant, _ = a_period_of_cases
        officer = make_agent(tenant, email="adaeze@example.com", role=Agent.Role.COMPLIANCE)

        response = client_for(tenant, agent=officer).get("/v1/reports/regulatory")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/zip"

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert sorted(archive.namelist()) == [
                "cases.csv",
                "history.csv",
                "manifest.json",
                "report.pdf",
            ]
            manifest = json.loads(archive.read("manifest.json"))
        assert manifest["case_count"] == 3

    def test_an_agent_cannot_export_the_whole_period(
        self, a_period_of_cases, client_for, make_agent
    ):
        """An export is a disclosure of every case in the period."""
        from disputeshield.models import Agent

        tenant, _ = a_period_of_cases
        agent = make_agent(tenant, email="ngozi@example.com", role=Agent.Role.AGENT)
        assert client_for(tenant, agent=agent).get("/v1/reports/regulatory").status_code == 404

    def test_audit_verify_is_published(self, a_period_of_cases, client_for):
        tenant, _ = a_period_of_cases
        body = client_for(tenant).get("/v1/audit/verify").json()
        assert body["chain"]["verified"] is True
        assert body["attestation"]["externally_anchored"] is False

    def test_another_tenants_cases_never_appear(
        self, a_period_of_cases, tenant_b, make_dispute, make_policy, client_for, as_tenant
    ):
        tenant, _ = a_period_of_cases
        version = make_policy(tenant_b)
        theirs = make_dispute(tenant_b, policy_version=version, customer_ref="usr_b")

        with as_tenant(tenant):
            export = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        assert theirs.reference.encode() not in export.files["cases.csv"]


# Analytics genuinely reads from the replica (§11.1), which is a separate
# connection even when it mirrors the same database. A non-transactional test
# holds its data in an uncommitted transaction on the primary, where that
# connection cannot see it — so these commit. The alternative was leaving
# analytics on the primary and a docstring claiming otherwise.
@pytest.mark.django_db(transaction=True, databases=["default", "replica"])
class TestBreachAnalysis:
    def test_performance_groups_by_category(self, a_period_of_cases, as_tenant):
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            rows = analytics.sla_performance(
                period_from=PERIOD_FROM, period_to=PERIOD_TO, group_by="category"
            )
        assert rows[0]["cases"] == 3
        assert rows[0]["breached"] == 1
        assert rows[0]["breach_rate"] == pytest.approx(1 / 3, abs=1e-4)

    def test_causes_separate_documented_from_undocumented(self, a_period_of_cases, as_tenant):
        """§11.5 step 5's annotation earning its keep: the undocumented breaches
        become visible as a group."""
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            causes = analytics.breach_causes(period_from=PERIOD_FROM, period_to=PERIOD_TO)
        assert causes == [{"cause": "Beat scheduler stalled; INC-2026-0823", "cases": 1}]

    def test_an_unknown_grouping_is_refused(self, a_period_of_cases, as_tenant):
        tenant, _ = a_period_of_cases
        with as_tenant(tenant), pytest.raises(ValueError, match=r"category.*agent"):
            analytics.sla_performance(
                period_from=PERIOD_FROM, period_to=PERIOD_TO, group_by="customer"
            )

    def test_the_summary_reports_recorded_refunds_without_moving_money(
        self, a_period_of_cases, as_tenant
    ):
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            block = analytics.summary(period_from=PERIOD_FROM, period_to=PERIOD_TO)
        assert block["recorded_refund_minor"] == 5_000_000
        assert block["cases"] == 3

    def test_the_summary_names_the_currencies_its_totals_are_made_of(
        self, a_period_of_cases, as_tenant
    ):
        """A refund total is a sum of minor units, with nothing checking they are
        the same unit.

        A period holding both NGN and USD cases produces a figure that adds kobo
        to cents. The figure is still reported — narrowing it would hide cases
        from a regulatory count — but a caller needs to know when presenting it
        as money would be a lie. The dashboard refuses to render a single total
        on the strength of this field.
        """
        tenant, cases = a_period_of_cases

        # Each write closes its tenant block before the read. `summary()` runs on
        # the replica, which is a separate connection — a change still inside an
        # uncommitted transaction here is invisible there, and the assertion would
        # be measuring transaction visibility rather than the code.
        with as_tenant(tenant):
            for case in cases:
                case.currency = "NGN"
                case.save(update_fields=["currency"])
        with as_tenant(tenant):
            assert analytics.summary(period_from=PERIOD_FROM, period_to=PERIOD_TO)[
                "currencies"
            ] == ["NGN"]

        with as_tenant(tenant):
            cases[1].currency = "USD"
            cases[1].save(update_fields=["currency"])
        with as_tenant(tenant):
            mixed = analytics.summary(period_from=PERIOD_FROM, period_to=PERIOD_TO)

        assert mixed["currencies"] == ["NGN", "USD"]
        # Still counted. Reporting fewer cases would be the worse failure.
        assert mixed["cases"] == 3

    def test_the_endpoint_returns_rows_and_causes(self, a_period_of_cases, client_for):
        tenant, _ = a_period_of_cases
        body = client_for(tenant).get("/v1/analytics/sla-performance").json()
        assert body["group_by"] == "category"
        assert body["summary"]["cases"] == 3
        assert body["causes"]

    def test_a_period_filter_narrows_it(self, a_period_of_cases, client_for):
        tenant, _ = a_period_of_cases
        future = (datetime.now(UTC) + timedelta(days=365)).date().isoformat()
        body = client_for(tenant).get(f"/v1/analytics/sla-performance?from={future}").json()
        assert body["summary"]["cases"] == 0
