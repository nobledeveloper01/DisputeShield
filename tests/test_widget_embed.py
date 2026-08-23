"""§10.1 and D9 — the embed document, its CSP, and the origin boundary."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from disputeshield.api.views_embed import content_security_policy
from disputeshield.models import AllowedOrigin, validate_origin

pytestmark = pytest.mark.django_db


def directives(response) -> dict[str, str]:
    policy = response["Content-Security-Policy"]
    parsed = {}
    for part in policy.split(";"):
        part = part.strip()
        if part:
            name, _, value = part.partition(" ")
            parsed[name] = value
    return parsed


def embed(client, key: str, referer: str = "") -> object:
    headers = {"HTTP_REFERER": referer} if referer else {}
    return client.get(f"/v1/embed?k={key}", **headers)


class TestOriginValidation:
    def test_a_bare_origin_is_accepted(self):
        validate_origin("https://app.acme.io")
        validate_origin("http://localhost:5173")

    def test_an_origin_with_a_path_is_refused(self):
        """`frame-ancestors` ignores the path, so the longer form would authorise
        the whole host while the tenant believes they restricted a page."""
        with pytest.raises(ValidationError, match="authorise the whole host"):
            validate_origin("https://app.acme.io/dashboard")

    def test_a_wildcard_origin_is_refused(self):
        with pytest.raises(ValidationError, match="wildcard"):
            validate_origin("https://*.acme.io")

    def test_the_null_origin_is_refused(self):
        """'null' is the origin of a sandboxed or data: document, so allowing it
        lets any such document frame the widget."""
        with pytest.raises(ValidationError, match="sandboxed"):
            validate_origin("null")

    def test_a_scheme_less_origin_is_refused(self):
        with pytest.raises(ValidationError, match="http"):
            validate_origin("app.acme.io")

    def test_the_model_refuses_to_save_an_invalid_origin(self, tenant_a, as_tenant):
        """Validation on `save`, not only in a form: the admin, a fixture and a
        management command all reach the model without a form in between."""
        with as_tenant(tenant_a), pytest.raises(ValidationError):
            AllowedOrigin.objects.create(tenant=tenant_a, origin="https://app.acme.io/x")


class TestContentSecurityPolicy:
    def test_the_policy_denies_by_default(self):
        policy = content_security_policy("https://app.acme.io", "https://api.disputeshield.dev")
        assert policy.startswith("default-src 'none'")

    def test_the_policy_has_no_unsafe_inline_anywhere(self):
        """Dispute descriptions are attacker-controlled text rendered in a
        browser. An inline-script allowance turns that into an XSS."""
        policy = content_security_policy("https://app.acme.io", "https://api.disputeshield.dev")
        assert "unsafe-inline" not in policy
        assert "unsafe-eval" not in policy

    def test_frame_ancestors_is_the_tenants_own_origins(
        self, tenant_a, publishable_key_for, allowed_origin, client
    ):
        allowed_origin(tenant_a, "https://app.acme.io")
        full, _ = publishable_key_for(tenant_a)

        response = embed(client, full, referer="https://app.acme.io/account")
        assert response.status_code == 200
        assert directives(response)["frame-ancestors"] == "https://app.acme.io"

    def test_a_tenant_with_no_registered_origin_frames_nowhere(
        self, tenant_a, publishable_key_for, client
    ):
        """Failing closed is the only safe default: a permissive one would ship a
        working widget with no boundary, and nobody would notice."""
        full, _ = publishable_key_for(tenant_a)
        response = embed(client, full)
        assert directives(response)["frame-ancestors"] == "'none'"

    def test_two_tenants_get_different_frame_ancestors(
        self, tenant_a, tenant_b, publishable_key_for, allowed_origin, client
    ):
        """D9's whole point. Caching this document publicly would hand one tenant
        another tenant's frame-ancestors."""
        allowed_origin(tenant_a, "https://app.acme.io")
        allowed_origin(tenant_b, "https://app.borealis.test")
        key_a, _ = publishable_key_for(tenant_a)
        key_b, _ = publishable_key_for(tenant_b)

        a = directives(embed(client, key_a, "https://app.acme.io/x"))["frame-ancestors"]
        b = directives(embed(client, key_b, "https://app.borealis.test/x"))["frame-ancestors"]
        assert a == "https://app.acme.io"
        assert b == "https://app.borealis.test"

    def test_the_document_is_never_publicly_cacheable(
        self, tenant_a, publishable_key_for, allowed_origin, client
    ):
        """A shared cache holding a per-tenant CSP is the misconfiguration this
        split exists to prevent."""
        allowed_origin(tenant_a)
        full, _ = publishable_key_for(tenant_a)
        response = embed(client, full, "https://app.acme.io/x")
        assert "private" in response["Cache-Control"]
        assert "public" not in response["Cache-Control"]


class TestFailingClosed:
    def test_an_unknown_publishable_key_renders_nothing(self, client):
        """§8.6 principle 1: fail closed and quietly. Never render an error on the
        host's page."""
        response = embed(client, "pk_live_not_a_real_key")
        assert response.status_code == 403
        assert response.content == b""
        assert directives(response)["frame-ancestors"] == "'none'"

    def test_a_load_from_an_unregistered_origin_is_refused_and_recorded(
        self, tenant_a, publishable_key_for, allowed_origin, client, caplog
    ):
        """§11.6's most common support ticket, by a wide margin: a tenant added a
        domain and did not register it. Recorded so an operator reads the
        diagnosis rather than deducing it."""
        import logging

        allowed_origin(tenant_a, "https://app.acme.io")
        full, _ = publishable_key_for(tenant_a)

        with caplog.at_level(logging.WARNING):
            response = embed(client, full, referer="https://staging.acme.io/account")

        assert response.status_code == 403
        assert response["X-DisputeShield-Closed"] == "unregistered_origin"
        assert any("unregistered origin" in record.message for record in caplog.records)

    def test_a_revoked_publishable_key_stops_rendering(
        self, tenant_a, publishable_key_for, allowed_origin, client, as_tenant
    ):
        allowed_origin(tenant_a)
        full, key = publishable_key_for(tenant_a)
        assert embed(client, full, "https://app.acme.io/x").status_code == 200

        with as_tenant(tenant_a):
            key.revoke()
        assert embed(client, full, "https://app.acme.io/x").status_code == 403


class TestTheDocumentItself:
    def test_it_carries_no_inline_script(
        self, tenant_a, publishable_key_for, allowed_origin, client
    ):
        """The CSP forbids inline script, so a document containing one would be a
        document that cannot run — and a CSP nobody tested against the document
        it protects is a CSP that gets relaxed on the first bug report."""
        allowed_origin(tenant_a)
        full, _ = publishable_key_for(tenant_a)
        body = embed(client, full, "https://app.acme.io/x").content.decode()

        import re

        for match in re.finditer(r"<script([^>]*)>(.*?)</script>", body, re.S):
            attrs, inline = match.groups()
            assert "src=" in attrs, "an inline <script> would be blocked by our own CSP"
            assert not inline.strip()

    def test_the_publishable_key_is_escaped_into_the_document(
        self, tenant_a, publishable_key_for, allowed_origin, client
    ):
        allowed_origin(tenant_a)
        full, _ = publishable_key_for(tenant_a)
        response = client.get(
            f"/v1/embed?k={full}<script>alert(1)</script>",
            HTTP_REFERER="https://app.acme.io/x",
        )
        assert b"<script>alert(1)</script>" not in response.content


class TestConnectSource:
    """The CSP's connect-src is configuration, not a reflected request header."""

    def test_it_uses_the_configured_api_origin(
        self, tenant_a, publishable_key_for, allowed_origin, client, settings
    ):
        settings.DISPUTESHIELD = {**settings.DISPUTESHIELD, "API_ORIGIN": "https://api.example"}
        allowed_origin(tenant_a)
        full, _ = publishable_key_for(tenant_a)

        response = embed(client, full, "https://app.acme.io/x")
        assert directives(response)["connect-src"] == "https://api.example"

    def test_a_forged_host_header_cannot_widen_it(
        self, tenant_a, publishable_key_for, allowed_origin, client, settings
    ):
        """Django narrows Host via ALLOWED_HOSTS, which is not the same as
        removing an attacker's vote in a security header."""
        settings.DISPUTESHIELD = {**settings.DISPUTESHIELD, "API_ORIGIN": "https://api.example"}
        settings.ALLOWED_HOSTS = ["*"]
        allowed_origin(tenant_a)
        full, _ = publishable_key_for(tenant_a)

        response = client.get(
            f"/v1/embed?k={full}",
            HTTP_REFERER="https://app.acme.io/x",
            HTTP_HOST="attacker.example",
        )
        assert directives(response)["connect-src"] == "https://api.example"

    def test_production_without_a_configured_origin_connects_nowhere(
        self, tenant_a, publishable_key_for, allowed_origin, client, settings
    ):
        settings.DEBUG = False
        settings.DISPUTESHIELD = {**settings.DISPUTESHIELD, "API_ORIGIN": None}
        allowed_origin(tenant_a)
        full, _ = publishable_key_for(tenant_a)

        response = embed(client, full, "https://app.acme.io/x")
        assert directives(response)["connect-src"] == "'none'"
