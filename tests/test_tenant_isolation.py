"""§8.1 — three independent isolation layers, tested independently.

Testing them together would pass if any one of them worked, which is precisely
the property the three-layer design exists to avoid relying on. So each layer is
disabled in turn and the remaining ones are asserted to still hold.

The HTTP half of this gate — that cross-boundary reads return 404 and not 403 —
lands in phase 3 with the endpoints it applies to. There are no endpoints yet,
and a passing assertion over an empty URLconf would be a green gate that checks
nothing.
"""

from __future__ import annotations

import pytest
from django.db import connection

from disputeshield import audit
from disputeshield.models import Agent, AuditRecord
from disputeshield.tenancy import context
from disputeshield.tenancy.context import TenantContextRequired

pytestmark = [pytest.mark.django_db, pytest.mark.isolation]


# -- layer 2: the scoped manager ------------------------------------------------


def test_querying_without_a_tenant_context_raises(tenant_a, make_agent):
    make_agent(tenant_a)
    with pytest.raises(TenantContextRequired):
        list(Agent.objects.all())


def test_every_tenant_scoped_model_raises_without_a_context(tenant_a):
    """Walks the app registry rather than naming models.

    A model added in a later phase without a scoped manager fails here, which is
    the only way this stays true as the schema grows.
    """
    from django.apps import apps

    from disputeshield.tenancy.managers import TenantScopedManager

    unprotected = []
    for model in apps.get_app_config("disputeshield").get_models():
        field_names = {f.name for f in model._meta.fields}
        if "tenant" not in field_names or model.__name__ in {"APIKey"}:
            continue
        if not isinstance(model._default_manager, TenantScopedManager):
            unprotected.append(model.__name__)

    assert not unprotected, (
        f"tenant-scoped models with an unscoped default manager: {unprotected}. "
        "A default manager that returns unscoped rows is a leak waiting for a "
        "forgotten filter (§8.1 layer 2)."
    )


def test_a_scoped_query_returns_only_the_active_tenants_rows(
    tenant_a, tenant_b, make_agent, as_tenant
):
    make_agent(tenant_a, email="a@example.com")
    make_agent(tenant_b, email="b@example.com")

    with as_tenant(tenant_a):
        assert [a.email for a in Agent.objects.all()] == ["a@example.com"]
    with as_tenant(tenant_b):
        assert [a.email for a in Agent.objects.all()] == ["b@example.com"]


def test_tenant_b_cannot_fetch_tenant_as_record_by_primary_key(
    tenant_a, tenant_b, make_agent, as_tenant
):
    agent = make_agent(tenant_a, email="a@example.com")
    with as_tenant(tenant_b), pytest.raises(Agent.DoesNotExist):
        Agent.objects.get(pk=agent.pk)


# -- layer 3: row level security ------------------------------------------------


def _rows_visible_to_postgres(table: str) -> int:
    """Count rows via raw SQL. The ORM's filter is not in this path, so what
    comes back is what RLS alone permits."""
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed table names
        return cursor.fetchone()[0]


def test_rls_hides_other_tenants_rows_from_raw_sql(tenant_a, tenant_b, make_agent, as_tenant):
    make_agent(tenant_a, email="a@example.com")
    make_agent(tenant_b, email="b@example.com")

    with as_tenant(tenant_a):
        assert _rows_visible_to_postgres("disputeshield_agent") == 1
    with as_tenant(tenant_b):
        assert _rows_visible_to_postgres("disputeshield_agent") == 1


def test_rls_returns_nothing_when_no_tenant_context_is_set(tenant_a, make_agent):
    """The correct failure for an unscoped query is zero rows, not all rows."""
    make_agent(tenant_a)
    assert _rows_visible_to_postgres("disputeshield_agent") == 0


def test_rls_is_forced_so_it_applies_to_the_table_owner_too(raw_sql):
    """Plain ENABLE exempts the owner, and in every self-hosted compose install
    the application role *is* the owner. Without FORCE the layer looks installed
    and does nothing."""
    rows = raw_sql(
        """
        SELECT relname, relrowsecurity, relforcerowsecurity
        FROM pg_class
        WHERE relname IN (
            'disputeshield_agent', 'disputeshield_apikey', 'disputeshield_auditrecord'
        )
        """
    )
    for name, enabled, forced in rows:
        assert enabled, f"{name}: row level security is not enabled"
        assert forced, f"{name}: row level security is not FORCEd — the owner bypasses it"


def test_the_connecting_role_does_not_bypass_rls(raw_sql):
    ((bypasses, superuser),) = raw_sql(
        "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user"
    )
    assert not bypasses and not superuser, (
        "the application connects as a superuser or BYPASSRLS role; §8.1's third "
        "isolation layer is inert in this configuration"
    )


# -- the audit trail is scoped too ---------------------------------------------


def test_audit_records_do_not_cross_tenants(tenant_a, tenant_b, as_tenant):
    audit.append(
        tenant=tenant_a,
        event_type="dispute.created",
        subject_type="dispute",
        subject_id="dsp_a",
        actor_type="system",
    )
    audit.append(
        tenant=tenant_b,
        event_type="dispute.created",
        subject_type="dispute",
        subject_id="dsp_b",
        actor_type="system",
    )

    with as_tenant(tenant_a):
        assert [r.subject_id for r in AuditRecord.objects.all()] == ["dsp_a"]
    with as_tenant(tenant_b):
        assert [r.subject_id for r in AuditRecord.objects.all()] == ["dsp_b"]


def test_appending_for_another_tenant_inside_a_scoped_context_is_refused(
    tenant_a, tenant_b, as_tenant
):
    """Otherwise the append would move the transaction's RLS context, and every
    query after it in that request would read the wrong tenant's rows."""
    from disputeshield.audit.service import TenantMismatch

    with as_tenant(tenant_a), pytest.raises(TenantMismatch):
        audit.append(
            tenant=tenant_b,
            event_type="dispute.created",
            subject_type="dispute",
            subject_id="dsp_b",
            actor_type="system",
        )


def test_all_tenants_escapes_layer_2_but_not_layer_3(tenant_a, tenant_b, as_tenant):
    """Defence in depth, demonstrated rather than asserted.

    `all_tenants()` deliberately bypasses the scoped manager — platform code
    genuinely needs it, and it has an unmissable name so that using it is a
    decision rather than an inherited default. It does **not** bypass RLS, so the
    worst outcome of misusing it is still no rows, not every tenant's rows.

    That is the whole argument for having three layers instead of one: the layer
    that can be escaped by ordinary code is not the layer holding the guarantee.
    """
    audit.append(
        tenant=tenant_a, event_type="e", subject_type="s", subject_id="1", actor_type="system"
    )
    audit.append(
        tenant=tenant_b, event_type="e", subject_type="s", subject_id="2", actor_type="system"
    )
    assert context.get() is None

    # No manager filter, and no tenant context: Postgres still returns nothing.
    assert AuditRecord.objects.all_tenants().count() == 0

    # A platform sweep gets its rows by scoping to each tenant in turn, which is
    # the only supported way to read across tenants.
    with as_tenant(tenant_a):
        assert AuditRecord.objects.all_tenants().count() == 1
    with as_tenant(tenant_b):
        assert AuditRecord.objects.all_tenants().count() == 1
