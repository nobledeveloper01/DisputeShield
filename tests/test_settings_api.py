"""Settings: API keys, the team and the retention position (§8.2, §11.7).

Two of these have no recovery path if they go wrong, so they are asserted
rather than reviewed: a key value that could be retrieved after creation, and a
tenant left with no active owner.

Every key value in this file is a `test`-environment key. A `live`-shaped value
committed to this repository would be caught by the gitleaks rule added in phase
4 — and should be, which is why the fixtures do not go near one.
"""

from __future__ import annotations

import pytest

from disputeshield.models import Agent, APIKey

pytestmark = pytest.mark.django_db


@pytest.fixture
def clients(client_for, make_agent):
    """One client per (tenant, role), cached — creating the agent twice trips the
    per-tenant email constraint and surfaces far from the cause."""
    made = {}

    def _make(tenant, role):
        key = (tenant.pk, role)
        if key not in made:
            agent = make_agent(tenant, email=f"{role}@example.com", role=role)
            made[key] = client_for(tenant, agent=agent)
        return made[key]

    return _make


@pytest.fixture
def owner(clients, tenant_a):
    return clients(tenant_a, Agent.Role.OWNER)


class TestAPIKeys:
    def test_the_value_is_returned_once_and_never_again(self, tenant_a, owner):
        """Only an Argon2id hash is stored, so there is nothing to show later."""
        created = owner.post(
            "/v1/api-keys", {"name": "CI", "environment": "test"}, format="json"
        ).json()

        assert created["key"].startswith("ds_test_")
        assert created["shown_once"] is True

        listed = owner.get("/v1/api-keys").json()["data"]
        row = next(entry for entry in listed if entry["id"] == created["id"])
        assert "key" not in row
        assert row["prefix"] == created["prefix"]

    def test_the_audit_record_carries_the_prefix_and_not_the_key(self, tenant_a, owner, as_tenant):
        """An audit trail that records credentials is a credential store with a
        seven-year retention policy attached."""
        from disputeshield.models import AuditRecord

        created = owner.post(
            "/v1/api-keys", {"name": "CI", "environment": "test"}, format="json"
        ).json()

        with as_tenant(tenant_a):
            record = AuditRecord.objects.get(event_type="api_key.created")

        assert record.payload["prefix"] == created["prefix"]
        assert created["key"] not in str(record.payload)

    def test_the_stored_hash_is_not_the_key(self, tenant_a, owner, as_tenant):
        created = owner.post(
            "/v1/api-keys", {"name": "CI", "environment": "test"}, format="json"
        ).json()
        with as_tenant(tenant_a):
            row = APIKey.objects.get(pk=created["id"])
        assert row.key_hash != created["key"]
        assert created["key"] not in row.key_hash

    def test_a_revoked_key_stops_authenticating(self, tenant_a, owner, as_tenant):
        from rest_framework.test import APIClient

        created = owner.post(
            "/v1/api-keys", {"name": "CI", "environment": "test"}, format="json"
        ).json()

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {created['key']}")
        assert client.get("/v1/disputes/").status_code == 200

        owner.delete(f"/v1/api-keys/{created['id']}")

        assert client.get("/v1/disputes/").status_code == 401

    def test_an_unnamed_key_is_refused(self, owner):
        response = owner.post("/v1/api-keys", {"environment": "test"}, format="json")
        assert response.status_code == 400
        assert "nobody can safely revoke" in response.json()["error"]["message"]

    def test_only_an_owner_may_mint_or_revoke(self, tenant_a, clients, owner):
        created = owner.post(
            "/v1/api-keys", {"name": "CI", "environment": "test"}, format="json"
        ).json()

        for role in (Agent.Role.COMPLIANCE, Agent.Role.AGENT):
            client = clients(tenant_a, role)
            # Readable — a compliance officer should be able to see what exists.
            assert client.get("/v1/api-keys").status_code == 200
            # 404, never 403.
            assert (
                client.post(
                    "/v1/api-keys", {"name": "x", "environment": "test"}, format="json"
                ).status_code
                == 404
            )
            assert client.delete(f"/v1/api-keys/{created['id']}").status_code == 404

    def test_another_tenants_key_cannot_be_revoked(self, tenant_a, tenant_b, clients, owner):
        created = owner.post(
            "/v1/api-keys", {"name": "CI", "environment": "test"}, format="json"
        ).json()
        response = clients(tenant_b, Agent.Role.OWNER).delete(f"/v1/api-keys/{created['id']}")
        assert response.status_code == 404


class TestTheTeam:
    def test_demoting_the_last_owner_is_refused(self, tenant_a, owner, as_tenant):
        """Refused, though not by the rule you might expect.

        Through the API the two guards overlap completely: if the actor is an
        active owner and the target is a *different* active owner, there are two
        owners and the target is not the last one. So the only reachable way to
        demote the last owner is to demote yourself, and the self-role rule fires
        first. What matters to a caller is that it is refused and the role does
        not move; which sentence comes back is an implementation detail.
        """
        with as_tenant(tenant_a):
            me = Agent.objects.get(role=Agent.Role.OWNER)

        response = owner.patch(f"/v1/agents/{me.pk}", {"role": Agent.Role.AGENT}, format="json")

        assert response.status_code == 400
        with as_tenant(tenant_a):
            me.refresh_from_db()
        assert me.role == Agent.Role.OWNER

    def test_the_last_owner_guard_holds_for_callers_that_are_not_the_api(self, tenant_a, as_tenant):
        """Defence in depth, asserted where it actually lives.

        The API cannot reach this branch — see the test above — but the view is
        one caller of several, and a management command or a future admin path
        would not pass through the self-role check at all. The invariant belongs
        to the rule, so it is tested there.
        """
        from disputeshield.api.views_settings import _would_strand_the_tenant

        with as_tenant(tenant_a):
            only_owner = Agent.objects.create(
                tenant=tenant_a,
                email="solo@example.com",
                display_name="Solo",
                role=Agent.Role.OWNER,
            )
            assert "only active owner" in _would_strand_the_tenant(
                only_owner, new_role=Agent.Role.AGENT
            )
            # Promoting an owner to owner strands nobody.
            assert _would_strand_the_tenant(only_owner, new_role=Agent.Role.OWNER) is None

            Agent.objects.create(
                tenant=tenant_a,
                email="second-owner@example.com",
                display_name="Second",
                role=Agent.Role.OWNER,
            )
            assert _would_strand_the_tenant(only_owner, new_role=Agent.Role.AGENT) is None

    def test_the_last_active_owner_cannot_be_deactivated(self, tenant_a, owner, as_tenant):
        with as_tenant(tenant_a):
            me = Agent.objects.get(role=Agent.Role.OWNER)

        response = owner.patch(f"/v1/agents/{me.pk}", {"is_active": False}, format="json")

        assert response.status_code == 400
        assert "no owner" in response.json()["error"]["message"]
        with as_tenant(tenant_a):
            me.refresh_from_db()
        assert me.is_active is True

    def test_a_second_owner_makes_the_first_removable(self, tenant_a, owner, as_tenant):
        with as_tenant(tenant_a):
            me = Agent.objects.get(role=Agent.Role.OWNER)
        owner.post(
            "/v1/agents",
            {"email": "second@example.com", "role": Agent.Role.OWNER},
            format="json",
        )

        response = owner.patch(f"/v1/agents/{me.pk}", {"is_active": False}, format="json")

        assert response.status_code == 200
        assert response.json()["is_active"] is False

    def test_nobody_changes_their_own_role(self, tenant_a, owner, as_tenant):
        """Self-promotion is the obvious reason; self-demotion is the likelier
        accident and produces the same locked-out tenant."""
        owner.post(
            "/v1/agents",
            {"email": "second@example.com", "role": Agent.Role.OWNER},
            format="json",
        )
        with as_tenant(tenant_a):
            me = Agent.objects.get(email="owner@example.com")

        response = owner.patch(
            f"/v1/agents/{me.pk}", {"role": Agent.Role.COMPLIANCE}, format="json"
        )

        assert response.status_code == 400
        assert "your own role" in response.json()["error"]["message"]

    def test_a_role_change_is_audited_with_what_moved(self, tenant_a, owner, as_tenant):
        from disputeshield.models import AuditRecord

        person = owner.post(
            "/v1/agents", {"email": "ngozi@example.com", "role": Agent.Role.AGENT}, format="json"
        ).json()
        owner.patch(f"/v1/agents/{person['id']}", {"role": Agent.Role.COMPLIANCE}, format="json")

        with as_tenant(tenant_a):
            record = AuditRecord.objects.get(event_type="team.member_changed")
        assert record.payload["changed"]["role"] == [Agent.Role.AGENT, Agent.Role.COMPLIANCE]

    def test_a_duplicate_address_is_refused(self, owner):
        owner.post("/v1/agents", {"email": "ngozi@example.com"}, format="json")
        response = owner.post("/v1/agents", {"email": "ngozi@example.com"}, format="json")
        assert response.status_code == 400
        assert "already on this team" in response.json()["error"]["message"]

    def test_only_an_owner_may_change_the_team(self, tenant_a, clients, owner):
        person = owner.post("/v1/agents", {"email": "ngozi@example.com"}, format="json").json()

        for role in (Agent.Role.COMPLIANCE, Agent.Role.AGENT):
            client = clients(tenant_a, role)
            assert client.get("/v1/agents").status_code == 200
            assert (
                client.patch(
                    f"/v1/agents/{person['id']}", {"role": Agent.Role.OWNER}, format="json"
                ).status_code
                == 404
            )

    def test_another_tenants_member_is_invisible(self, tenant_a, tenant_b, clients, owner):
        person = owner.post("/v1/agents", {"email": "ngozi@example.com"}, format="json").json()
        response = clients(tenant_b, Agent.Role.OWNER).patch(
            f"/v1/agents/{person['id']}", {"role": Agent.Role.OWNER}, format="json"
        )
        assert response.status_code == 404


class TestRetention:
    def test_it_reports_the_window_rather_than_offering_to_change_it(self, tenant_a, owner):
        """A tenant able to shorten its own retention below the mandated period
        would be using a settings screen to fall out of compliance."""
        body = owner.get("/v1/retention").json()

        assert body["years"] == 7
        assert body["deletes_only_when_told"] is True
        assert "cases_past_window" in body

    def test_there_is_no_way_to_write_a_retention_setting(self, owner):
        for method in (owner.post, owner.patch, owner.put):
            assert method("/v1/retention", {"years": 1}, format="json").status_code == 405

    def test_active_legal_holds_are_reported_beside_it(self, tenant_a, owner, as_tenant):
        """Retention and legal hold point in opposite directions, and the hold
        wins. A window shown without the holds against it is half the picture."""
        from disputeshield.models import LegalHold

        with as_tenant(tenant_a):
            LegalHold.objects.create(
                tenant=tenant_a,
                name="Regulatory investigation",
                matter_reference="MATTER-2026-01",
                scope=LegalHold.Scope.CUSTOMER,
                customer_ref_hash="abc123",
                placed_by="agt_1",
                reason="Regulatory investigation.",
            )

        assert owner.get("/v1/retention").json()["active_legal_holds"] == 1
