"""Emailing a regulatory export to a registered recipient.

A supervisor asks for a period and expects it in their inbox; producing it by
hand from a download is the manual step §6.5 exists to remove. But this is the
one feature in the product whose whole purpose is to move **every case in a
period** outside the system, so almost all of the file is about what it refuses
to do.

Three properties, each with the failure it prevents:

  * **The destination is chosen before the send, not during it.** Recipients come
    from `ReportRecipient`, never from the request body. An endpoint that emails
    a period's disclosure to an address supplied by the caller is an exfiltration
    route with an OpenAPI entry.
  * **Nothing is attached to the outbox row.** The payload carries the period and
    the digests the export had when it was requested. The dispatcher rebuilds the
    bundle and sends it only if the digests still match — which is a real use for
    the byte-reproducibility guarantee rather than a claim about it. It also
    means case content never sits in a queue table waiting to be sent.
  * **A mismatch refuses rather than sends.** If the rebuild does not match, the
    period's data changed between request and delivery. The honest response is to
    fail loudly: a supervisor who receives a bundle whose digests disagree with
    the ones they were promised has been handed a reason to doubt all of it.

The email body deliberately restates the manifest signature and each file's
digest, so a recipient can verify the attachment against the API without
trusting the message that carried it.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
from datetime import datetime

from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from disputeshield.models import NotificationOutbox, ReportRecipient

logger = logging.getLogger(__name__)

EVENT_TYPE = "report.regulatory"
CHANNEL = "report_email"

# Refused rather than truncated. A provider that bounces a 30 MB attachment turns
# a compliance deadline into a support ticket discovered a week later, and an
# export silently trimmed to fit is worse than one that never arrived.
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024


class UnknownRecipient(Exception):
    """An address that is not on the tenant's allowlist.

    Names the addresses it rejected. A caller who mistyped a domain needs to see
    which one, and a caller who is probing needs the refusal to be the same
    whether or not the address exists elsewhere.
    """


class ReportTooLarge(Exception):
    pass


class BundleChanged(Exception):
    """The rebuild did not match the digests promised at request time."""


@dataclasses.dataclass(frozen=True)
class Queued:
    notification_id: str
    idempotency_key: str
    recipients: tuple[str, ...]
    files: dict[str, str]


def resolve_recipients(addresses: list[str]) -> list[ReportRecipient]:
    """Allowlist lookup. Refuses the whole request if any address is unknown.

    The whole request, not the known subset: a partial send is a supervisor
    waiting for a report that four of five people received, and nobody noticing
    for a week.
    """
    wanted = [a.strip().lower() for a in addresses if a and a.strip()]
    if not wanted:
        raise UnknownRecipient("A report needs at least one recipient.")

    registered = {
        recipient.address.lower(): recipient
        for recipient in ReportRecipient.objects.filter(is_active=True)
    }
    unknown = sorted(set(wanted) - set(registered))
    if unknown:
        raise UnknownRecipient(
            f"Not on this tenant's report allowlist: {', '.join(unknown)}. "
            "Register the address first — a regulatory export discloses every "
            "case in the period, so where it may be sent is decided in advance."
        )
    # Deduplicated but order-preserving, so the audit record reads the way the
    # request did.
    seen: set[str] = set()
    resolved: list[ReportRecipient] = []
    for address in wanted:
        if address in seen:
            continue
        seen.add(address)
        resolved.append(registered[address])
    return resolved


def request_delivery(
    *,
    tenant,
    period_from: datetime,
    period_to: datetime,
    addresses: list[str],
    requested_by: str,
    note: str = "",
) -> Queued:
    """Queue one export for delivery. Builds it once, to fail now rather than later."""
    from disputeshield import audit
    from disputeshield.reports import regulatory

    recipients = resolve_recipients(addresses)

    # Built here as well as at send time. A period that cannot be exported should
    # say so to the person who asked, not to a log line six minutes later.
    export = regulatory.build(tenant=tenant, period_from=period_from, period_to=period_to)
    attachment = export.as_zip()
    if len(attachment) > MAX_ATTACHMENT_BYTES:
        raise ReportTooLarge(
            f"The export for this period is {len(attachment) / 1_048_576:.1f} MB, over the "
            f"{MAX_ATTACHMENT_BYTES / 1_048_576:.0f} MB limit for email. Narrow the period "
            "or download it from the API."
        )

    files = dict(export.manifest["files"])
    key = _idempotency_key(period_from, period_to, [r.address for r in recipients])

    with transaction.atomic():
        notification, created = NotificationOutbox.objects.get_or_create(
            tenant=tenant,
            idempotency_key=key,
            defaults={
                "channel": CHANNEL,
                "event_type": EVENT_TYPE,
                "payload": {
                    # The period and the promise. Not the bundle: see the module
                    # docstring. Nothing here is case content.
                    "period_from": period_from.isoformat(),
                    "period_to": period_to.isoformat(),
                    "recipients": [r.address for r in recipients],
                    "files": files,
                    "signature": export.manifest["signature"],
                    "requested_by": requested_by,
                    "note": note[:500],
                },
            },
        )

        if created:
            # Recorded at request time rather than at send time, and separately
            # from the delivery result. "Who asked for a period to be sent
            # outside" is the question a supervisor asks, and it has an answer
            # even when the send later fails.
            audit.append(
                tenant=tenant,
                event_type="report.delivery_requested",
                subject_type="report",
                subject_id=notification.pk,
                actor_type="user",
                actor_id=requested_by,
                payload={
                    "period_from": period_from.isoformat(),
                    "period_to": period_to.isoformat(),
                    "recipients": [r.address for r in recipients],
                    "files": files,
                    "signature": export.manifest["signature"],
                    "note": note[:500],
                },
            )

    return Queued(
        notification_id=notification.pk,
        idempotency_key=key,
        recipients=tuple(r.address for r in recipients),
        files=files,
    )


def deliver(notification: NotificationOutbox) -> None:
    """Rebuild, verify against what was promised, then send.

    Raising here is correct: the dispatcher retries and eventually parks, and a
    parked delivery is visible. A caught-and-logged failure is a report a
    supervisor is still waiting for.
    """
    from disputeshield.reports import regulatory

    payload = notification.payload
    period_from = datetime.fromisoformat(payload["period_from"])
    period_to = datetime.fromisoformat(payload["period_to"])

    export = regulatory.build(
        tenant=notification.tenant, period_from=period_from, period_to=period_to
    )
    rebuilt = dict(export.manifest["files"])
    promised = dict(payload["files"])

    if rebuilt != promised:
        differing = sorted(
            name for name in set(rebuilt) | set(promised) if rebuilt.get(name) != promised.get(name)
        )
        raise BundleChanged(
            f"The export for this period no longer matches what was requested "
            f"({', '.join(differing)} differ). Nothing was sent: a bundle whose digests "
            "disagree with the ones the recipient was promised is worse than a late one."
        )

    attachment = export.as_zip()
    if len(attachment) > MAX_ATTACHMENT_BYTES:
        raise ReportTooLarge("The export grew past the email attachment limit before it was sent.")

    stem = f"disputeshield-{period_from.date()}-{period_to.date()}"
    message = EmailMessage(
        subject=f"Regulatory export: {period_from.date()} to {period_to.date()}",
        body=_body(payload, period_from, period_to, attachment),
        to=list(payload["recipients"]),
    )
    message.attach(f"{stem}.zip", attachment, "application/zip")
    # Not silently. A provider failure must reach the dispatcher's retry.
    message.send(fail_silently=False)

    logger.info(
        "regulatory export delivered",
        extra={
            "notification": notification.pk,
            "recipients": len(payload["recipients"]),
            "bytes": len(attachment),
        },
    )


def record_delivered(notification: NotificationOutbox) -> None:
    """The audit record for the send itself, written after it succeeded."""
    from disputeshield import audit

    payload = notification.payload
    audit.append(
        tenant=notification.tenant,
        event_type="report.delivered",
        subject_type="report",
        subject_id=notification.pk,
        actor_type="system",
        actor_id="dispatcher",
        payload={
            "recipients": payload["recipients"],
            "period_from": payload["period_from"],
            "period_to": payload["period_to"],
            "files": payload["files"],
            "signature": payload["signature"],
            "delivered_at": timezone.now().isoformat(),
        },
    )


def _body(payload: dict, period_from: datetime, period_to: datetime, attachment: bytes) -> str:
    """Restates the manifest so the attachment can be checked against the API.

    A recipient who trusts the attachment because the email said to trust it has
    verified nothing. These digests are the ones `GET /v1/reports/regulatory` will
    return for the same period, from a channel the email did not travel on.
    """
    digests = "\n".join(f"  {name}: {digest}" for name, digest in sorted(payload["files"].items()))
    note = payload.get("note") or ""
    return (
        f"Regulatory export for {period_from.date()} to {period_to.date()}.\n"
        f"Requested by {payload.get('requested_by')}.\n"
        f"{('Note: ' + note + chr(10)) if note else ''}"
        f"\nThe attached zip contains cases.csv, history.csv, report.pdf and manifest.json.\n"
        f"\nSHA-256 of each file, as recorded in the signed manifest:\n{digests}\n"
        f"\nManifest signature: {payload.get('signature')}\n"
        f"Attachment: {len(attachment)} bytes.\n"
        f"\nThe same period exported again produces identical bytes. Verify these "
        f"digests against GET /v1/reports/regulatory rather than trusting this "
        f"message — an email is not evidence of its own integrity.\n"
    )


def _idempotency_key(period_from: datetime, period_to: datetime, addresses: list[str]) -> str:
    """Derived from what the delivery is *about*, like every other outbox key.

    The consequence is deliberate: asking twice for the same period to the same
    people sends one email, so a retried request during an incident cannot page a
    regulator's inbox twice.
    """
    material = f"{period_from.isoformat()}|{period_to.isoformat()}|{','.join(sorted(addresses))}"
    return f"{EVENT_TYPE}:{hashlib.sha256(material.encode()).hexdigest()[:32]}"
