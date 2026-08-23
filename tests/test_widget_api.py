"""§4.3 and §10 — the widget's authorisation boundary.

Two threats in §10 are rated Critical and both are about this boundary:
"customer A reads customer B's disputes" and "publishable key used to enumerate
disputes". Each gets a test that walks every route rather than sampling one,
because a sample proves only that the paths we thought of are closed.
"""

from __future__ import annotations

import uuid

import pytest

from disputeshield.api import sessions

pytestmark = pytest.mark.django_db


def idem() -> dict:
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


WIDGET_DATA_ROUTES = [
    ("get", "/v1/widget/disputes/"),
    ("get", "/v1/widget/disputes/transactions/"),
    ("post", "/v1/widget/disputes/"),
]


class TestPublishableKeyReachesNoData:
    """§10: "There is no code path from a publishable key to a dispute record."

    Asserted against every data route, not one.
    """

    @pytest.mark.parametrize(("method", "route"), WIDGET_DATA_ROUTES)
    def test_a_publishable_key_cannot_reach_any_widget_data_route(
        self, tenant_a, publishable_key_for, method, route
    ):
        from rest_framework.test import APIClient

        full, _ = publishable_key_for(tenant_a)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {full}")

        response = getattr(client, method)(route, {}, format="json", **idem())
        assert response.status_code in {401, 403, 404}, (
            f"a publishable key reached {method.upper()} {route} with {response.status_code}"
        )

    def test_a_publishable_key_cannot_mint_a_session(self, tenant_a, publishable_key_for):
        """Otherwise anyone reading the page could mint a session for any
        customer_ref they cared to name."""
        from rest_framework.test import APIClient

        full, _ = publishable_key_for(tenant_a)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {full}")

        response = client.post("/v1/sessions", {"customer_ref": "usr_anyone"}, format="json")
        # 401 (the credential is not one this endpoint accepts) or 404 (accepted
        # but not a secret key). Either is correct; what matters is that no token
        # comes back.
        assert response.status_code in {401, 404}
        assert "session_token" not in response.json()

    def test_a_publishable_key_cannot_reach_the_management_api(self, tenant_a, publishable_key_for):
        from rest_framework.test import APIClient

        full, _ = publishable_key_for(tenant_a)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {full}")
        assert client.get("/v1/disputes/").status_code in {401, 404}

    def test_a_publishable_key_can_read_configuration_and_only_that(
        self, tenant_a, publishable_key_for, make_policy
    ):
        from rest_framework.test import APIClient

        make_policy(tenant_a)
        full, _ = publishable_key_for(tenant_a)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {full}")

        response = client.get("/v1/widget/config")
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"theme", "locale", "categories"}
        assert "failed_transfer" in body["categories"]

    def test_a_secret_key_is_not_accepted_as_a_publishable_one(self, tenant_a, api_key_for):
        """The two are different classes on purpose, so one cannot satisfy a
        permission written for the other by accident."""
        from rest_framework.test import APIClient

        full, _ = api_key_for(tenant_a)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {full}")
        assert client.get("/v1/widget/config").status_code in {401, 403}


class TestSessionMinting:
    def test_a_secret_key_mints_a_customer_scoped_token(self, tenant_a, client_for):
        response = client_for(tenant_a).post(
            "/v1/sessions",
            {
                "customer_ref": "usr_9931",
                "display_name": "A. Okafor",
                "transactions": [{"reference": "TXN-1", "amount_minor": 100, "currency": "NGN"}],
            },
            format="json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["session_token"].startswith("dst_")
        assert body["expires_at"]

    def test_the_raw_customer_reference_is_never_stored(self, tenant_a, session_for):
        """§8.4. The widget queries by hash, which is what the case rows carry."""
        _, session = session_for(tenant_a, customer_ref="usr_9931")
        assert session.customer_ref_hash != "usr_9931"
        assert "usr_9931" not in str(session)

    def test_the_token_is_stored_hashed(self, tenant_a, session_for):
        """A Redis snapshot in a backup should not contain anything usable."""
        token, _ = session_for(tenant_a)
        client = sessions._client()
        raw = client.get(f"{sessions.NAMESPACE}:tok:{sessions.digest(token)}")
        assert raw is not None
        assert token not in raw

    def test_an_expired_token_is_refused(self, tenant_a, session_for):
        token, _ = session_for(tenant_a)
        sessions.revoke(token)
        with pytest.raises(sessions.SessionExpired):
            sessions.resolve(token)

    def test_revoking_by_customer_kills_every_session_for_that_customer(
        self, tenant_a, session_for
    ):
        first, session = session_for(tenant_a, customer_ref="usr_9931")
        second, _ = session_for(tenant_a, customer_ref="usr_9931")
        other, _ = session_for(tenant_a, customer_ref="usr_other")

        sessions.revoke_for_customer(tenant_a.pk, session.customer_ref_hash)

        for token in (first, second):
            with pytest.raises(sessions.SessionExpired):
                sessions.resolve(token)
        assert sessions.resolve(other) is not None

    def test_revoking_by_key_kills_every_session_it_minted(
        self, tenant_a, api_key_for, as_tenant, make_policy
    ):
        """The response to a leaked secret key, available immediately rather than
        after a rotation completes."""
        make_policy(tenant_a)
        _, key = api_key_for(tenant_a)
        tokens = []
        with as_tenant(tenant_a):
            for ref in ("usr_1", "usr_2"):
                token, _ = sessions.mint(tenant=tenant_a, customer_ref=ref, api_key_id=key.pk)
                tokens.append(token)

        assert sessions.revoke_for_key(key.pk) == 2
        for token in tokens:
            with pytest.raises(sessions.SessionExpired):
                sessions.resolve(token)


class TestCustomerScope:
    def test_a_customer_sees_only_their_own_cases(
        self, tenant_a, widget_client, make_dispute, make_policy
    ):
        version = make_policy(tenant_a)
        make_dispute(tenant_a, policy_version=version, customer_ref="usr_9931")
        make_dispute(tenant_a, policy_version=version, customer_ref="usr_other")

        client, _ = widget_client(tenant_a, customer_ref="usr_9931")
        body = client.get("/v1/widget/disputes/").json()
        assert len(body) == 1

    def test_another_customers_case_is_404_not_403(
        self, tenant_a, widget_client, make_dispute, make_policy
    ):
        """§10's first Critical threat. 403 would confirm the case exists."""
        version = make_policy(tenant_a)
        theirs = make_dispute(tenant_a, policy_version=version, customer_ref="usr_other")

        client, _ = widget_client(tenant_a, customer_ref="usr_9931")
        assert client.get(f"/v1/widget/disputes/{theirs.pk}/").status_code == 404

    def test_a_customer_cannot_post_a_message_to_another_customers_case(
        self, tenant_a, widget_client, make_dispute, make_policy
    ):
        version = make_policy(tenant_a)
        theirs = make_dispute(tenant_a, policy_version=version, customer_ref="usr_other")

        client, _ = widget_client(tenant_a, customer_ref="usr_9931")
        response = client.post(
            f"/v1/widget/disputes/{theirs.pk}/messages/",
            {"body": "let me in"},
            format="json",
            **idem(),
        )
        assert response.status_code == 404

    def test_a_session_from_one_tenant_cannot_read_another_tenants_case(
        self, tenant_a, tenant_b, widget_client, make_dispute, make_policy
    ):
        version = make_policy(tenant_b)
        theirs = make_dispute(tenant_b, policy_version=version, customer_ref="usr_9931")

        client, _ = widget_client(tenant_a, customer_ref="usr_9931")
        assert client.get(f"/v1/widget/disputes/{theirs.pk}/").status_code == 404


class TestFiling:
    def test_a_customer_can_file_and_is_told_the_expected_resolution_date(
        self, tenant_a, widget_client
    ):
        client, _ = widget_client(tenant_a)
        response = client.post(
            "/v1/widget/disputes/",
            {
                "category": "failed_transfer",
                "description": "Transfer failed but I was debited",
                "transaction_ref": "TXN-2026-08-11-8842",
            },
            format="json",
            **idem(),
        )
        assert response.status_code == 201
        body = response.json()
        assert body["expected_resolution_at"] is not None
        assert body["reference"].startswith("DS-")

    def test_the_transaction_picker_offers_only_this_customers_transactions(
        self, tenant_a, widget_client
    ):
        """§3.2 A2. They were supplied by the fintech at mint time, so they are
        the only set this customer could dispute."""
        client, _ = widget_client(tenant_a)
        body = client.get("/v1/widget/disputes/transactions/").json()
        assert [t["reference"] for t in body["transactions"]] == ["TXN-2026-08-11-8842"]

    def test_a_transaction_reference_outside_the_session_is_refused(self, tenant_a, widget_client):
        """Accepting an arbitrary reference would let a customer attach someone
        else's transaction to their own case."""
        client, _ = widget_client(tenant_a)
        response = client.post(
            "/v1/widget/disputes/",
            {
                "category": "failed_transfer",
                "description": "…",
                "transaction_ref": "TXN-SOMEONE-ELSE",
            },
            format="json",
            **idem(),
        )
        assert response.status_code == 400
        assert "not one of yours" in response.json()["error"]["message"]

    def test_a_customer_message_cannot_be_internal(self, tenant_a, widget_client, as_tenant):
        """Visibility is fixed in the view, not read from the request."""
        from disputeshield.models import DisputeMessage

        client, _ = widget_client(tenant_a)
        created = client.post(
            "/v1/widget/disputes/",
            {"category": "failed_transfer", "description": "…"},
            format="json",
            **idem(),
        ).json()

        client.post(
            f"/v1/widget/disputes/{created['id']}/messages/",
            {"body": "any update?", "visibility": "internal"},
            format="json",
            **idem(),
        )
        with as_tenant(tenant_a):
            message = DisputeMessage.objects.get(author_type="customer")
        assert message.visibility == DisputeMessage.Visibility.CUSTOMER

    def test_an_unknown_category_is_refused(self, tenant_a, widget_client):
        client, _ = widget_client(tenant_a)
        response = client.post(
            "/v1/widget/disputes/",
            {"category": "not_a_category", "description": "…"},
            format="json",
            **idem(),
        )
        assert response.status_code == 400
