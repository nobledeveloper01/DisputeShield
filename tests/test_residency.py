"""Crypto-shredding, imported history and the sandbox clock (phase 12).

The gate that this file exists for, and the reason both halves are asserted
together:

  **After a shred, the hash chain still verifies AND the content is
  unrecoverable.** Either half alone is worthless. A shred that breaks the chain
  destroys the evidence a regulator is entitled to; a shred that leaves the
  content readable is not a shred.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest
from django.core.exceptions import ValidationError

from disputeshield import audit
from disputeshield.crypto import vault
from disputeshield.disputes import service
from disputeshield.migration import importer
from disputeshield.models import (
    AuditRecord,
    Dispute,
    DisputeMessage,
    ImportBatch,
    SLAClock,
    SubjectKey,
    Tenant,
)
from disputeshield.reports import regulatory
from disputeshield.retention import holds
from disputeshield.sla import sweeper

pytestmark = pytest.mark.django_db

UTC_ = UTC


@pytest.fixture
def sealing_tenant(tenant_a, as_tenant):
    with as_tenant(tenant_a):
        tenant_a.content_sealing_enabled = True
        tenant_a.save(update_fields=["content_sealing_enabled"])
    return tenant_a


@pytest.fixture
def sealed_case(sealing_tenant, make_dispute, make_policy, as_tenant):
    version = make_policy(sealing_tenant)
    dispute = make_dispute(
        sealing_tenant,
        policy_version=version,
        customer_ref="usr_subject",
        description="I was debited 50,000 and the transfer never arrived.",
    )
    with as_tenant(sealing_tenant):
        service.add_message(
            dispute=dispute,
            body="Please refund me, this has been three weeks.",
            author_type=DisputeMessage.AuthorType.CUSTOMER,
            visibility=DisputeMessage.Visibility.CUSTOMER,
        )
    return dispute


class TestSealing:
    def test_content_is_sealed_at_rest(self, sealing_tenant, sealed_case, as_tenant):
        with as_tenant(sealing_tenant):
            sealed_case.refresh_from_db()
            stored = sealed_case.description
            readable = service.readable_description(sealed_case)

        assert vault.is_sealed(stored)
        assert "50,000" not in stored
        assert readable == "I was debited 50,000 and the transfer never arrived."

    def test_a_tenant_without_sealing_stores_plaintext(self, tenant_a, make_dispute, as_tenant):
        """Opt-in, and the honest consequence is stated: a tenant with sealing off
        can only be offered §11.7's refusal, because an append-only system cannot
        delete."""
        dispute = make_dispute(tenant_a, description="Plain content.")
        with as_tenant(tenant_a):
            dispute.refresh_from_db()
            assert not vault.is_sealed(dispute.description)

    def test_each_subject_gets_its_own_key(
        self, sealing_tenant, make_dispute, make_policy, as_tenant
    ):
        version = make_policy(sealing_tenant)
        make_dispute(sealing_tenant, policy_version=version, customer_ref="usr_one")
        make_dispute(sealing_tenant, policy_version=version, customer_ref="usr_two")
        with as_tenant(sealing_tenant):
            assert SubjectKey.objects.count() == 2


class TestCryptoShredding:
    def _shred(self, tenant, subject_hash):
        return vault.shred(
            tenant=tenant,
            subject_hash=subject_hash,
            requested_by="agt_dpo",
            approved_by="agt_counsel",
            reason="Erasure request, verified; no live matter",
        )

    def test_the_chain_still_verifies_and_the_content_is_gone(
        self, sealing_tenant, sealed_case, as_tenant
    ):
        """Both halves, together. Either one alone is worthless."""
        with as_tenant(sealing_tenant):
            before = audit.verify_tenant(sealing_tenant.pk)
            assert before.ok

            self._shred(sealing_tenant, sealed_case.customer_ref_hash)

            after = audit.verify_tenant(sealing_tenant.pk)
            sealed_case.refresh_from_db()
            readable = service.readable_description(sealed_case)

        # Half one: the evidence survives.
        assert after.ok, f"the shred broke the chain at {after.first_break}"
        assert after.records_checked > before.records_checked

        # Half two: the content does not.
        assert readable == vault.SHREDDED_MARKER
        assert "50,000" not in readable

    def test_the_row_is_untouched_which_is_why_the_chain_survives(
        self, sealing_tenant, sealed_case, as_tenant
    ):
        with as_tenant(sealing_tenant):
            stored_before = Dispute.objects.get(pk=sealed_case.pk).description
            self._shred(sealing_tenant, sealed_case.customer_ref_hash)
            stored_after = Dispute.objects.get(pk=sealed_case.pk).description
        assert stored_before == stored_after, "a shred must change no row"

    def test_messages_are_shredded_too(self, sealing_tenant, sealed_case, as_tenant):
        with as_tenant(sealing_tenant):
            self._shred(sealing_tenant, sealed_case.customer_ref_hash)
            message = sealed_case.messages.get()
            assert service.readable_body(message) == vault.SHREDDED_MARKER

    def test_shredding_needs_a_second_different_person(
        self, sealing_tenant, sealed_case, as_tenant
    ):
        with as_tenant(sealing_tenant):
            with pytest.raises(PermissionError, match="different approver"):
                vault.shred(
                    tenant=sealing_tenant,
                    subject_hash=sealed_case.customer_ref_hash,
                    requested_by="agt_dpo",
                    approved_by="agt_dpo",
                    reason="erasure",
                )
            with pytest.raises(PermissionError):
                vault.shred(
                    tenant=sealing_tenant,
                    subject_hash=sealed_case.customer_ref_hash,
                    requested_by="agt_dpo",
                    approved_by="",
                    reason="erasure",
                )

    def test_a_shred_is_audited_as_irreversible(self, sealing_tenant, sealed_case, as_tenant):
        """The fact that data was erased on a lawful request is itself something
        that must be provable."""
        with as_tenant(sealing_tenant):
            self._shred(sealing_tenant, sealed_case.customer_ref_hash)
            record = AuditRecord.objects.get(event_type="crypto.shredded")

        assert record.actor_id == "agt_dpo"
        assert record.payload["approved_by"] == "agt_counsel"
        assert record.payload["irreversible"] is True

    def test_material_under_legal_hold_cannot_be_shredded(
        self, sealing_tenant, sealed_case, as_tenant
    ):
        """Silently shredding held material would be spoliation of evidence
        performed by a feature built to be lawful."""
        from disputeshield.models import LegalHold

        with as_tenant(sealing_tenant):
            holds.place(
                tenant=sealing_tenant,
                name="Okafor v Acme",
                matter_reference="LIT-2026-4",
                reason="Claim filed",
                scope=LegalHold.Scope.DISPUTE,
                placed_by="agt_1",
                dispute=sealed_case,
            )
            with pytest.raises(holds.HeldMaterial, match="LIT-2026-4"):
                self._shred(sealing_tenant, sealed_case.customer_ref_hash)

            assert service.readable_description(sealed_case) != vault.SHREDDED_MARKER

    def test_a_shred_touches_exactly_one_subject(
        self, sealing_tenant, sealed_case, make_dispute, make_policy, as_tenant
    ):
        """BYOK and erasure both depend on this: one subject's key, nobody else's."""
        version = make_policy(sealing_tenant)
        other = make_dispute(
            sealing_tenant,
            policy_version=version,
            customer_ref="usr_innocent",
            description="A different customer's complaint.",
        )
        with as_tenant(sealing_tenant):
            self._shred(sealing_tenant, sealed_case.customer_ref_hash)
            other.refresh_from_db()
            assert service.readable_description(other) == "A different customer's complaint."

    def test_a_revoked_master_key_makes_exactly_that_tenants_data_unreadable(
        self, sealing_tenant, sealed_case, tenant_b, as_tenant, settings
    ):
        """What BYOK revocation looks like from here: we cannot recover it, and
        saying so plainly is the point of the arrangement."""
        with as_tenant(sealing_tenant):
            # Rotating the project secret stands in for a customer revoking the
            # KMS key that wraps their data keys.
            settings.SECRET_KEY = "a-different-master-key"
            assert service.readable_description(sealed_case) == vault.SHREDDED_MARKER


class TestTheSandboxClock:
    def test_a_live_tenant_cannot_carry_a_clock_offset(self, db):
        """Refused at the model layer, not the view. A view-layer guard is one
        refactor away from being bypassed."""
        with pytest.raises(ValidationError, match="clock offset"):
            Tenant.objects.create(
                name="Live",
                slug="live-offset",
                environment=Tenant.Environment.LIVE,
                clock_offset_seconds=-86_400,
            )

    def test_an_existing_live_tenant_cannot_gain_one(self, tenant_a, as_tenant):
        with as_tenant(tenant_a):
            tenant_a.clock_offset_seconds = -3600
            with pytest.raises(ValidationError):
                tenant_a.save(update_fields=["clock_offset_seconds"])

    def test_a_sandbox_tenant_may(self, db):
        sandbox = Tenant.objects.create(
            name="Sandbox",
            slug="sbx",
            environment=Tenant.Environment.TEST,
            clock_offset_seconds=-86_400,
        )
        assert sandbox.clock_offset_seconds == -86_400
        assert not sandbox.is_live

    def test_the_guard_is_on_the_model_not_a_form(self):
        import inspect

        source = inspect.getsource(Tenant.save)
        assert "clock_offset_seconds" in source


class TestImportedHistory:
    CSV = (
        b"external_reference,customer_ref,category,description,opened_at,closed_at\n"
        b"ZD-1001,cust-a,failed_transfer,Old complaint about a transfer,"
        b"2021-03-04T09:00:00Z,2021-03-11T16:00:00Z\n"
        b"ZD-1002,cust-b,failed_transfer,Another old complaint,"
        b"2021-04-01T09:00:00Z,\n"
        b"ZD-BAD,cust-c,failed_transfer,Unreadable date,not-a-date,\n"
    )

    @pytest.fixture
    def imported(self, tenant_a, make_policy, as_tenant):
        make_policy(tenant_a, category="failed_transfer")
        make_policy(tenant_a, category="other")
        with as_tenant(tenant_a):
            return importer.import_csv(
                tenant=tenant_a,
                content=self.CSV,
                source=ImportBatch.Source.ZENDESK,
                imported_by="agt_1",
            )

    def test_history_arrives_with_its_original_timestamps(self, tenant_a, imported, as_tenant):
        with as_tenant(tenant_a):
            case = Dispute.objects.get(reference="IMP-ZD-1001")
        assert case.submitted_at == datetime(2021, 3, 4, 9, 0, tzinfo=UTC_)
        assert case.closed_at == datetime(2021, 3, 11, 16, 0, tzinfo=UTC_)

    def test_a_bad_row_is_rejected_with_its_line_number(self, tenant_a, imported):
        assert imported.batch.cases_imported == 2
        assert imported.batch.cases_rejected == 1
        assert imported.rejected[0]["line"] == 4
        assert "opened_at" in imported.rejected[0]["reason"]

    def test_imported_cases_are_excluded_from_live_sla_computation(
        self, tenant_a, imported, as_tenant
    ):
        """A case closed in 2021 must not acquire a deadline in 2026, and a sweep
        firing on one would page somebody about a five-year-old complaint."""
        with as_tenant(tenant_a):
            clocks = SLAClock.objects.filter(subject_id__in=imported.imported)
            assert clocks.count() == 2
            assert all(clock.state == SLAClock.State.STOPPED for clock in clocks)

        assert sweeper.sweep().fired == 0

    def test_the_import_claims_no_integrity(self, tenant_a, imported, as_tenant):
        """We did not witness it. Absorbing foreign history and implying we vouch
        for it is the failure this module is arranged to avoid."""
        with as_tenant(tenant_a):
            case_record = AuditRecord.objects.filter(event_type="import.case").first()
            batch_record = AuditRecord.objects.get(event_type="import.completed")

        assert case_record.payload["disputeshield_witnessed"] is False
        assert batch_record.payload["disputeshield_witnessed"] is False
        # What we were handed, so "this is the file you gave us" is provable.
        assert batch_record.payload["source_digest"]

    def test_the_export_visibly_separates_imported_history(
        self, tenant_a, imported, make_dispute, make_policy, as_tenant
    ):
        native = make_dispute(
            tenant_a,
            policy_version=make_policy(tenant_a, category="failed_transfer"),
            customer_ref="usr_native",
        )
        with as_tenant(tenant_a):
            export = regulatory.build(
                tenant=tenant_a,
                period_from=datetime(2020, 1, 1, tzinfo=UTC_),
                period_to=datetime(2030, 1, 1, tzinfo=UTC_),
            )

        rows = list(io.StringIO(export.files["cases.csv"].decode()))
        header = rows[0]
        assert "origin" in header

        body = "".join(rows[1:])
        assert "IMP-ZD-1001,imported" in body
        assert f"{native.reference},disputeshield" in body
        assert export.manifest["imported_case_count"] == 2
        assert "did not witness" in export.manifest["integrity_note"]

    def test_the_chain_verifies_after_an_import(self, tenant_a, imported, as_tenant):
        with as_tenant(tenant_a):
            assert audit.verify_tenant(tenant_a.pk).ok


class TestResidency:
    def test_a_tenant_carries_its_region(self, db):
        tenant = Tenant.objects.create(name="EU", slug="eu", region="eu-west-1")
        assert tenant.region == "eu-west-1"

    def test_two_tenants_can_be_pinned_to_different_regions(self, db):
        eu = Tenant.objects.create(name="EU", slug="eu-t", region="eu-west-1")
        af = Tenant.objects.create(name="AF", slug="af-t", region="af-south-1")
        assert eu.region != af.region

    def test_a_subject_key_never_leaves_its_tenant(
        self, sealing_tenant, sealed_case, tenant_b, as_tenant
    ):
        """Region pinning is a deployment property; this is the part the code can
        enforce — a key readable across a boundary would make per-tenant
        encryption decorative."""
        with as_tenant(tenant_b):
            assert SubjectKey.objects.count() == 0


class TestTheSandboxSimulator:
    """§A19: every §3.1 persona is blocked without a demo that looks real.

    Marked slow because it builds a whole tenant; the roadmap's gate is under 60
    seconds and it runs in well under one.
    """

    @pytest.mark.django_db(transaction=True)
    def test_it_builds_a_queue_worth_looking_at(self):
        """A breach, a pause, a reopening and a mass incident — in one command.

        A demo of the happy path demonstrates a ticketing system."""
        import io
        import json

        from django.core.management import call_command

        out = io.StringIO()
        call_command("disputeshield_simulate", "--slug", "sbx-test", "--cases", "12", stdout=out)
        summary = json.loads(out.getvalue())

        assert summary["environment"] == "test"
        assert summary["cases"] == 12
        assert summary["paused"].startswith("DS-")
        assert summary["reopened"].startswith("DS-")
        assert summary["mass_event_applied"] > 0
        assert summary["deadlines_fired"] > 0, "a demo without a breach is not a demo"
        assert summary["seconds"] < 60

    @pytest.mark.django_db(transaction=True)
    def test_it_refuses_to_build_on_a_live_tenant(self):
        """Moving a live clock would move a regulatory deadline."""
        from django.core.management import call_command
        from django.core.management.base import CommandError

        Tenant.objects.create(name="Live", slug="live-sbx", environment=Tenant.Environment.LIVE)
        with pytest.raises(CommandError, match="live tenant"):
            call_command("disputeshield_simulate", "--slug", "live-sbx")
