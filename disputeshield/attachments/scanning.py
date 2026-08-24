"""Antivirus scanning, and what happens while it has not finished.

The scanner is an interface with a pluggable backend because the realistic
deployments differ: a hosted install talks to a scanning service, a self-hosted
one runs ClamAV in a sidecar, and an evaluation install has neither. What must
not differ is the behaviour before a verdict arrives — the file is not
retrievable, by anyone, including its uploader.

The default backend refuses to pretend. `NullScanner` marks files `failed`, not
`clean`, so an installation that never configured a scanner has invisible
attachments rather than unscanned ones being served to agents.
"""

from __future__ import annotations

import dataclasses
import importlib
import re

from django.utils import timezone

from disputeshield.models import DisputeAttachment


@dataclasses.dataclass(frozen=True)
class Verdict:
    status: str
    detail: str = ""


class Scanner:
    def scan(self, content: bytes) -> Verdict:  # pragma: no cover - interface
        raise NotImplementedError


class NullScanner(Scanner):
    """No scanner configured. Fails closed, loudly, forever."""

    def scan(self, content: bytes) -> Verdict:
        return Verdict(
            DisputeAttachment.ScanStatus.FAILED,
            "No antivirus backend is configured. Set DISPUTESHIELD['AV_SCANNER'].",
        )


class EicarScanner(Scanner):
    """Detects the EICAR test string and nothing else.

    For development and for CI, where running a real engine is neither possible
    nor useful. It is a real scanner in exactly one respect that matters to the
    tests: it returns `infected` for something, so the infected path is exercised
    rather than assumed.
    """

    EICAR = re.compile(rb"EICAR-STANDARD-ANTIVIRUS-TEST-FILE")

    def scan(self, content: bytes) -> Verdict:
        if self.EICAR.search(content):
            return Verdict(DisputeAttachment.ScanStatus.INFECTED, "EICAR test signature")
        return Verdict(DisputeAttachment.ScanStatus.CLEAN)


def get_scanner() -> Scanner:
    from disputeshield import conf

    path = conf.get("AV_SCANNER")
    if not path:
        return NullScanner()
    module_name, _, class_name = path.rpartition(".")
    return getattr(importlib.import_module(module_name), class_name)()


def scan_attachment(attachment_id: str) -> str:
    """Scan one attachment and record the verdict. Idempotent.

    Re-scanning something already judged is allowed and is a no-op on the record:
    a scanner upgrade should be able to re-run over history without rewriting
    what was known at the time.
    """
    from disputeshield import audit
    from disputeshield.attachments import storage
    from disputeshield.tenancy.middleware import db_tenant_context

    attachment = DisputeAttachment.objects.all_tenants().get(pk=attachment_id)

    from django.db import transaction

    with transaction.atomic(), db_tenant_context(attachment.tenant_id):
        content = storage.get(attachment.storage_key)
        verdict = get_scanner().scan(content)

        attachment.scan_status = verdict.status
        attachment.scan_detail = verdict.detail
        attachment.scanned_at = timezone.now()
        attachment.save(update_fields=["scan_status", "scan_detail", "scanned_at"])

        audit.append(
            tenant=attachment.tenant,
            event_type=f"attachment.scan_{verdict.status}",
            subject_type="dispute",
            subject_id=attachment.dispute_id,
            actor_type="system",
            payload={
                "attachment_id": attachment.pk,
                "sha256": attachment.sha256,
                "detail": verdict.detail,
            },
        )
        return verdict.status
