"""The regulator-facing PDF (§6.5, §7.3's `format=pdf`).

The same gate the CSVs carry, for the same reason: **exporting the same period
twice produces identical bytes.** PDFs make that harder, because the format has
several places for a timestamp to hide — a creation date, a modification date and
a document ID, all regenerated on every build unless they are suppressed.

The second gate is about what the document *says*. A regulator-ready artefact
that overclaims is worse than one that says nothing, so the attestation page has
to state what it does not prove, and a failed chain has to appear on page one
rather than be quietly omitted.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import UTC, datetime

import pytest

from disputeshield.disputes import service
from disputeshield.models import Agent
from disputeshield.models.dispute import Outcome, Status
from disputeshield.reports import pdf, regulatory

pytestmark = pytest.mark.django_db

PERIOD_FROM = datetime(2026, 1, 1, tzinfo=UTC)
PERIOD_TO = datetime(2027, 1, 1, tzinfo=UTC)


def text_of(document: bytes) -> str:
    """Extract the drawn strings from an uncompressed-enough PDF.

    Deliberately crude — a full parser would be a dependency whose bugs we would
    then be debugging. Every string reportlab draws appears in a text-showing
    operator, and that is enough to assert what the document says.
    """
    import zlib

    chunks: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", document, re.S):
        raw = match.group(1)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        chunks.extend(part.decode("latin-1") for part in re.findall(rb"\((?:\\.|[^\\()])*\)", raw))
    return " ".join(chunk.strip("()") for chunk in chunks)


@pytest.fixture
def a_period_of_cases(tenant_a, make_dispute, make_policy, as_tenant):
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
    def test_the_same_period_renders_identical_bytes(self, a_period_of_cases, as_tenant):
        """The gate. A supervisor who asks twice and gets two different files has
        been handed a reason to doubt the rest of the bundle."""
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            first = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
            second = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        assert first.files["report.pdf"] == second.files["report.pdf"]

    def test_the_creation_metadata_is_pinned_rather_than_current(
        self, a_period_of_cases, as_tenant
    ):
        """`invariant=1` does not remove CreationDate, ModDate and the document
        ID — it pins them. Absence was never the requirement; sameness is."""
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            first = regulatory.build(
                tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO
            ).files["report.pdf"]
            second = regulatory.build(
                tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO
            ).files["report.pdf"]

        for field in (rb"/CreationDate\s*\(([^)]*)\)", rb"/ID\s*\[([^\]]*)\]"):
            assert re.search(field, first)
            assert re.search(field, first).group(1) == re.search(field, second).group(1)

    def test_the_streams_are_uncompressed(self, a_period_of_cases, as_tenant):
        """So reproducibility does not depend on which build of zlib rendered it."""
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            document = regulatory.build(
                tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO
            ).files["report.pdf"]
        assert b"/Filter" not in document or b"FlateDecode" not in document

    def test_the_whole_zip_is_still_reproducible(self, a_period_of_cases, as_tenant):
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            a = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
            b = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        assert a.as_zip() == b.as_zip()

    def test_it_uses_only_built_in_fonts(self, a_period_of_cases, as_tenant):
        """An embedded font is subsetted, and a subset carries a generated name
        that differs between builds."""
        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            document = regulatory.build(
                tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO
            ).files["report.pdf"]
        assert b"/FontFile" not in document


class TestTheManifestCoversIt:
    def test_the_pdf_digest_is_published_and_signed(self, a_period_of_cases, as_tenant):
        import hashlib

        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            export = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)

        assert (
            export.manifest["files"]["report.pdf"]
            == hashlib.sha256(export.files["report.pdf"]).hexdigest()
        )

    def test_a_tampered_pdf_no_longer_matches(self, a_period_of_cases, as_tenant):
        import hashlib

        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            export = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        tampered = export.files["report.pdf"].replace(b"Complaints", b"Compliments")
        assert hashlib.sha256(tampered).hexdigest() != export.manifest["files"]["report.pdf"]

    def test_every_format_reports_the_same_manifest(
        self, a_period_of_cases, client_for, make_agent
    ):
        """A period has one manifest, whichever way it is asked for.

        This is what makes the PDF non-optional. Building the bundle without it
        would sign a different file list, and a supervisor comparing the manifest
        they fetched as JSON against the one inside the zip would find two
        signatures for the same period and no way to tell which is authoritative.
        """
        tenant, _ = a_period_of_cases
        officer = make_agent(tenant, email="adaeze@example.com", role=Agent.Role.COMPLIANCE)
        client = client_for(tenant, agent=officer)

        as_json = client.get("/v1/reports/regulatory?format=json").json()
        zipped = client.get("/v1/reports/regulatory").content
        with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
            inside = json.loads(archive.read("manifest.json"))

        assert as_json == inside
        assert set(as_json["files"]) == {"cases.csv", "history.csv", "report.pdf"}


class TestWhatTheDocumentSays:
    def _text(self, tenant, as_tenant) -> str:
        with as_tenant(tenant):
            export = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        return text_of(export.files["report.pdf"])

    def test_it_leads_with_the_attestation(self, a_period_of_cases, as_tenant):
        tenant, _ = a_period_of_cases
        body = self._text(tenant, as_tenant)
        assert "Integrity attestation" in body
        assert "VERIFIED" in body

    def test_it_states_what_the_attestation_does_not_prove(self, a_period_of_cases, as_tenant):
        """A regulator-ready document that overclaims is worse than one that says
        nothing."""
        tenant, _ = a_period_of_cases
        body = self._text(tenant, as_tenant)
        assert "does not establish" in body or "does and does not" in body
        assert "External anchor" in body

    def test_it_reports_the_counts(self, a_period_of_cases, as_tenant):
        tenant, cases = a_period_of_cases
        body = self._text(tenant, as_tenant)
        assert "Complaints in period" in body
        assert "Breached a mandated window" in body
        assert cases[0].reference in body

    def test_a_broken_chain_appears_on_the_first_page(
        self, a_period_of_cases, as_tenant, tamper, raw_sql
    ):
        """Producing a clean-looking document from a tampered history is the worst
        thing this feature could do."""
        tenant, _ = a_period_of_cases
        with tamper(tenant):
            raw_sql(
                "UPDATE disputeshield_auditrecord SET payload = %s WHERE sequence = 3",
                ['{"tampered": true}'],
            )
        body = self._text(tenant, as_tenant)

        assert "FAILED VERIFICATION" in body
        assert "cannot be relied upon" in body

    def test_imported_history_is_distinguished(self, tenant_a, make_policy, as_tenant):
        from disputeshield.migration import importer
        from disputeshield.models import ImportBatch

        make_policy(tenant_a, category="failed_transfer")
        with as_tenant(tenant_a):
            importer.import_csv(
                tenant=tenant_a,
                content=(
                    b"external_reference,customer_ref,category,description,opened_at,closed_at\n"
                    b"ZD-1,cust-a,failed_transfer,Old complaint,2026-03-04T09:00:00Z,\n"
                ),
                source=ImportBatch.Source.ZENDESK,
                imported_by="agt_1",
            )
        body = self._text(tenant_a, as_tenant)
        assert "imported" in body
        assert "did not witness" in body

    def test_an_empty_period_still_renders(self, tenant_a, as_tenant):
        """A nil return is a return. A regulator asking for a quiet month gets a
        document saying it was quiet, not an error."""
        with as_tenant(tenant_a):
            export = regulatory.build(tenant=tenant_a, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        body = text_of(export.files["report.pdf"])
        assert "No complaints were filed" in body


class TestAbridgement:
    def test_it_never_truncates_silently(self, monkeypatch, a_period_of_cases, as_tenant):
        """A supervisor reading an abridged document has no way to tell it was
        abridged unless it says so — with the count, and where the rest lives."""
        monkeypatch.setattr(pdf, "MAX_CASES_WITH_HISTORY", 1)

        tenant, _ = a_period_of_cases
        with as_tenant(tenant):
            export = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        body = text_of(export.files["report.pdf"])

        assert "abridged" in body
        assert "history.csv" in body
        assert "Nothing has been discarded" in body

    def test_a_small_period_carries_full_per_case_history(self, a_period_of_cases, as_tenant):
        tenant, _cases = a_period_of_cases
        with as_tenant(tenant):
            export = regulatory.build(tenant=tenant, period_from=PERIOD_FROM, period_to=PERIOD_TO)
        body = text_of(export.files["report.pdf"])

        assert "Per-case history" in body
        assert "dispute.created" in body
        assert "abridged" not in body


class TestThroughTheApi:
    def test_compliance_can_download_the_pdf(
        self, a_period_of_cases, client_for, make_agent, as_tenant
    ):
        tenant, _ = a_period_of_cases
        officer = make_agent(tenant, email="adaeze@example.com", role=Agent.Role.COMPLIANCE)

        response = client_for(tenant, agent=officer).get("/v1/reports/regulatory?format=pdf")

        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response["Content-Disposition"].endswith('.pdf"')
        assert response.content.startswith(b"%PDF-")
        assert "no-store" in response["Cache-Control"]

    def test_an_agent_still_cannot_export(self, a_period_of_cases, client_for, make_agent):
        """An export is a disclosure of every case in the period, in any format."""
        tenant, _ = a_period_of_cases
        agent = make_agent(tenant, email="ngozi@example.com", role=Agent.Role.AGENT)
        response = client_for(tenant, agent=agent).get("/v1/reports/regulatory?format=pdf")
        assert response.status_code == 404

    def test_an_unknown_format_says_so_rather_than_returning_a_zip(
        self, a_period_of_cases, client_for, make_agent
    ):
        """DRF reserves `format` for renderer selection; this endpoint documents it
        as its own. The regression that motivated the test is worse than a 400: a
        typo returned the whole period as a zip and nothing said otherwise."""
        tenant, _ = a_period_of_cases
        officer = make_agent(tenant, email="adaeze@example.com", role=Agent.Role.COMPLIANCE)

        response = client_for(tenant, agent=officer).get("/v1/reports/regulatory?format=pdff")

        assert response.status_code == 400
        assert "pdff" in response.json()["error"]["message"]

    def test_every_documented_format_is_reachable(self, a_period_of_cases, client_for, make_agent):
        tenant, _ = a_period_of_cases
        officer = make_agent(tenant, email="adaeze@example.com", role=Agent.Role.COMPLIANCE)
        client = client_for(tenant, agent=officer)

        for requested, content_type in (
            ("zip", "application/zip"),
            ("json", "application/json"),
            ("pdf", "application/pdf"),
            ("csv", "text/csv"),
        ):
            response = client.get(f"/v1/reports/regulatory?format={requested}")
            assert response.status_code == 200, requested
            assert response["Content-Type"].startswith(content_type), requested

    def test_the_zip_carries_the_pdf_too(self, a_period_of_cases, client_for, make_agent):
        tenant, _ = a_period_of_cases
        officer = make_agent(tenant, email="adaeze@example.com", role=Agent.Role.COMPLIANCE)

        response = client_for(tenant, agent=officer).get("/v1/reports/regulatory")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert "report.pdf" in archive.namelist()
            assert archive.read("report.pdf").startswith(b"%PDF-")
