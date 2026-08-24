"""Storing, scanning and serving attachments.

The exit gate this file exists for: **nothing is retrievable until it is clean**,
by anyone, including whoever uploaded it. An uploader who can fetch their own
file back before it is scanned has a working file-hosting endpoint on a fintech's
domain, and the malware never needs to reach an agent to be useful.
"""

from __future__ import annotations

import time
import uuid

import pytest

from disputeshield.api.views_attachments import download_url
from disputeshield.attachments import service, storage
from disputeshield.attachments.inspection import RejectedUpload
from disputeshield.models import AuditRecord, DisputeAttachment

pytestmark = pytest.mark.django_db

PNG = b"\x89PNG\r\n\x1a\n" + b"receipt bytes" * 8
EICAR_PNG = (
    b"\x89PNG\r\n\x1a\n" + b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


def idem() -> dict:
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


@pytest.fixture
def attach(as_tenant):
    def _make(dispute, content=PNG, filename="receipt.png", by="agent", scan_now=True):
        with as_tenant(dispute.tenant):
            return service.upload(
                dispute=dispute,
                content=content,
                filename=filename,
                uploaded_by_type=by,
                uploaded_by_id="agt_1" if by == "agent" else "",
                scan_now=scan_now,
            )

    return _make


class TestUploading:
    def test_an_accepted_file_is_stored_and_hashed(self, tenant_a, make_dispute, attach):
        attachment = attach(make_dispute(tenant_a))
        assert attachment.sha256 == storage.digest(PNG)
        assert attachment.size_bytes == len(PNG)
        assert attachment.content_type == "image/png"

    def test_a_rejected_file_never_reaches_storage(self, tenant_a, make_dispute, as_tenant):
        """Inspection happens before anything is written, so there is no window
        in which a refused upload sits on disk awaiting a cleanup job."""
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a), pytest.raises(RejectedUpload):
            service.upload(
                dispute=dispute,
                content=b"\x7fELF not a picture",
                filename="receipt.png",
                uploaded_by_type="agent",
                uploaded_by_id="agt_1",
            )
        with as_tenant(tenant_a):
            assert DisputeAttachment.objects.count() == 0

    def test_the_storage_key_contains_nothing_the_uploader_supplied(
        self, tenant_a, make_dispute, attach
    ):
        """A filename-derived path is both guessable and a traversal surface."""
        attachment = attach(make_dispute(tenant_a), filename="../../etc/passwd.png")
        assert "etc" not in attachment.storage_key
        assert ".." not in attachment.storage_key
        assert attachment.pk in attachment.storage_key

    def test_a_traversal_filename_is_stripped_for_display_too(self, tenant_a, make_dispute, attach):
        attachment = attach(make_dispute(tenant_a), filename="../../etc/passwd.png")
        assert "/" not in attachment.filename
        assert ".." not in attachment.filename

    def test_uploading_writes_an_audit_record(self, tenant_a, make_dispute, attach, as_tenant):
        attachment = attach(make_dispute(tenant_a))
        with as_tenant(tenant_a):
            record = AuditRecord.objects.get(event_type="attachment.uploaded")
        assert record.payload["sha256"] == attachment.sha256

    def test_an_attachment_cannot_be_deleted(self, tenant_a, make_dispute, attach):
        attachment = attach(make_dispute(tenant_a))
        with pytest.raises(PermissionError, match="evidence"):
            attachment.delete()


class TestTheScanGate:
    def test_a_pending_attachment_is_not_retrievable(self, tenant_a, make_dispute, attach):
        attachment = attach(make_dispute(tenant_a), scan_now=False)
        assert attachment.scan_status == DisputeAttachment.ScanStatus.PENDING
        with pytest.raises(service.NotRetrievable):
            service.retrieve(attachment)

    def test_an_infected_attachment_is_not_retrievable(self, tenant_a, make_dispute, attach):
        attachment = attach(make_dispute(tenant_a), content=EICAR_PNG)
        assert attachment.scan_status == DisputeAttachment.ScanStatus.INFECTED
        with pytest.raises(service.NotRetrievable):
            service.retrieve(attachment)

    def test_a_clean_attachment_is_retrievable(self, tenant_a, make_dispute, attach):
        attachment = attach(make_dispute(tenant_a))
        assert attachment.scan_status == DisputeAttachment.ScanStatus.CLEAN
        assert service.retrieve(attachment) == PNG

    def test_an_unconfigured_scanner_fails_closed_rather_than_open(
        self, tenant_a, make_dispute, as_tenant, settings
    ):
        """An installation that never configured a scanner gets invisible
        attachments, not unscanned ones served to agents."""
        settings.DISPUTESHIELD = {**settings.DISPUTESHIELD, "AV_SCANNER": None}
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            attachment = service.upload(
                dispute=dispute,
                content=PNG,
                filename="r.png",
                uploaded_by_type="agent",
                uploaded_by_id="agt_1",
                scan_now=True,
            )
        assert attachment.scan_status == DisputeAttachment.ScanStatus.FAILED
        assert "No antivirus backend" in attachment.scan_detail
        with pytest.raises(service.NotRetrievable):
            service.retrieve(attachment)

    def test_the_scan_verdict_is_audited(self, tenant_a, make_dispute, attach, as_tenant):
        attach(make_dispute(tenant_a), content=EICAR_PNG)
        with as_tenant(tenant_a):
            assert AuditRecord.objects.filter(event_type="attachment.scan_infected").exists()


class TestSignedDownload:
    def test_a_valid_signature_serves_the_file(self, tenant_a, make_dispute, attach, client):
        attachment = attach(make_dispute(tenant_a))
        response = client.get(download_url(attachment))
        assert response.status_code == 200
        assert response.content == PNG

    def test_it_is_never_served_as_its_own_content_type(
        self, tenant_a, make_dispute, attach, client
    ):
        """§10: never executed or rendered inline. A fixed octet-stream plus
        nosniff plus a deny-everything CSP is what makes that true even when the
        file is served from the same host in development."""
        attachment = attach(make_dispute(tenant_a))
        response = client.get(download_url(attachment))

        assert response["Content-Type"] == "application/octet-stream"
        assert response["Content-Disposition"].startswith("attachment;")
        assert response["X-Content-Type-Options"] == "nosniff"
        assert "default-src 'none'" in response["Content-Security-Policy"]
        assert "sandbox" in response["Content-Security-Policy"]

    def test_an_unsigned_url_is_404(self, tenant_a, make_dispute, attach, client):
        attachment = attach(make_dispute(tenant_a))
        assert client.get(f"/v1/attachments/{attachment.pk}").status_code == 404

    def test_a_tampered_signature_is_404(self, tenant_a, make_dispute, attach, client):
        attachment = attach(make_dispute(tenant_a))
        url = download_url(attachment).replace("signature=", "signature=x")
        assert client.get(url).status_code == 404

    def test_a_signature_cannot_be_replayed_against_another_tenant(
        self, tenant_a, tenant_b, make_dispute, attach, client
    ):
        """The tenant travels in the URL, so it must be covered by the signature."""
        attachment = attach(make_dispute(tenant_a))
        expires, signature = storage.sign(attachment.pk, attachment.tenant_id)
        url = (
            f"/v1/attachments/{attachment.pk}"
            f"?tenant={tenant_b.pk}&expires={expires}&signature={signature}"
        )
        assert client.get(url).status_code == 404

    def test_a_signature_for_another_attachment_does_not_transfer(
        self, tenant_a, make_dispute, attach, client
    ):
        dispute = make_dispute(tenant_a)
        mine = attach(dispute)
        theirs = attach(dispute, content=PNG + b"other")

        expires, signature = storage.sign(mine.pk, mine.tenant_id)
        url = (
            f"/v1/attachments/{theirs.pk}"
            f"?tenant={theirs.tenant_id}&expires={expires}&signature={signature}"
        )
        assert client.get(url).status_code == 404

    def test_an_expired_link_stops_working(self, tenant_a, make_dispute, attach, client):
        """A URL that leaks into a chat log or a support ticket goes stale."""
        attachment = attach(make_dispute(tenant_a))
        past = int(time.time()) - 10
        _, signature = storage.sign(attachment.pk, attachment.tenant_id, ttl_seconds=-10, now=past)
        url = (
            f"/v1/attachments/{attachment.pk}"
            f"?tenant={attachment.tenant_id}&expires={past}&signature={signature}"
        )
        assert client.get(url).status_code == 404

    def test_a_pending_file_is_indistinguishable_from_a_missing_one(
        self, tenant_a, make_dispute, attach, client
    ):
        """Saying "not scanned yet" tells an uploader exactly when to retry."""
        attachment = attach(make_dispute(tenant_a), scan_now=False)
        missing = client.get("/v1/attachments/att_DOESNOTEXIST?tenant=x&expires=0&signature=x")
        pending = client.get(download_url(attachment))

        assert pending.status_code == missing.status_code == 404
        assert pending.content == missing.content == b""


class TestThroughTheApi:
    def test_an_agent_can_upload_and_list_clean_attachments(
        self, tenant_a, make_dispute, client_for, as_tenant
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        dispute = make_dispute(tenant_a)
        client = client_for(tenant_a)

        response = client.post(
            f"/v1/disputes/{dispute.pk}/attachments/",
            {"file": SimpleUploadedFile("receipt.png", PNG, content_type="image/png")},
            format="multipart",
            **idem(),
        )
        assert response.status_code == 201

        with as_tenant(tenant_a):
            from disputeshield.attachments.scanning import scan_attachment

            scan_attachment(response.json()["id"])

        listed = client.get(f"/v1/disputes/{dispute.pk}/attachments/").json()
        assert [a["id"] for a in listed] == [response.json()["id"]]

    def test_the_listing_never_includes_an_unscanned_file(
        self, tenant_a, make_dispute, client_for, attach
    ):
        dispute = make_dispute(tenant_a)
        attach(dispute, scan_now=False)
        assert client_for(tenant_a).get(f"/v1/disputes/{dispute.pk}/attachments/").json() == []

    def test_a_rejected_upload_explains_itself_without_leaking_internals(
        self, tenant_a, make_dispute, client_for
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        dispute = make_dispute(tenant_a)
        response = client_for(tenant_a).post(
            f"/v1/disputes/{dispute.pk}/attachments/",
            {"file": SimpleUploadedFile("x.png", b"\x7fELF nope", content_type="image/png")},
            format="multipart",
            **idem(),
        )
        assert response.status_code == 400
        assert response.json()["error"]["reason"] == "unsupported_type"

    def test_a_customer_sees_only_their_own_uploads_metadata(
        self, tenant_a, widget_client, make_dispute, make_policy, attach, as_tenant
    ):
        """A scan verdict naming a signature tells an uploader which malware got
        through and which did not."""
        from disputeshield.attachments.scanning import scan_attachment

        version = make_policy(tenant_a)
        dispute = make_dispute(tenant_a, policy_version=version, customer_ref="usr_9931")
        attachment = attach(dispute, by="customer", scan_now=False)
        with as_tenant(tenant_a):
            scan_attachment(attachment.pk)

        client, _ = widget_client(tenant_a, customer_ref="usr_9931")
        body = client.get(f"/v1/widget/disputes/{dispute.pk}/attachments/").json()

        assert len(body) == 1
        assert set(body[0]) == {"id", "filename", "size_bytes", "created_at"}
        assert "scan_status" not in body[0]
        assert "sha256" not in body[0]
