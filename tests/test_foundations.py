"""Identifiers, keys, tenant context plumbing and the startup invariants."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from disputeshield import conf
from disputeshield.identifiers import generate_api_key, new_id
from disputeshield.models import Tenant
from disputeshield.tenancy import context
from disputeshield.tenancy.middleware import (
    TenantContextMiddleware,
    current_tenant_context,
    db_tenant_context,
)


class TestIdentifiers:
    def test_identifiers_carry_a_type_prefix(self):
        assert new_id("dsp").startswith("dsp_")

    def test_identifiers_are_not_enumerable(self):
        """§10 answers ID enumeration with random identifiers. A sequential id
        would defeat that before the 404 ever gets a chance to."""
        generated = {new_id("dsp") for _ in range(2_000)}
        assert len(generated) == 2_000

    def test_the_alphabet_excludes_visually_confusable_characters(self):
        """I/L/O/U are absent so an identifier read off a screen into a support
        ticket survives the transcription."""
        body = new_id("dsp", length=200).split("_", 1)[1]
        assert not set(body) & set("ILOU")

    def test_api_keys_are_environment_scoped(self):
        live, live_prefix = generate_api_key("live")
        test, test_prefix = generate_api_key("test")
        assert live.startswith("ds_live_")
        assert test.startswith("ds_test_")
        assert live_prefix != test_prefix

    def test_an_unknown_environment_is_refused(self):
        """A key whose environment is neither test nor live cannot be reasoned
        about — and §8.2's guarantee is that a leaked test key can do nothing to
        live data."""
        with pytest.raises(ValueError, match=r"test.*live"):
            generate_api_key("staging")

    def test_the_prefix_is_a_prefix_of_the_key(self):
        full, prefix = generate_api_key("live")
        assert full.startswith(prefix)
        assert len(prefix) == 16


@pytest.mark.django_db
class TestAPIKeyLifecycle:
    def test_revocation_is_immediate_and_recorded(self, tenant_a, make_api_key, as_tenant):
        _, key = make_api_key(tenant_a)
        assert key.is_active
        with as_tenant(tenant_a):
            key.revoke()
            key.refresh_from_db()
        assert not key.is_active
        assert key.revoked_at is not None

    def test_a_write_with_no_tenant_context_changes_nothing(self, tenant_a, make_api_key):
        """RLS does not merely hide rows from reads — it hides them from writes.

        An update issued with no tenant context matches zero rows, so the write
        silently does nothing rather than touching another tenant's data. Django
        surfaces that as a DatabaseError here because `update_fields` lets it
        notice; the important half is that nothing was modified.
        """
        from django.db import DatabaseError

        _, key = make_api_key(tenant_a)
        with pytest.raises(DatabaseError):
            key.revoke()

    def test_the_key_itself_is_never_stored(self, tenant_a, make_api_key):
        full, key = make_api_key(tenant_a)
        assert full not in key.key_hash
        assert full.removeprefix(key.prefix) not in key.key_hash


@pytest.mark.django_db
class TestTenantLockKey:
    def test_the_lock_key_is_stable_for_a_tenant(self, tenant_a):
        assert tenant_a.lock_key == Tenant.objects.get(pk=tenant_a.pk).lock_key

    def test_the_lock_key_fits_a_signed_32_bit_integer(self, tenant_a):
        """pg_advisory_xact_lock takes two int4s. A value that overflows would
        raise inside the audit append, which is the worst place to find out."""
        assert 0 <= tenant_a.lock_key <= 0x7FFF_FFFF


@pytest.mark.django_db
class TestTenantContext:
    def test_the_context_restores_rather_than_clears_on_exit(self, tenant_a, tenant_b):
        with context.tenant_context(tenant_a.pk):
            with context.tenant_context(tenant_b.pk):
                assert context.get() == tenant_b.pk
            assert context.get() == tenant_a.pk, "the outer scope was silently dropped"
        assert context.get() is None

    def test_the_database_context_also_restores(self, tenant_a, tenant_b):
        """The failure this prevents is a cross-tenant read with no bad code
        anywhere in the traversal (ADR-0005)."""
        with db_tenant_context(tenant_a.pk):
            with db_tenant_context(tenant_b.pk):
                assert current_tenant_context() == tenant_b.pk
            assert current_tenant_context() == tenant_a.pk
        assert current_tenant_context() == ""

    def test_the_middleware_establishes_the_database_context(self, tenant_a, rf):
        seen = {}

        def view(request):
            seen["tenant"] = current_tenant_context()
            return "response"

        request = rf.get("/")
        request.tenant = tenant_a
        assert TenantContextMiddleware(view)(request) == "response"
        assert seen["tenant"] == tenant_a.pk

    def test_the_middleware_sets_nothing_when_there_is_no_tenant(self, rf):
        """An unauthenticated request must not inherit a scope. No context means
        RLS returns nothing, which is the correct failure."""
        request = rf.get("/")
        TenantContextMiddleware(lambda r: "response")(request)
        assert current_tenant_context() == ""


class TestProductionInvariants:
    """§10.2 — assertions, not settings. Each one has been someone's incident."""

    @override_settings(DEBUG=True)
    def test_debug_is_reported(self):
        assert any("DEBUG is True" in problem for problem in conf.check_production_invariants())

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["*"])
    def test_wildcard_allowed_hosts_is_reported(self):
        assert any("ALLOWED_HOSTS" in problem for problem in conf.check_production_invariants())

    @override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False)
    def test_missing_tls_redirect_is_reported(self):
        assert any(
            "SECURE_SSL_REDIRECT" in problem for problem in conf.check_production_invariants()
        )

    def test_an_unknown_setting_raises_rather_than_returning_none(self):
        """A typo in a setting name must not silently become None — that is how a
        security control ends up disabled by a spelling mistake."""
        with pytest.raises(ImproperlyConfigured, match="Unknown DisputeShield setting"):
            conf.get("ENCYRPTION_KEY_REF")

    def test_documented_defaults_are_readable(self):
        assert conf.get("SESSION_TOKEN_TTL_SECONDS") == 1800
        assert conf.get("DEFAULT_SLA_POLICY")["resolution_hours"] == 72
