"""The management API: authentication, the queue, writes, and idempotency."""

from __future__ import annotations

import uuid

import pytest

from disputeshield.models import Agent, AuditRecord, DisputeMessage
from disputeshield.models.dispute import Outcome, Status

pytestmark = pytest.mark.django_db


def idem() -> dict:
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class TestAuthentication:
    def test_an_unauthenticated_request_is_401(self, client_for, tenant_a):
        """A role-checking permission class replaces IsAuthenticated rather than
        adding to it, so this asserts the role classes require authentication
        themselves. Without it an anonymous request reaches the queryset, and only
        the scoped manager stands between it and the data."""
        from rest_framework.test import APIClient

        response = APIClient().get("/v1/disputes/")
        assert response.status_code == 401

    def test_a_malformed_key_is_401_not_404(self, tenant_a):
        """401 is a statement about the caller, not about a resource. Answering
        404 to a bad key would be actively unhelpful to the integrating engineer."""
        from rest_framework.test import APIClient

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer ds_live_nonsense")
        assert client.get("/v1/disputes/").status_code == 401

    def test_a_revoked_key_stops_working_immediately(self, tenant_a, api_key_for, as_tenant):
        from rest_framework.test import APIClient

        full, key = api_key_for(tenant_a)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {full}")
        assert client.get("/v1/disputes/").status_code == 200

        with as_tenant(tenant_a):
            key.revoke()
        assert client.get("/v1/disputes/").status_code == 401

    def test_an_unknown_prefix_and_a_wrong_secret_are_indistinguishable(
        self, tenant_a, api_key_for
    ):
        """Distinguishing them tells an attacker when a prefix is real."""
        from rest_framework.test import APIClient

        full, _ = api_key_for(tenant_a)
        wrong_secret = full[:16] + "x" * 30
        unknown_prefix = "ds_live_zzzzzzzz" + "y" * 30

        bodies = []
        for candidate in (wrong_secret, unknown_prefix):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {candidate}")
            response = client.get("/v1/disputes/")
            bodies.append((response.status_code, response.json()))

        assert bodies[0] == bodies[1]


class TestTheQueue:
    def test_the_default_order_is_most_at_risk_first(
        self, tenant_a, make_dispute, client_for, as_tenant
    ):
        """§3.2 B1. An agent who has to sort a queue to find urgent work is using
        a table, not a queue."""
        urgent = make_dispute(tenant_a, customer_ref="usr_1")
        later = make_dispute(tenant_a, customer_ref="usr_2")

        with as_tenant(tenant_a):
            from datetime import timedelta

            later.resolution_deadline = urgent.resolution_deadline + timedelta(hours=5)
            later.save(update_fields=["resolution_deadline"])

        response = client_for(tenant_a).get("/v1/disputes/")
        references = [row["reference"] for row in response.json()["results"]]
        assert references == [urgent.reference, later.reference]

    def test_breached_cases_are_pinned_to_the_top(
        self, tenant_a, make_dispute, client_for, as_tenant
    ):
        from datetime import timedelta

        breached = make_dispute(tenant_a, customer_ref="usr_1")
        fine = make_dispute(tenant_a, customer_ref="usr_2")

        with as_tenant(tenant_a):
            breached.breach_resolution = True
            # …and give it a *later* deadline, so only the pinning can explain
            # it coming first.
            breached.resolution_deadline = fine.resolution_deadline + timedelta(days=2)
            breached.save(update_fields=["breach_resolution", "resolution_deadline"])

        response = client_for(tenant_a).get("/v1/disputes/")
        assert response.json()["results"][0]["reference"] == breached.reference

    def test_filters_narrow_the_queue(self, tenant_a, make_dispute, client_for):
        make_dispute(tenant_a, customer_ref="usr_1", category="failed_transfer")
        make_dispute(tenant_a, customer_ref="usr_2", category="card_chargeback")

        client = client_for(tenant_a)
        assert len(client.get("/v1/disputes/?category=card_chargeback").json()["results"]) == 1
        assert len(client.get("/v1/disputes/?assigned_to=none").json()["results"]) == 2
        assert len(client.get("/v1/disputes/?status=submitted").json()["results"]) == 2

    def test_pagination_is_cursor_based(self, tenant_a, make_dispute, client_for):
        """Offset pagination over a queue that reorders itself skips cases —
        a defect on a queue whose purpose is that nothing is missed."""
        for n in range(3):
            make_dispute(tenant_a, customer_ref=f"usr_{n}")

        body = client_for(tenant_a).get("/v1/disputes/?limit=2").json()
        assert len(body["results"]) == 2
        assert body["next"] is not None
        assert "cursor=" in body["next"]


class TestWrites:
    def test_a_write_without_an_idempotency_key_is_refused(
        self, tenant_a, make_dispute, client_for
    ):
        """An optional key means the guarantee silently does not apply to the
        callers who most need it, and they have no way to tell."""
        dispute = make_dispute(tenant_a)
        response = client_for(tenant_a).post(
            f"/v1/disputes/{dispute.pk}/transition/", {"to": Status.ACKNOWLEDGED}, format="json"
        )
        assert response.status_code == 400
        assert "Idempotency-Key" in response.json()["error"]["message"]

    def test_a_replayed_write_returns_the_original_result(self, tenant_a, make_dispute, client_for):
        dispute = make_dispute(tenant_a)
        client = client_for(tenant_a)
        headers = idem()
        body = {"to": Status.ACKNOWLEDGED}

        first = client.post(
            f"/v1/disputes/{dispute.pk}/transition/", body, format="json", **headers
        )
        second = client.post(
            f"/v1/disputes/{dispute.pk}/transition/", body, format="json", **headers
        )

        assert first.status_code == 200
        assert second.json() == first.json()

    def test_reusing_a_key_with_a_different_body_is_a_conflict(
        self, tenant_a, make_dispute, client_for
    ):
        """Returning the first response would hide a client bug."""
        dispute = make_dispute(tenant_a)
        client = client_for(tenant_a)
        headers = idem()

        client.post(
            f"/v1/disputes/{dispute.pk}/transition/",
            {"to": Status.ACKNOWLEDGED},
            format="json",
            **headers,
        )
        second = client.post(
            f"/v1/disputes/{dispute.pk}/transition/",
            {"to": Status.INVESTIGATING},
            format="json",
            **headers,
        )
        assert second.status_code == 409

    def test_an_illegal_transition_is_409_and_names_the_legal_moves(
        self, tenant_a, make_dispute, client_for
    ):
        dispute = make_dispute(tenant_a)
        response = client_for(tenant_a).post(
            f"/v1/disputes/{dispute.pk}/transition/",
            {"to": Status.CLOSED},
            format="json",
            **idem(),
        )
        assert response.status_code == 409
        assert "may move to" in response.json()["error"]["message"]

    def test_pausing_through_the_api_requires_a_reason(self, tenant_a, make_dispute, client_for):
        dispute = make_dispute(tenant_a)
        client = client_for(tenant_a)
        for step in (Status.ACKNOWLEDGED, Status.INVESTIGATING):
            client.post(
                f"/v1/disputes/{dispute.pk}/transition/",
                {"to": step, "reason": "x"},
                format="json",
                **idem(),
            )

        response = client.post(f"/v1/disputes/{dispute.pk}/pause/", {}, format="json", **idem())
        assert response.status_code == 400

    def test_resolving_records_the_outcome_and_the_refund_amount(
        self, tenant_a, make_dispute, client_for, as_tenant
    ):
        dispute = make_dispute(tenant_a)
        client = client_for(tenant_a)
        for step in (Status.ACKNOWLEDGED, Status.INVESTIGATING):
            client.post(
                f"/v1/disputes/{dispute.pk}/transition/",
                {"to": step, "reason": "x"},
                format="json",
                **idem(),
            )

        response = client.post(
            f"/v1/disputes/{dispute.pk}/resolve/",
            {
                "outcome": Outcome.UPHELD,
                "notes": "Reversal confirmed.",
                "refund_amount_minor": 5_000_000,
            },
            format="json",
            **idem(),
        )

        assert response.status_code == 200
        with as_tenant(tenant_a):
            dispute.refresh_from_db()
        assert dispute.outcome == Outcome.UPHELD
        assert dispute.refund_amount_minor == 5_000_000

    def test_an_internal_note_is_stored_as_internal(
        self, tenant_a, make_dispute, client_for, as_tenant
    ):
        dispute = make_dispute(tenant_a)
        response = client_for(tenant_a).post(
            f"/v1/disputes/{dispute.pk}/messages/",
            {"body": "Check the provider log first", "visibility": "internal"},
            format="json",
            **idem(),
        )
        assert response.status_code == 201
        with as_tenant(tenant_a):
            assert dispute.messages.get().visibility == DisputeMessage.Visibility.INTERNAL

    def test_every_api_write_lands_in_the_audit_trail(
        self, tenant_a, make_dispute, client_for, as_tenant
    ):
        dispute = make_dispute(tenant_a)
        client_for(tenant_a).post(
            f"/v1/disputes/{dispute.pk}/transition/",
            {"to": Status.ACKNOWLEDGED},
            format="json",
            **idem(),
        )
        with as_tenant(tenant_a):
            assert AuditRecord.objects.filter(event_type="dispute.acknowledge").exists()


class TestRoles:
    def test_a_read_only_agent_cannot_move_a_case(
        self, tenant_a, make_dispute, make_agent, client_for
    ):
        """§6.5's separation of duties, made real rather than documented."""
        dispute = make_dispute(tenant_a)
        reader = make_agent(tenant_a, email="reader@example.com", role=Agent.Role.READ_ONLY)

        response = client_for(tenant_a, agent=reader).post(
            f"/v1/disputes/{dispute.pk}/transition/",
            {"to": Status.ACKNOWLEDGED},
            format="json",
            **idem(),
        )
        # 404, not 403 — a 403 confirms the endpoint is real (D8).
        assert response.status_code == 404

    def test_a_read_only_agent_can_still_read_the_queue(
        self, tenant_a, make_dispute, make_agent, client_for
    ):
        make_dispute(tenant_a)
        reader = make_agent(tenant_a, email="reader@example.com", role=Agent.Role.READ_ONLY)
        assert client_for(tenant_a, agent=reader).get("/v1/disputes/").status_code == 200

    def test_the_acting_agent_is_recorded_rather_than_the_key(
        self, tenant_a, make_dispute, make_agent, client_for, as_tenant
    ):
        """ "An API key resolved this case" is not an answer a supervisor accepts."""
        dispute = make_dispute(tenant_a)
        ngozi = make_agent(tenant_a, email="ngozi@example.com", role=Agent.Role.AGENT)

        client_for(tenant_a, agent=ngozi).post(
            f"/v1/disputes/{dispute.pk}/transition/",
            {"to": Status.ACKNOWLEDGED},
            format="json",
            **idem(),
        )
        with as_tenant(tenant_a):
            record = AuditRecord.objects.get(event_type="dispute.acknowledge")
        assert record.actor_type == "user"
        assert record.actor_id == ngozi.pk
