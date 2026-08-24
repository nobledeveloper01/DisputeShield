"""Signed, ordered, replayable outbound events (amplifier A14).

The specification has DisputeShield receiving context and offering export, but
nothing pushing outward in real time. A dispute resolved with an upheld outcome
and a recorded refund amount is an event the fintech's ledger, ops tooling and
analytics all need, and polling a management API for it is how integrations rot.

Three delivery properties, each with a failure it prevents:

  * **Ordered per dispute.** A `dispute.resolved` arriving before its
    `dispute.acknowledged` has the fintech's ledger reacting to a case it has not
    heard of.
  * **At-least-once, with a deterministic idempotency key.** A consumer that
    de-duplicates on the key cannot be made to double-process by our retries.
  * **Parked, never dropped.** An event silently discarded after a customer's
    outage is a reconciliation gap nobody can explain months later.

The signature is deliberately the Stripe scheme (§8.2): well documented, widely
understood, and not a novel cryptographic design.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import time
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from disputeshield.models import WebhookDelivery, WebhookEndpoint

MAX_ATTEMPTS = 8
# Exponential, capped. A customer down for a day is retried through the day
# rather than hammered for the first minute and abandoned.
BACKOFF_SECONDS = (30, 120, 600, 1800, 3600, 7200, 21600, 43200)

SIGNATURE_HEADER = "X-DisputeShield-Signature"
TOLERANCE_SECONDS = 300


class Transport:
    """How a delivery reaches an endpoint. Injected so tests never touch a network."""

    def post(self, url: str, body: bytes, headers: dict) -> int:  # pragma: no cover
        raise NotImplementedError


class CollectingTransport(Transport):
    """Development and CI. Records instead of sending."""

    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.sent: list[dict] = []

    def post(self, url: str, body: bytes, headers: dict) -> int:
        self.sent.append({"url": url, "body": body, "headers": headers})
        return self.status_code


class FailingTransport(Transport):
    """A customer endpoint that is down."""

    def __init__(self, status_code: int = 503) -> None:
        self.status_code = status_code

    def post(self, url: str, body: bytes, headers: dict) -> int:
        return self.status_code


@dataclasses.dataclass(frozen=True)
class DispatchResult:
    delivered: int
    retried: int
    parked: int
    blocked: int  # waiting behind an earlier failure for the same case


def sign(secret: str, body: bytes, *, timestamp: int | None = None) -> str:
    """`t=<unix>,v1=<hex>` over `{timestamp}.{raw_body}` (§8.2).

    The timestamp is inside the signed material so a captured payload cannot be
    replayed at a customer indefinitely.
    """
    timestamp = timestamp or int(time.time())
    signed = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify(secret: str, body: bytes, header: str, *, now: int | None = None) -> bool:
    """The check a customer performs. Published so they can implement it exactly."""
    parts = dict(piece.split("=", 1) for piece in header.split(",") if "=" in piece)
    try:
        timestamp = int(parts.get("t", ""))
    except ValueError:
        return False

    if abs((now or int(time.time())) - timestamp) > TOLERANCE_SECONDS:
        return False

    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, parts.get("v1", ""))


def enqueue(*, dispute, event_type: str, payload: dict | None = None) -> list[WebhookDelivery]:
    """Queue one event for every endpoint that wants it.

    The payload is the **customer-visible projection** — see
    `WebhookDisputeSerializer`, which lives in the widget serializer module so
    that the same field-graph test protects both.
    """
    from disputeshield.api.serializers_widget import WebhookDisputeSerializer

    body = payload or json.loads(json.dumps(WebhookDisputeSerializer(dispute).data, default=str))

    created: list[WebhookDelivery] = []
    with transaction.atomic():
        # Per-case sequence, so ordering is a property of the row rather than of
        # when a worker happened to pick it up.
        next_sequence = (
            WebhookDelivery.objects.filter(sequence_key=dispute.pk)
            .order_by("-sequence")
            .values_list("sequence", flat=True)
            .first()
            or 0
        ) + 1

        for endpoint in WebhookEndpoint.objects.filter(is_active=True):
            if not endpoint.wants(event_type):
                continue
            delivery, was_created = WebhookDelivery.objects.get_or_create(
                tenant=dispute.tenant,
                endpoint=endpoint,
                idempotency_key=f"{event_type}:{dispute.pk}:{next_sequence}",
                defaults={
                    "event_type": event_type,
                    "sequence_key": dispute.pk,
                    "sequence": next_sequence,
                    "payload": {"event": event_type, "data": body},
                    "next_attempt_at": timezone.now(),
                },
            )
            if was_created:
                created.append(delivery)
    return created


def dispatch(*, transport: Transport | None = None, limit: int = 100, now=None) -> DispatchResult:
    """Attempt due deliveries, per tenant, in per-case order."""
    from disputeshield.tenancy.platform import for_each_tenant

    transport = transport or CollectingTransport()
    now = now or timezone.now()

    delivered = retried = parked = blocked = 0
    for one in for_each_tenant(lambda _tenant_id: _dispatch_one_tenant(transport, limit, now)):
        delivered += one[0]
        retried += one[1]
        parked += one[2]
        blocked += one[3]
    return DispatchResult(delivered=delivered, retried=retried, parked=parked, blocked=blocked)


def _dispatch_one_tenant(transport: Transport, limit: int, now) -> tuple[int, int, int, int]:
    delivered = retried = parked = blocked = 0

    due = list(
        WebhookDelivery.objects.filter(
            status=WebhookDelivery.Status.PENDING, next_attempt_at__lte=now
        )
        .select_related("endpoint")
        .order_by("sequence_key", "sequence", "id")[:limit]
    )

    # A case whose earlier event has not landed does not get its later one.
    stalled_cases: set[str] = set()

    for delivery in due:
        if delivery.sequence_key in stalled_cases:
            blocked += 1
            continue

        body = json.dumps(delivery.payload, sort_keys=True, separators=(",", ":")).encode()
        headers = {
            "Content-Type": "application/json",
            SIGNATURE_HEADER: sign(delivery.endpoint.signing_secret, body),
            # Published so a consumer can de-duplicate on it. Deterministic, so a
            # retry carries the same value the first attempt did.
            "Idempotency-Key": delivery.idempotency_key,
            "X-DisputeShield-Event": delivery.event_type,
        }

        with transaction.atomic():
            delivery.attempts += 1
            try:
                status_code = transport.post(delivery.endpoint.url, body, headers)
            except Exception as exc:
                status_code = 0
                delivery.last_error = f"{type(exc).__name__}: {exc}"[:255]

            delivery.last_status_code = status_code or None

            if 200 <= status_code < 300:
                delivery.status = WebhookDelivery.Status.DELIVERED
                delivery.delivered_at = now
                delivery.save(
                    update_fields=[
                        "attempts",
                        "status",
                        "delivered_at",
                        "last_status_code",
                        "last_error",
                    ]
                )
                delivered += 1
                continue

            stalled_cases.add(delivery.sequence_key)

            if delivery.attempts >= MAX_ATTEMPTS:
                # Parked, not dropped (§8.6 principle 2).
                delivery.status = WebhookDelivery.Status.PARKED
                parked += 1
            else:
                delay = BACKOFF_SECONDS[min(delivery.attempts - 1, len(BACKOFF_SECONDS) - 1)]
                delivery.next_attempt_at = now + timedelta(seconds=delay)
                retried += 1

            delivery.save(
                update_fields=[
                    "attempts",
                    "status",
                    "next_attempt_at",
                    "last_status_code",
                    "last_error",
                ]
            )

    return delivered, retried, parked, blocked


def replay(*, delivery: WebhookDelivery) -> WebhookDelivery:
    """Put a parked delivery back in the queue, keeping its idempotency key.

    Keeping the key is the whole point: a consumer that already processed it
    ignores the replay, and one that never received it processes it once.
    """
    delivery.status = WebhookDelivery.Status.PENDING
    delivery.attempts = 0
    delivery.next_attempt_at = timezone.now()
    delivery.save(update_fields=["status", "attempts", "next_attempt_at"])
    return delivery
