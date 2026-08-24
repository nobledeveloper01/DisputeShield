"""Widget configuration (§7.3): theming, categories and embed origins.

The assertion this file exists for is the cross-check: a category the widget
offers with no SLA policy behind it lets a customer choose it and then fails
their filing with "Unknown category". Nothing in the data model prevents that
combination, so it has to be reported.
"""

from __future__ import annotations

import pytest

from disputeshield.models import Agent, AllowedOrigin, WidgetConfig

pytestmark = pytest.mark.django_db


@pytest.fixture
def clients(client_for, make_agent):
    """One client per (tenant, role), cached.

    Created twice for the same tenant and role this trips the per-tenant email
    constraint, which surfaces as a TransactionManagementError several queries
    later and reads like a bug in the code under test.
    """
    made = {}

    def _make(tenant, role):
        key = (tenant.pk, role)
        if key not in made:
            agent = make_agent(tenant, email=f"{role}@example.com", role=role)
            made[key] = client_for(tenant, agent=agent)
        return made[key]

    return _make


@pytest.fixture
def a_configured_tenant(tenant_a, make_policy, as_tenant):
    make_policy(tenant_a, category="failed_transfer")
    with as_tenant(tenant_a):
        WidgetConfig.objects.create(
            tenant=tenant_a,
            primary_colour="#0B5FFF",
            categories=["failed_transfer", "duplicate_charge"],
        )
        AllowedOrigin.objects.create(tenant=tenant_a, origin="https://app.acme.test")
    return tenant_a


class TestTheCategoryCrossCheck:
    def test_a_category_with_no_policy_is_reported(self, a_configured_tenant, clients):
        """The customer picks it, then cannot file under it.

        `duplicate_charge` is offered by the widget and has no policy, so
        `POST /v1/widget/disputes/` answers "Unknown category" — after the
        customer has already chosen it.
        """
        body = clients(a_configured_tenant, Agent.Role.OWNER).get("/v1/widget-config").json()
        by_name = {entry["name"]: entry for entry in body["categories"]}

        assert by_name["failed_transfer"]["has_policy"] is True
        assert by_name["duplicate_charge"]["has_policy"] is False

    def test_the_flag_follows_the_policy_rather_than_a_snapshot(
        self, a_configured_tenant, clients, make_policy
    ):
        client = clients(a_configured_tenant, Agent.Role.OWNER)
        make_policy(a_configured_tenant, category="duplicate_charge")

        body = client.get("/v1/widget-config").json()
        assert all(entry["has_policy"] for entry in body["categories"])

    def test_a_policy_with_no_widget_category_is_reported_not_flagged(
        self, a_configured_tenant, clients, make_policy
    ):
        """A channel-only category is a normal arrangement, not a fault."""
        make_policy(a_configured_tenant, category="atm_dispense_error")
        body = clients(a_configured_tenant, Agent.Role.OWNER).get("/v1/widget-config").json()
        assert "atm_dispense_error" in body["policies_not_offered"]


class TestOrigins:
    def test_an_origin_with_a_path_is_refused_with_the_consequence(
        self, a_configured_tenant, clients
    ):
        response = clients(a_configured_tenant, Agent.Role.OWNER).post(
            "/v1/widget-config/origins",
            {"origin": "https://app.acme.test/checkout"},
            format="json",
        )
        assert response.status_code == 400
        assert "authorise the whole host" in response.json()["error"]["message"]

    def test_a_wildcard_is_refused(self, a_configured_tenant, clients):
        response = clients(a_configured_tenant, Agent.Role.OWNER).post(
            "/v1/widget-config/origins", {"origin": "https://*.acme.test"}, format="json"
        )
        assert response.status_code == 400
        assert "wildcard" in response.json()["error"]["message"].lower()

    def test_only_an_owner_may_widen_the_boundary(self, a_configured_tenant, clients):
        """`frame-ancestors` is what makes a leaked publishable key harmless.

        A compliance officer is trusted with the regulatory export and is still
        not the right person to decide who may embed the product.
        """
        for role in (Agent.Role.COMPLIANCE, Agent.Role.AGENT):
            response = clients(a_configured_tenant, role).post(
                "/v1/widget-config/origins", {"origin": "https://evil.test"}, format="json"
            )
            # 404, never 403.
            assert response.status_code == 404, role

        assert not AllowedOrigin.objects.all_tenants().filter(origin="https://evil.test").exists()

    def test_withdrawing_an_origin_is_audited_before_the_row_goes(
        self, a_configured_tenant, clients, as_tenant
    ):
        from disputeshield.models import AuditRecord

        client = clients(a_configured_tenant, Agent.Role.OWNER)
        with as_tenant(a_configured_tenant):
            row = AllowedOrigin.objects.get(origin="https://app.acme.test")

        assert client.delete(f"/v1/widget-config/origins/{row.pk}").status_code == 204

        with as_tenant(a_configured_tenant):
            assert not AllowedOrigin.objects.filter(pk=row.pk).exists()
            record = AuditRecord.objects.get(event_type="widget.origin_withdrawn")
        assert record.payload["origin"] == "https://app.acme.test"

    def test_another_tenants_origin_cannot_be_withdrawn(
        self, a_configured_tenant, tenant_b, clients, as_tenant
    ):
        with as_tenant(a_configured_tenant):
            row = AllowedOrigin.objects.get(origin="https://app.acme.test")

        response = clients(tenant_b, Agent.Role.OWNER).delete(f"/v1/widget-config/origins/{row.pk}")
        assert response.status_code == 404
        with as_tenant(a_configured_tenant):
            assert AllowedOrigin.objects.filter(pk=row.pk).exists()


class TestTheming:
    def test_a_colour_the_browser_cannot_parse_is_refused(self, a_configured_tenant, clients):
        response = clients(a_configured_tenant, Agent.Role.COMPLIANCE).patch(
            "/v1/widget-config", {"primary_colour": "cornflower"}, format="json"
        )
        assert response.status_code == 400
        assert "not a hex colour" in response.json()["error"]["message"]

    def test_a_change_is_audited_with_what_moved(self, a_configured_tenant, clients, as_tenant):
        from disputeshield.models import AuditRecord

        clients(a_configured_tenant, Agent.Role.COMPLIANCE).patch(
            "/v1/widget-config", {"primary_colour": "#112233"}, format="json"
        )
        with as_tenant(a_configured_tenant):
            record = AuditRecord.objects.get(event_type="widget.configured")
        assert record.payload["changed"]["primary_colour"] == ["#0B5FFF", "#112233"]

    def test_an_unchanged_patch_writes_no_audit_record(
        self, a_configured_tenant, clients, as_tenant
    ):
        from disputeshield.models import AuditRecord

        clients(a_configured_tenant, Agent.Role.COMPLIANCE).patch(
            "/v1/widget-config", {"primary_colour": "#0B5FFF"}, format="json"
        )
        with as_tenant(a_configured_tenant):
            assert not AuditRecord.objects.filter(event_type="widget.configured").exists()

    def test_an_agent_can_read_the_config_but_not_change_it(self, a_configured_tenant, clients):
        client = clients(a_configured_tenant, Agent.Role.AGENT)
        assert client.get("/v1/widget-config").status_code == 200
        assert (
            client.patch(
                "/v1/widget-config", {"primary_colour": "#000000"}, format="json"
            ).status_code
            == 404
        )

    def test_the_frame_ancestors_header_is_shown_as_it_will_be_sent(
        self, a_configured_tenant, clients
    ):
        """The one line that decides where the widget renders, quoted verbatim.

        A screen that lists origins but paraphrases the header leaves an operator
        guessing at the thing §11.6 says causes the most support tickets.
        """
        body = clients(a_configured_tenant, Agent.Role.OWNER).get("/v1/widget-config").json()
        assert "https://app.acme.test" in body["frame_ancestors"]
