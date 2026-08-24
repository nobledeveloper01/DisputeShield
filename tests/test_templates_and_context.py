"""Response templates, the context API, and the outbox dispatcher.

The gate here is the second half of §10's containment: the widget serializer
closes the *field* path from internal content to a customer, and the template
context closes the *substitution* path. A template is written by a compliance
officer in a dashboard, so "they would not do that" is not a control.
"""

from __future__ import annotations

import uuid

import pytest

from disputeshield.models import AuditRecord, NotificationOutbox, ResponseTemplate
from disputeshield.templates_engine import (
    ALLOWED_VARIABLES,
    context_for,
    render,
    validate,
)

pytestmark = pytest.mark.django_db


def idem() -> dict:
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class TestTemplateContainment:
    """Marked as a leakage gate: it protects the same guarantee."""

    pytestmark = pytest.mark.leakage

    def test_the_context_exposes_nothing_internal(self, tenant_a, make_dispute, as_tenant):
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            dispute.outcome_notes = "Agent believes this is a repeat claimant"
            dispute.breach_reason = "Beat scheduler stalled"
            dispute.save(update_fields=["outcome_notes", "breach_reason"])
            context = context_for(dispute, agent_name="Ngozi")

        assert set(context) <= ALLOWED_VARIABLES
        rendered = str(context)
        assert "repeat claimant" not in rendered
        assert "Beat scheduler" not in rendered
        assert dispute.customer_ref_hash not in rendered

    def test_a_template_cannot_walk_the_object_graph(self, tenant_a, make_dispute, as_tenant):
        """The reason this is a substitution and not a template engine: in a real
        one, `{{ dispute.tenant.api_keys.first.key_hash }}` renders."""
        dispute = make_dispute(tenant_a)
        hostile = "{{ dispute.tenant.api_keys.first.key_hash }} {{ outcome_notes }}"

        assert validate(hostile) == ("outcome_notes",)
        with as_tenant(tenant_a):
            result = render(hostile, context_for(dispute))

        assert "key_hash" not in result.body or result.body == hostile
        assert "outcome_notes" in result.unknown

    def test_an_unknown_variable_renders_the_placeholder_not_an_empty_string(self):
        """An empty string is how a customer receives "Dear ," and nobody notices
        until they do."""
        result = render("Dear {{ nonsense }},", {})
        assert result.body == "Dear {{ nonsense }},"
        assert result.unknown == ("nonsense",)

    def test_a_missing_value_is_reported_rather_than_blanked(
        self, tenant_a, make_dispute, as_tenant
    ):
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            context = context_for(dispute)
        result = render("Hello {{ customer_name }},", context)
        assert result.missing == ("customer_name",)
        assert "{{ customer_name }}" in result.body

    def test_a_value_is_always_rendered_as_a_string(self):
        """A value that renders as `<Dispute: …>` is a repr in a customer's inbox."""

        class Sneaky:
            def __str__(self):
                return "1234"

        assert render("{{ amount }}", {"amount": Sneaky()}).body == "1234"

    def test_the_api_refuses_to_save_a_template_with_an_unknown_variable(
        self, tenant_a, client_for
    ):
        from disputeshield.api.serializers_management import ResponseTemplateSerializer

        serializer = ResponseTemplateSerializer(
            data={"name": "leaky", "body": "Notes: {{ outcome_notes }}"}
        )
        assert not serializer.is_valid()
        assert "outcome_notes" in str(serializer.errors)


class TestRendering:
    def test_a_template_renders_against_a_case(self, tenant_a, make_dispute, client_for, as_tenant):
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            template = ResponseTemplate.objects.create(
                tenant=tenant_a,
                name="acknowledgement",
                body="Hello, your case {{ reference }} is {{ status }}.",
            )

        response = client_for(tenant_a).post(
            f"/v1/disputes/{dispute.pk}/render_template/",
            {"template_id": template.pk},
            format="json",
        )
        assert response.status_code == 200
        assert dispute.reference in response.json()["body"]

    def test_rendering_does_not_send_anything(self, tenant_a, make_dispute, client_for, as_tenant):
        """The agent stays the author of whatever actually goes out."""
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            template = ResponseTemplate.objects.create(
                tenant=tenant_a, name="ack", body="Case {{ reference }}."
            )

        client_for(tenant_a).post(
            f"/v1/disputes/{dispute.pk}/render_template/",
            {"template_id": template.pk},
            format="json",
        )
        with as_tenant(tenant_a):
            assert dispute.messages.count() == 0

    def test_another_tenants_template_is_404(
        self, tenant_a, tenant_b, make_dispute, client_for, as_tenant
    ):
        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_b):
            theirs = ResponseTemplate.objects.create(
                tenant=tenant_b, name="theirs", body="Case {{ reference }}."
            )

        response = client_for(tenant_a).post(
            f"/v1/disputes/{dispute.pk}/render_template/",
            {"template_id": theirs.pk},
            format="json",
        )
        assert response.status_code == 404


class TestTransactionContext:
    def test_context_is_pushed_and_listed(self, tenant_a, make_dispute, client_for):
        dispute = make_dispute(tenant_a)
        client = client_for(tenant_a)

        created = client.post(
            f"/v1/disputes/{dispute.pk}/context/",
            {
                "source": "ledger",
                "occurred_at": "2026-08-19T09:14:22Z",
                "summary": "Reversal queued",
                "detail": {"rail": "NIP", "attempt": 2},
            },
            format="json",
            **idem(),
        )
        assert created.status_code == 201

        listed = client.get(f"/v1/disputes/{dispute.pk}/context/").json()
        assert listed[0]["summary"] == "Reversal queued"
        assert listed[0]["detail"]["rail"] == "NIP"

    def test_pushing_context_is_audited(self, tenant_a, make_dispute, client_for, as_tenant):
        dispute = make_dispute(tenant_a)
        client_for(tenant_a).post(
            f"/v1/disputes/{dispute.pk}/context/",
            {"source": "ledger", "occurred_at": "2026-08-19T09:14:22Z", "summary": "Queued"},
            format="json",
            **idem(),
        )
        with as_tenant(tenant_a):
            assert AuditRecord.objects.filter(event_type="dispute.context_added").exists()

    def test_a_context_entry_cannot_be_rewritten(self, tenant_a, make_dispute, as_tenant):
        """A record of what was known at a moment. A correction is a new entry."""
        from disputeshield.disputes import service

        dispute = make_dispute(tenant_a)
        with as_tenant(tenant_a):
            entry = service.add_context(
                dispute=dispute,
                source="ledger",
                occurred_at=dispute.submitted_at,
                summary="Queued",
                detail={},
                actor_type="api_key",
                actor_id="key_1",
            )
            entry.summary = "Actually settled"
            with pytest.raises(PermissionError):
                entry.save()

    def test_another_tenants_case_is_404(self, tenant_a, tenant_b, make_dispute, client_for):
        theirs = make_dispute(tenant_a)
        response = client_for(tenant_b).post(
            f"/v1/disputes/{theirs.pk}/context/",
            {"source": "ledger", "occurred_at": "2026-08-19T09:14:22Z", "summary": "x"},
            format="json",
            **idem(),
        )
        assert response.status_code == 404


class TestOutboxDispatch:
    @pytest.fixture(autouse=True)
    def _clear_console(self):
        from disputeshield.notifications.dispatcher import ConsoleChannel

        ConsoleChannel.sent.clear()
        yield
        ConsoleChannel.sent.clear()

    def _queue(self, tenant, key="sla:clk_1:resolution"):
        return NotificationOutbox.objects.create(
            tenant=tenant,
            idempotency_key=key,
            event_type="sla.resolution",
            payload={"subject_id": "dsp_1", "due_at": "2026-08-19T17:00:00Z"},
        )

    def test_a_pending_notification_is_sent_once(self, tenant_a, as_tenant):
        from disputeshield.notifications.dispatcher import ConsoleChannel, dispatch

        with as_tenant(tenant_a):
            notification = self._queue(tenant_a)

        assert dispatch().sent == 1
        assert len(ConsoleChannel.sent) == 1

        # A second drain must not re-send: the first marked it sent.
        assert dispatch().sent == 0
        assert len(ConsoleChannel.sent) == 1

        with as_tenant(tenant_a):
            notification.refresh_from_db()
        assert notification.status == NotificationOutbox.Status.SENT
        assert notification.sent_at is not None

    def test_a_failing_channel_retries_and_then_parks(
        self, tenant_a, as_tenant, settings, monkeypatch
    ):
        """Parked, not dropped (§8.6 principle 2). A breach alert that vanishes
        after six failures is one nobody received and nobody can prove was owed.
        """
        from disputeshield.notifications import dispatcher

        class Broken(dispatcher.Channel):
            def send(self, notification):
                raise RuntimeError("provider is down")

        monkeypatch.setattr(dispatcher, "get_channel", lambda name: Broken())

        with as_tenant(tenant_a):
            notification = self._queue(tenant_a)

        for _ in range(dispatcher.MAX_ATTEMPTS - 1):
            assert dispatcher.dispatch().failed == 1
        assert dispatcher.dispatch().exhausted == 1

        with as_tenant(tenant_a):
            notification.refresh_from_db()
        assert notification.status == NotificationOutbox.Status.FAILED
        assert notification.attempts == dispatcher.MAX_ATTEMPTS
        assert "provider is down" in notification.last_error

    def test_one_bad_send_does_not_stop_the_drain(self, tenant_a, as_tenant, monkeypatch):
        from disputeshield.notifications import dispatcher

        class Fussy(dispatcher.Channel):
            def send(self, notification):
                if notification.idempotency_key.endswith("bad"):
                    raise RuntimeError("nope")

        monkeypatch.setattr(dispatcher, "get_channel", lambda name: Fussy())

        with as_tenant(tenant_a):
            self._queue(tenant_a, key="sla:clk_1:bad")
            self._queue(tenant_a, key="sla:clk_2:good")

        result = dispatcher.dispatch()
        assert result.sent == 1
        assert result.failed == 1

    def test_the_body_carries_no_case_content(self, tenant_a, as_tenant):
        """An SLA warning that quotes the customer's description puts that
        description into an inbox with its own retention."""
        from disputeshield.notifications.dispatcher import ConsoleChannel, dispatch

        with as_tenant(tenant_a):
            self._queue(tenant_a)
        dispatch()

        payload = ConsoleChannel.sent[0]["payload"]
        assert "description" not in payload
        assert "customer_ref" not in str(payload)
