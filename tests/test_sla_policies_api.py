"""Publishing SLA policy terms (§7.3, ADR-0004).

The specification documents `PATCH /v1/sla-policies/{id}` and the architecture
says a policy's terms are immutable. Those point in different directions, and the
resolution is asserted here rather than left to a reader: a PATCH is accepted,
and its effect is to publish version n+1. The standard any case was judged
against stays exactly where it was.
"""

from __future__ import annotations

import pytest

from disputeshield.models import Agent, SLAPolicy, SLAPolicyVersion
from disputeshield.sla import policies

pytestmark = pytest.mark.django_db


@pytest.fixture
def compliance(client_for, make_agent):
    """One client per tenant, cached.

    Called twice for the same tenant this used to create the officer twice and
    trip the per-tenant email constraint — which surfaces as a
    TransactionManagementError several queries later and reads like a bug in the
    code under test.
    """
    clients = {}

    def _make(tenant):
        if tenant.pk not in clients:
            officer = make_agent(tenant, email="adaeze@example.com", role=Agent.Role.COMPLIANCE)
            clients[tenant.pk] = client_for(tenant, agent=officer)
        return clients[tenant.pk]

    return _make


@pytest.fixture
def a_policy(tenant_a, make_calendar, as_tenant):
    calendar = make_calendar(tenant_a)
    with as_tenant(tenant_a):
        published = policies.publish(
            tenant=tenant_a,
            category="failed_transfer",
            terms={
                "acknowledgement_minutes": 60,
                "resolution_hours": 72,
                "business_hours_only": True,
                "warning_thresholds": [50, 80, 95],
                "escalate_at_percent": 80,
                "auto_close_after_hours": 168,
                "reopen_window_hours": 336,
                "regulatory_reference": "CBN 2020 §3.1",
            },
            calendar=calendar,
            actor_id="agt_1",
        )
    return tenant_a, published.policy


class TestPublishing:
    def test_a_change_publishes_a_new_version_and_leaves_the_old_one(self, a_policy, as_tenant):
        tenant, policy = a_policy
        with as_tenant(tenant):
            policies.publish(
                tenant=tenant,
                category="failed_transfer",
                terms={
                    "resolution_hours": 168,
                    "acknowledgement_minutes": 60,
                    "warning_thresholds": [50, 80, 95],
                    "escalate_at_percent": 80,
                    "auto_close_after_hours": 168,
                    "reopen_window_hours": 336,
                },
                actor_id="agt_1",
            )
            versions = list(policy.versions.order_by("version"))

        assert [v.version for v in versions] == [1, 2]
        assert versions[0].resolution_hours == 72, "the old standard is untouched"
        assert versions[1].resolution_hours == 168

    def test_the_audit_record_says_what_changed_not_what_the_terms_are(self, a_policy, as_tenant):
        """ "The resolution window went from 72 to 168 hours on the 4th" is the
        sentence a supervisor needs, and two full snapshots do not contain it."""
        from disputeshield.models import AuditRecord

        tenant, _policy = a_policy
        with as_tenant(tenant):
            policies.publish(
                tenant=tenant,
                category="failed_transfer",
                terms={
                    "resolution_hours": 168,
                    "acknowledgement_minutes": 60,
                    "warning_thresholds": [50, 80, 95],
                    "escalate_at_percent": 80,
                    "auto_close_after_hours": 168,
                    "reopen_window_hours": 336,
                },
                actor_id="agt_1",
            )
            record = (
                AuditRecord.objects.filter(event_type="sla_policy.published")
                .order_by("-sequence")
                .first()
            )

        assert record.payload["changed"] == {"resolution_hours": [72, 168]}
        assert record.payload["version"] == 2

    def test_publishing_identical_terms_adds_no_version(self, a_policy, as_tenant):
        """A no-op version is a row a reviewer has to read and discard."""
        tenant, policy = a_policy
        with as_tenant(tenant):
            published = policies.publish(
                tenant=tenant,
                category="failed_transfer",
                terms={
                    "acknowledgement_minutes": 60,
                    "resolution_hours": 72,
                    "business_hours_only": True,
                    "warning_thresholds": [95, 50, 80],
                    "escalate_at_percent": 80,
                    "auto_close_after_hours": 168,
                    "reopen_window_hours": 336,
                    "regulatory_reference": "CBN 2020 §3.1",
                },
                actor_id="agt_1",
            )
            assert policy.versions.count() == 1
        assert published.changed == {}

    def test_a_version_cannot_be_edited_at_all(self, a_policy, as_tenant):
        tenant, policy = a_policy
        with as_tenant(tenant), pytest.raises(PermissionError, match="immutable"):
            version = policy.versions.first()
            version.resolution_hours = 1
            version.save()


class TestTermsThatCannotDescribeAWindow:
    @pytest.mark.parametrize(
        ("field", "value", "why"),
        [
            ("resolution_hours", 0, "breaches every case the moment it is filed"),
            ("acknowledgement_minutes", 0, "at least 1"),
            ("warning_thresholds", [50, 100], "never fires"),
            ("escalate_at_percent", 100, "already breached"),
            ("reopen_window_hours", 0, "at least 1"),
        ],
    )
    def test_it_is_refused_with_the_reason(self, field, value, why):
        terms = {
            "acknowledgement_minutes": 60,
            "resolution_hours": 72,
            "warning_thresholds": [50, 80],
            "escalate_at_percent": 80,
            "auto_close_after_hours": 168,
            "reopen_window_hours": 336,
        }
        terms[field] = value
        with pytest.raises(policies.InvalidTerms) as exc:
            policies.validate(terms)
        assert why in str(exc.value)

    def test_thresholds_are_sorted_and_deduplicated(self):
        cleaned = policies.validate(
            {
                "acknowledgement_minutes": 60,
                "resolution_hours": 72,
                "warning_thresholds": [80, 50, 80, 95],
                "escalate_at_percent": 80,
                "auto_close_after_hours": 168,
                "reopen_window_hours": 336,
            }
        )
        assert cleaned["warning_thresholds"] == [50, 80, 95]


class TestThroughTheApi:
    def test_a_patch_publishes_a_version_rather_than_editing(self, a_policy, compliance):
        tenant, policy = a_policy
        response = compliance(tenant).patch(
            f"/v1/sla-policies/{policy.pk}", {"resolution_hours": 168}, format="json"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["current"]["version"] == 2
        assert body["current"]["resolution_hours"] == 168
        assert body["changed"] == {"resolution_hours": [72, 168]}

    def test_a_patch_carries_forward_what_it_did_not_mention(self, a_policy, compliance):
        """A sparse PATCH must not publish a version whose other terms quietly
        became defaults."""
        tenant, policy = a_policy
        compliance(tenant).patch(
            f"/v1/sla-policies/{policy.pk}", {"resolution_hours": 168}, format="json"
        )
        body = compliance(tenant).get(f"/v1/sla-policies/{policy.pk}").json()

        assert body["current"]["regulatory_reference"] == "CBN 2020 §3.1"
        assert body["current"]["warning_thresholds"] == [50, 80, 95]

    def test_the_history_carries_what_changed_at_each_version(self, a_policy, compliance):
        tenant, policy = a_policy
        client = compliance(tenant)
        client.patch(f"/v1/sla-policies/{policy.pk}", {"resolution_hours": 168}, format="json")
        client.patch(f"/v1/sla-policies/{policy.pk}", {"escalate_at_percent": 60}, format="json")

        history = client.get(f"/v1/sla-policies/{policy.pk}").json()["history"]

        assert [entry["version"] for entry in history] == [3, 2, 1]
        assert history[0]["changed"] == {"escalate_at_percent": [80, 60]}
        assert history[1]["changed"] == {"resolution_hours": [72, 168]}

    def test_an_agent_can_read_a_policy_but_not_change_one(self, a_policy, client_for, make_agent):
        tenant, policy = a_policy
        agent = make_agent(tenant, email="ngozi@example.com", role=Agent.Role.AGENT)
        client = client_for(tenant, agent=agent)

        assert client.get("/v1/sla-policies").status_code == 200
        # 404, never 403: a 403 confirms the policy exists.
        assert (
            client.patch(
                f"/v1/sla-policies/{policy.pk}", {"resolution_hours": 1000}, format="json"
            ).status_code
            == 404
        )
        assert SLAPolicyVersion.objects.all_tenants().filter(resolution_hours=1000).count() == 0

    def test_invalid_terms_are_refused_with_the_reason(self, a_policy, compliance):
        tenant, policy = a_policy
        response = compliance(tenant).patch(
            f"/v1/sla-policies/{policy.pk}", {"resolution_hours": 0}, format="json"
        )
        assert response.status_code == 400
        assert "the moment it is filed" in response.json()["error"]["message"]

    def test_another_tenants_policy_is_not_visible(self, a_policy, tenant_b, compliance):
        _tenant, policy = a_policy
        response = compliance(tenant_b).get(f"/v1/sla-policies/{policy.pk}")
        assert response.status_code == 404

    def test_creating_a_policy_for_a_new_category(self, a_policy, compliance):
        tenant, _policy = a_policy
        response = compliance(tenant).post(
            "/v1/sla-policies",
            {
                "category": "duplicate_charge",
                "acknowledgement_minutes": 30,
                "resolution_hours": 48,
                "warning_thresholds": [50, 90],
                "escalate_at_percent": 75,
                "auto_close_after_hours": 168,
                "reopen_window_hours": 336,
            },
            format="json",
        )

        assert response.status_code == 201
        assert response.json()["current"]["version"] == 1
        assert SLAPolicy.objects.all_tenants().filter(category="duplicate_charge").exists()
