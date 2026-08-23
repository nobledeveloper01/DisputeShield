"""`GET /v1/embed` — the document that runs inside the sandboxed iframe (D9).

Two artefacts with two caching policies, and conflating them is a real breach:

  * **This document is dynamic**, rendered per publishable key, cached privately
    for a minute, carrying the tenant's own `frame-ancestors`.
  * **The bundles it references are static**, tenant-independent, content-hashed
    and cached for a year.

Caching this document publicly would hand one tenant another tenant's
`frame-ancestors`, turning the product's headline security control into a shared
misconfiguration (§10.1).

It is also the natural place to notice §11.6's most common support ticket: a
tenant added a domain and did not register it. A load from an unregistered origin
is recorded here, so an operator reads the diagnosis rather than deducing it.
"""

from __future__ import annotations

import logging

from django.http import HttpResponse
from django.utils.html import escape
from rest_framework.views import APIView

from disputeshield import conf
from disputeshield.api.authentication import SilentPublishableKeyAuthentication
from disputeshield.models import AllowedOrigin, WidgetConfig

logger = logging.getLogger(__name__)

BUNDLE_URL = "/static/widget/widget.js"

TEMPLATE = """<!doctype html>
<html lang="{locale}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Report a problem</title>
<link rel="stylesheet" href="{stylesheet}">
</head>
<body data-key="{publishable_key}" data-origin="{parent_origin}">
<div id="disputeshield-root"></div>
<script src="{bundle}" defer></script>
</body>
</html>
"""


def content_security_policy(frame_ancestors: str, api_origin: str) -> str:
    """§10.1, generated per tenant.

    `default-src 'none'` first, so anything not named below is refused rather
    than inherited. No `unsafe-inline` anywhere: dispute descriptions are
    attacker-controlled text rendered in a browser, and an inline-script
    allowance is what turns that from an inconvenience into an XSS.
    """
    return "; ".join(
        [
            "default-src 'none'",
            "script-src 'self'",
            "style-src 'self'",
            f"connect-src {api_origin}",
            "img-src 'self' data:",
            "font-src 'self'",
            f"frame-ancestors {frame_ancestors}",
            "base-uri 'none'",
            "form-action 'none'",
            "object-src 'none'",
        ]
    )


class EmbedView(APIView):
    authentication_classes = [SilentPublishableKeyAuthentication]
    permission_classes = []

    def get(self, request):
        user = getattr(request, "user", None)
        if user is None or not hasattr(user, "api_key"):
            # Fail closed and quietly (§8.6 principle 1). No detail, and nothing
            # that renders on the host's page.
            return self._closed("unknown_key")

        tenant = user.tenant
        config = WidgetConfig.objects.filter(tenant=tenant).first()
        origins = list(AllowedOrigin.objects.values_list("origin", flat=True))
        referring_origin = _origin_of(request.META.get("HTTP_REFERER", ""))

        if referring_origin and referring_origin not in origins:
            # §11.6's first diagnosis, recorded rather than deduced.
            logger.warning(
                "widget load from an unregistered origin",
                extra={
                    "tenant": tenant.pk,
                    "origin": referring_origin,
                    "registered": origins,
                },
            )
            return self._closed("unregistered_origin", origin=referring_origin)

        frame_ancestors = " ".join(origins) if origins else "'none'"
        body = TEMPLATE.format(
            locale=escape(config.locale if config else "en"),
            publishable_key=escape(request.query_params.get("k", "")),
            parent_origin=escape(referring_origin),
            bundle=BUNDLE_URL,
            stylesheet="/static/widget/widget.css",
        )

        response = HttpResponse(body, content_type="text/html; charset=utf-8")
        response["Content-Security-Policy"] = content_security_policy(
            frame_ancestors, _api_origin(request)
        )
        # Private and short: the document varies per tenant, so a shared cache
        # holding it is the misconfiguration described above.
        response["Cache-Control"] = "private, max-age=60"
        response["X-Frame-Options"] = "ALLOWALL" if origins else "DENY"
        response["Referrer-Policy"] = "strict-origin"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def _closed(self, reason: str, origin: str = "") -> HttpResponse:
        response = HttpResponse("", status=403, content_type="text/html; charset=utf-8")
        response["Content-Security-Policy"] = content_security_policy("'none'", "'none'")
        response["Cache-Control"] = "no-store"
        response["X-DisputeShield-Closed"] = reason
        return response


def _origin_of(referer: str) -> str:
    from urllib.parse import urlparse

    if not referer:
        return ""
    parsed = urlparse(referer)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _api_origin(request) -> str:
    """Where the widget may send requests, for the CSP's `connect-src`.

    Configured, not derived from the request. Django validates `Host` against
    ALLOWED_HOSTS, so the request-derived form is not exploitable today — but it
    makes a security header depend on an attacker-supplied one, and the next
    person to relax ALLOWED_HOSTS for a health check would not connect the two.

    Falls back to the request only when DEBUG is on, where the alternative is a
    developer having to configure a value to see the widget load at all.
    """
    from django.conf import settings

    configured = conf.get("API_ORIGIN")
    if configured:
        return configured
    if settings.DEBUG:
        # The rule below looks for a Flask view returning a formatted response
        # body. This is a Django helper returning an origin for a CSP directive,
        # on a branch that cannot execute in production, from a host Django has
        # already validated against ALLOWED_HOSTS. The suppression has to sit on
        # the line itself — semgrep only reads the offending line and the one
        # immediately above it.
        scheme, host = request.scheme, request.get_host()
        return f"{scheme}://{host}"  # nosemgrep: python.flask.security.audit.directly-returned-format-string.directly-returned-format-string
    # No configured origin in production means the widget calls nowhere, which
    # fails closed rather than guessing.
    return "'none'"
