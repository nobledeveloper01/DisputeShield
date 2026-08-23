"""§8.6 principle 4 — every write endpoint returns the original result on replay.

Two details that are easy to get wrong and expensive to get wrong:

  * **The key is required on writes.** An optional idempotency key means the
    guarantee silently does not apply to the callers who most need it, and they
    have no way to tell.
  * **The request is fingerprinted.** Reusing a key with a *different* body is a
    client bug, and returning the first response would hide it. That answers 409.
"""

from __future__ import annotations

import hashlib
import json

from rest_framework import status
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from disputeshield.models import IdempotencyRecord

HEADER = "Idempotency-Key"


def fingerprint(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class IdempotentCreateMixin:
    """Mix into a view whose POST must be replay-safe."""

    def idempotent(self, request, endpoint: str, produce):
        key = request.headers.get(HEADER)
        if not key:
            return Response(
                {
                    "error": {
                        "type": "invalid_request",
                        "message": f"{HEADER} is required on write requests.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = request.user.tenant
        digest = fingerprint(request.data)
        existing = IdempotencyRecord.objects.filter(key=key).first()

        if existing is not None:
            if existing.request_fingerprint != digest:
                return Response(
                    {
                        "error": {
                            "type": "conflict",
                            "message": (
                                f"{HEADER} {key!r} was already used with a different request "
                                "body. Reusing a key for a different request is a client bug, "
                                "and returning the first response would hide it."
                            ),
                        }
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(existing.response_body, status=existing.response_status)

        response = produce()
        IdempotencyRecord.objects.create(
            tenant=tenant,
            key=key,
            endpoint=endpoint,
            request_fingerprint=digest,
            response_status=response.status_code,
            # Stored as *rendered* JSON, not as the serializer's Python objects.
            # A replay has to return what the client actually received, and
            # `response.data` can hold datetimes and Decimals that a JSONField
            # cannot store — which fails at write time, on the original request,
            # for a feature that only exists to make retries safe.
            response_body=_renderable(response.data),
        )
        return response


def _renderable(data):
    """Round-trip through DRF's renderer so what is stored is what was sent."""
    if data is None:
        return {}
    return json.loads(JSONRenderer().render(data))
