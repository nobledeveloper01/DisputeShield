from __future__ import annotations

from celery import shared_task


@shared_task(name="disputeshield.attachments.scan")
def scan(attachment_id: str) -> str:
    from disputeshield.attachments.scanning import scan_attachment

    return scan_attachment(attachment_id)
