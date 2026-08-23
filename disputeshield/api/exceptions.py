"""D8 — 404 rather than 403, as a handler rather than as a convention.

§8.1 and §10 both require that cross-boundary access returns 404, because a 403
confirms the resource exists. Left to each view to remember, one view eventually
forgets, and the one that forgets leaks *existence* — a finding a penetration
test produces and a code review does not.

Authentication failures stay 401. Those are statements about the caller, not
about a resource, and answering 404 to a malformed key would be actively unhelpful
to the engineer integrating.
"""

from __future__ import annotations

import logging

from django.http import Http404
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from disputeshield.tenancy.context import TenantContextRequired

logger = logging.getLogger(__name__)

NOT_FOUND_BODY = {
    "error": {"type": "not_found", "message": "No such resource."},
}


def exception_handler(exc, context_):
    if isinstance(exc, TenantContextRequired):
        # A bug, never a permission problem: the code path did not decide who it
        # was acting for. Answering 404 would paper over that, so it is a 500 —
        # loudly, with the detail in the log and nothing in the response.
        logger.exception("tenant context missing in %s", context_.get("view"))
        return Response(
            {"error": {"type": "internal_error", "message": "Internal error."}}, status=500
        )

    if isinstance(exc, exceptions.PermissionDenied | Http404):
        # The real reason is logged; the response says nothing. Debugging is
        # harder by exactly this much, and that is the trade being made.
        logger.info(
            "denied, answering 404",
            extra={"view": str(context_.get("view")), "reason": str(exc)},
        )
        return Response(NOT_FOUND_BODY, status=404)

    response = drf_exception_handler(exc, context_)
    if response is not None and not isinstance(response.data, dict):
        return response
    if response is not None and "error" not in response.data:
        detail = response.data.get("detail") if isinstance(response.data, dict) else None
        response.data = {
            "error": {
                "type": _type_for(response.status_code),
                "message": str(detail) if detail else "Request failed.",
                "fields": {k: v for k, v in response.data.items() if k != "detail"} or None,
            }
        }
    return response


def _type_for(status_code: int) -> str:
    return {
        400: "invalid_request",
        401: "unauthenticated",
        404: "not_found",
        409: "conflict",
        429: "rate_limited",
    }.get(status_code, "error")
