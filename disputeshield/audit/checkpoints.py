"""Signed checkpoints over a tenant's audit chain (§8.3).

`GET /v1/audit/verify` exists so a customer or their auditor can check our
integrity claim independently rather than taking our word for it. A proof only we
can run is a promise, not a proof — so this module produces something small,
signed and portable that an auditor can keep and re-check later against a chain
they walk themselves.

Two facts are reported separately and must stay separate:

  * **Chain status** — the records are internally consistent.
  * **Attestation status** — we signed a statement saying we computed that.

They answer different questions. The chain says nothing was altered relative to
its neighbours; it cannot say *when* the chain existed, because an adversary with
full control could rebuild a consistent chain after the fact. Closing that gap
needs an external timestamp authority, which is amplifier A8 in phase 8. Until
then the honest thing is to publish both facts and let nobody confuse them.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import json

from django.conf import settings

from disputeshield.audit.verify import verify_tenant
from disputeshield.models import AuditCheckpoint, AuditRecord


@dataclasses.dataclass(frozen=True)
class CheckpointResult:
    checkpoint: AuditCheckpoint | None
    verified: bool
    records_checked: int
    first_break: int | None


def sign_payload(payload: dict) -> str:
    """HMAC over the canonical form, so an auditor can recompute it from the
    published fields alone rather than from our serialisation choices."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hmac.new(settings.SECRET_KEY.encode(), canonical, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def checkpoint_payload(
    *,
    tenant_id: str,
    sequence_from: int,
    sequence_to: int,
    record_count: int,
    head_hash: str,
    verified: bool,
) -> dict:
    return {
        "tenant_id": tenant_id,
        "sequence_from": sequence_from,
        "sequence_to": sequence_to,
        "record_count": record_count,
        "head_hash": head_hash,
        "verified": verified,
    }


def create_checkpoint(tenant) -> CheckpointResult:
    """Walk the tenant's chain and record what we found. Never repairs anything.

    A failed verification produces a checkpoint marked `verified=False` rather
    than no checkpoint at all. Silence after a failed check is indistinguishable
    from the job not having run, and §11.4 pages on exactly this condition.
    """
    result = verify_tenant(tenant.pk)

    head = (
        AuditRecord.objects.all_tenants()
        .filter(tenant_id=tenant.pk)
        .order_by("-sequence")
        .values("sequence", "hash")
        .first()
    )
    if head is None:
        return CheckpointResult(None, True, 0, None)

    previous = AuditCheckpoint.objects.order_by("-sequence_to").first()
    sequence_from = (previous.sequence_to + 1) if previous else 1

    if previous and previous.sequence_to >= head["sequence"]:
        # Nothing new since the last checkpoint. Re-signing the same range would
        # produce a second attestation for one state, which an auditor comparing
        # two checkpoints would reasonably read as a change.
        return CheckpointResult(previous, result.ok, result.records_checked, result.first_break)

    payload = checkpoint_payload(
        tenant_id=tenant.pk,
        sequence_from=sequence_from,
        sequence_to=head["sequence"],
        record_count=result.records_checked,
        head_hash=head["hash"],
        verified=result.ok,
    )
    checkpoint = AuditCheckpoint.objects.create(
        tenant=tenant,
        sequence_from=sequence_from,
        sequence_to=head["sequence"],
        record_count=result.records_checked,
        head_hash=head["hash"],
        verified=result.ok,
        failure_detail="" if result.ok else "; ".join(f.reason for f in result.failures[:5]),
        signature=sign_payload(payload),
    )
    return CheckpointResult(checkpoint, result.ok, result.records_checked, result.first_break)


def verify_signature(checkpoint: AuditCheckpoint) -> bool:
    payload = checkpoint_payload(
        tenant_id=checkpoint.tenant_id,
        sequence_from=checkpoint.sequence_from,
        sequence_to=checkpoint.sequence_to,
        record_count=checkpoint.record_count,
        head_hash=checkpoint.head_hash,
        verified=checkpoint.verified,
    )
    return hmac.compare_digest(sign_payload(payload), checkpoint.signature)


def attestation(tenant) -> dict:
    """What `GET /v1/audit/verify` and the regulatory export both publish."""
    result = verify_tenant(tenant.pk)
    latest = AuditCheckpoint.objects.order_by("-sequence_to").first()

    return {
        "chain": {
            "verified": result.ok,
            "records_checked": result.records_checked,
            "first_break": result.first_break,
            "failures": [
                {"sequence": f.sequence, "reason": f.reason} for f in result.failures[:10]
            ],
        },
        "attestation": {
            "present": latest is not None,
            "signature_valid": verify_signature(latest) if latest else None,
            "sequence_to": latest.sequence_to if latest else None,
            "head_hash": latest.head_hash if latest else None,
            "computed_at": latest.computed_at.isoformat() if latest else None,
            # Stated plainly so nobody reads the weaker claim as the stronger one.
            "externally_anchored": False,
            "note": (
                "The chain proves internal consistency. The signature proves we computed "
                "it. Neither proves when the chain existed — external anchoring to a "
                "timestamp authority is planned (amplifier A8)."
            ),
        },
    }
