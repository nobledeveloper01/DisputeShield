"""Uploading and retrieving attachments. The only supported path for either."""

from __future__ import annotations

from django.db import transaction

from disputeshield import audit
from disputeshield.attachments import storage
from disputeshield.attachments.inspection import RejectedUpload, inspect
from disputeshield.models import Dispute, DisputeAttachment


class NotRetrievable(PermissionError):
    """Refused because the file has not been cleared, not because it is missing."""


def upload(
    *,
    dispute: Dispute,
    content: bytes,
    filename: str,
    uploaded_by_type: str,
    uploaded_by_id: str = "",
    scan_now: bool = False,
) -> DisputeAttachment:
    """Inspect, store, record, and queue a scan — in that order.

    Inspection happens before anything is written, so a refused file never
    reaches storage at all. There is no window in which a rejected upload exists
    on disk waiting for a cleanup job that might not run.
    """
    inspection = inspect(content, declared_name=filename)
    sha256 = storage.digest(content)

    with transaction.atomic():
        attachment = DisputeAttachment.objects.create(
            tenant=dispute.tenant,
            dispute=dispute,
            uploaded_by_type=uploaded_by_type,
            uploaded_by_id=uploaded_by_id,
            filename=_safe_display_name(filename, inspection.extension),
            content_type=inspection.content_type,
            size_bytes=inspection.size_bytes,
            sha256=sha256,
            storage_key=storage.storage_key(dispute.tenant_id, "", sha256),
        )
        attachment.storage_key = storage.storage_key(dispute.tenant_id, attachment.pk, sha256)
        attachment.save(update_fields=["storage_key"])

        storage.put(attachment.storage_key, content)

        audit.append(
            tenant=dispute.tenant,
            event_type="attachment.uploaded",
            subject_type="dispute",
            subject_id=dispute.pk,
            actor_type="user" if uploaded_by_type == "agent" else uploaded_by_type,
            actor_id=uploaded_by_id or dispute.customer_ref_hash,
            payload={
                "attachment_id": attachment.pk,
                "sha256": sha256,
                "content_type": inspection.content_type,
                "size_bytes": inspection.size_bytes,
            },
        )

    if scan_now:
        from disputeshield.attachments.scanning import scan_attachment

        scan_attachment(attachment.pk)
        attachment.refresh_from_db()
    else:
        from disputeshield.attachments.tasks import scan

        transaction.on_commit(lambda: scan.delay(attachment.pk))

    return attachment


def retrieve(attachment: DisputeAttachment) -> bytes:
    """The single gate. Every retrieval path goes through here.

    `is_retrievable` is checked once, in one place, rather than in each view that
    happens to serve a file — a check repeated per caller is a check one caller
    eventually omits.
    """
    if not attachment.is_retrievable:
        raise NotRetrievable(f"Attachment {attachment.pk} is {attachment.scan_status}, not clean.")
    return storage.get(attachment.storage_key)


def _safe_display_name(filename: str, extension: str) -> str:
    """A display name, stripped of anything that makes it a path or a payload.

    Never used to build a storage key or a Content-Disposition filename directive
    without quoting, but sanitising at the boundary means the value in the
    database is already safe for the next person who uses it somewhere else.
    """
    import re

    stem = re.sub(r"[^A-Za-z0-9._ -]", "", (filename or "attachment").rsplit("/", 1)[-1])
    stem = stem.replace("..", ".").strip() or "attachment"
    if not stem.lower().endswith(f".{extension}"):
        stem = f"{stem}.{extension}"
    return stem[:255]


__all__ = ["NotRetrievable", "RejectedUpload", "retrieve", "upload"]
