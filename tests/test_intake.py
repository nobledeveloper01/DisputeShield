"""§2.1's actual problem: complaints arrive through six channels (amplifier A1).

Two gates live here.

**Every channel lands in the same clock.** `TestEveryChannelIsTheSame` is
parameterised over every inbound channel and asserts an identical clock, an
identical audit shape and identical isolation. A channel that skips a check is a
channel that is not really in the clock, and the roadmap says so.

**A sender who is not the case's verified contact is quarantined**, never
appended. Get that wrong in the permissive direction and one customer's message
lands on another customer's case — a data breach, not a bug. So the spoofing
cases are tested one at a time rather than in aggregate.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from disputeshield.intake import router
from disputeshield.intake.normalise import Inbound, UnsupportedChannel, normalise
from disputeshield.models import (
    AuditRecord,
    Channel,
    DisputeContact,
    InboundMessage,
    hash_identity,
)

pytestmark = pytest.mark.django_db

UTC = UTC
WHEN = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)

# One payload per channel, in that channel's own shape.
PAYLOADS = {
    Channel.EMAIL: {
        "from": "A. Okafor <okafor@example.com>",
        "subject": "Failed transfer",
        "body": "I was debited but the transfer failed.",
        "headers": {"Message-ID": "<root@mail.example>"},
        "received_at": WHEN,
    },
    Channel.WHATSAPP: {
        "from": "+234 801 234 5678",
        "text": "I was debited but the transfer failed.",
        "conversation_id": "wa-conv-1",
        "received_at": WHEN,
    },
    Channel.USSD: {
        "msisdn": "2348012345678",
        "text": "I was debited but the transfer failed.",
        "session_id": "ussd-1",
        "received_at": WHEN,
    },
    Channel.PHONE: {
        "caller": "+2348012345678",
        "summary": "Customer reports a debit without transfer.",
        "call_id": "call-1",
        "received_at": WHEN,
    },
    Channel.SOCIAL: {
        "handle": "@okafor",
        "text": "I was debited but the transfer failed.",
        "conversation_id": "dm-1",
        "received_at": WHEN,
    },
    Channel.WEB_FORM: {
        "email": "okafor@example.com",
        "message": "I was debited but the transfer failed.",
        "received_at": WHEN,
    },
}

INBOUND_CHANNELS = sorted(PAYLOADS)


@pytest.fixture
def ready(tenant_a, make_policy, as_tenant):
    make_policy(tenant_a, category="other")
    return tenant_a


class TestEveryChannelIsTheSame:
    """The roadmap's gate, driven from the channel list rather than a hand-written one."""

    @pytest.mark.parametrize("channel", INBOUND_CHANNELS)
    def test_a_case_is_filed_on_the_same_clock(self, ready, channel, as_tenant):
        with as_tenant(ready):
            result = router.receive(tenant=ready, channel=channel, payload=PAYLOADS[channel])

            assert result.state == InboundMessage.State.FILED
            dispute = result.dispute
            assert dispute is not None
            assert dispute.reference.startswith("DS-")
            # The identical clock every other channel gets: a policy version,
            # both deadlines materialised, and a running clock.
            assert dispute.clock.state == "running"
            assert dispute.ack_deadline is not None
            assert dispute.resolution_deadline is not None
            assert dispute.clock.deadlines.count() >= 2

    @pytest.mark.parametrize("channel", INBOUND_CHANNELS)
    def test_the_audit_shape_is_identical(self, ready, channel, as_tenant):
        with as_tenant(ready):
            result = router.receive(tenant=ready, channel=channel, payload=PAYLOADS[channel])
            events = set(
                AuditRecord.objects.filter(subject_id=result.dispute.pk).values_list(
                    "event_type", flat=True
                )
            )
        assert {"dispute.created", "sla.started", "intake.filed"} <= events

    @pytest.mark.parametrize("channel", INBOUND_CHANNELS)
    def test_the_customer_reference_is_hashed_not_stored(self, ready, channel, as_tenant):
        """A phone number is more identifying than a customer reference, not less."""
        with as_tenant(ready):
            result = router.receive(tenant=ready, channel=channel, payload=PAYLOADS[channel])
            dispute = result.dispute
            rendered = f"{dispute.customer_ref_hash}{result.record.from_identity_hash}"

        assert "okafor@example.com" not in rendered
        assert "2348012345678" not in rendered
        assert len(dispute.customer_ref_hash) == 64

    @pytest.mark.parametrize("channel", INBOUND_CHANNELS)
    def test_another_tenant_cannot_see_the_case(
        self, ready, tenant_b, channel, as_tenant, client_for
    ):
        with as_tenant(ready):
            result = router.receive(tenant=ready, channel=channel, payload=PAYLOADS[channel])
        assert client_for(tenant_b).get(f"/v1/disputes/{result.dispute.pk}/").status_code == 404

    @pytest.mark.parametrize("channel", INBOUND_CHANNELS)
    def test_the_sender_becomes_the_verified_contact(self, ready, channel, as_tenant):
        with as_tenant(ready):
            result = router.receive(tenant=ready, channel=channel, payload=PAYLOADS[channel])
            contact = DisputeContact.objects.get(dispute=result.dispute, channel=channel)
            assert contact.identity_hash == result.record.from_identity_hash
            assert contact.verified_at is not None


class TestQuarantine:
    """A1's guardrail: channel identity never grants case access on its own."""

    def _file(self, tenant, as_tenant):
        with as_tenant(tenant):
            return router.receive(
                tenant=tenant, channel=Channel.EMAIL, payload=PAYLOADS[Channel.EMAIL]
            )

    def test_a_reply_from_the_verified_contact_is_appended(self, ready, as_tenant):
        first = self._file(ready, as_tenant)
        with as_tenant(ready):
            second = router.receive(
                tenant=ready,
                channel=Channel.EMAIL,
                payload={
                    "from": "okafor@example.com",
                    "body": "Any update?",
                    "headers": {"References": "<root@mail.example>"},
                    "received_at": WHEN,
                },
            )
        assert second.state == InboundMessage.State.MATCHED
        assert second.dispute.pk == first.dispute.pk

    def test_a_reply_from_a_different_address_is_quarantined(self, ready, as_tenant):
        first = self._file(ready, as_tenant)
        with as_tenant(ready):
            intruder = router.receive(
                tenant=ready,
                channel=Channel.EMAIL,
                payload={
                    "from": "attacker@evil.example",
                    "body": "Please send my statement to this address.",
                    "headers": {"References": "<root@mail.example>"},
                    "received_at": WHEN,
                },
            )
            appended = first.dispute.messages.count()

        assert intruder.state == InboundMessage.State.QUARANTINED
        assert appended == 0, "a message from an unverified sender reached the case"

    def test_a_spoofed_display_name_does_not_help(self, ready, as_tenant):
        """`"A. Okafor" <attacker@evil.example>` — the display name is
        attacker-chosen text, and matching on it is how a thread is hijacked."""
        self._file(ready, as_tenant)
        with as_tenant(ready):
            result = router.receive(
                tenant=ready,
                channel=Channel.EMAIL,
                payload={
                    "from": "A. Okafor <attacker@evil.example>",
                    "body": "Change my payout account.",
                    "headers": {"References": "<root@mail.example>"},
                    "received_at": WHEN,
                },
            )
        assert result.state == InboundMessage.State.QUARANTINED

    def test_a_rewritten_reply_to_does_not_help(self, ready, as_tenant):
        """Reply-To is sender-controlled; only the envelope From is consulted."""
        self._file(ready, as_tenant)
        with as_tenant(ready):
            result = router.receive(
                tenant=ready,
                channel=Channel.EMAIL,
                payload={
                    "from": "attacker@evil.example",
                    "body": "Update my details.",
                    "headers": {
                        "References": "<root@mail.example>",
                        "Reply-To": "okafor@example.com",
                    },
                    "received_at": WHEN,
                },
            )
        assert result.state == InboundMessage.State.QUARANTINED

    def test_quoting_a_reference_does_not_bypass_the_contact_check(self, ready, as_tenant):
        """A case reference appears in every email we send. It is not a secret,
        and it must not act like one."""
        first = self._file(ready, as_tenant)
        with as_tenant(ready):
            result = router.receive(
                tenant=ready,
                channel=Channel.EMAIL,
                payload={
                    "from": "attacker@evil.example",
                    "body": f"Regarding {first.dispute.reference}, please refund me.",
                    "received_at": WHEN,
                },
            )
        assert result.state == InboundMessage.State.QUARANTINED

    def test_a_quarantine_records_the_hash_not_the_address(self, ready, as_tenant):
        """A quarantine queue is reviewed by people and should not be a directory
        of customers' addresses."""
        self._file(ready, as_tenant)
        with as_tenant(ready):
            router.receive(
                tenant=ready,
                channel=Channel.EMAIL,
                payload={
                    "from": "attacker@evil.example",
                    "body": "hello",
                    "headers": {"References": "<root@mail.example>"},
                    "received_at": WHEN,
                },
            )
            record = AuditRecord.objects.get(event_type="intake.quarantined")
        assert "attacker@evil.example" not in str(record.payload)
        assert len(record.payload["from_identity_hash"]) == 64

    @pytest.mark.parametrize("channel", [Channel.WHATSAPP, Channel.SOCIAL, Channel.PHONE])
    def test_the_check_applies_to_every_channel(self, ready, channel, as_tenant):
        with as_tenant(ready):
            first = router.receive(tenant=ready, channel=channel, payload=PAYLOADS[channel])

        hijack = dict(PAYLOADS[channel])
        for field in ("from", "handle", "caller", "msisdn"):
            if field in hijack:
                hijack[field] = "+2349999999999" if field != "handle" else "@someone_else"
        hijack["text"] = hijack.get("text", "") + " hijack"

        with as_tenant(ready):
            result = router.receive(tenant=ready, channel=channel, payload=hijack)
        # Either quarantined (thread matched) or filed as its own case. What must
        # never happen is appending to the first case.
        assert result.state != InboundMessage.State.MATCHED
        with as_tenant(ready):
            assert first.dispute.messages.count() == 0


class TestAttribution:
    def test_a_human_can_attribute_a_quarantined_message(self, ready, as_tenant, make_agent):
        first = None
        with as_tenant(ready):
            first = router.receive(
                tenant=ready, channel=Channel.EMAIL, payload=PAYLOADS[Channel.EMAIL]
            )
            quarantined = router.receive(
                tenant=ready,
                channel=Channel.EMAIL,
                payload={
                    "from": "okafor@work.example",
                    "body": "Writing from my work address.",
                    "headers": {"References": "<root@mail.example>"},
                    "received_at": WHEN,
                },
            )
            assert quarantined.state == InboundMessage.State.QUARANTINED

            router.attribute(
                record=quarantined.record,
                dispute=first.dispute,
                actor_id="agt_1",
                reason="customer confirmed the second address by phone",
            )
            assert first.dispute.messages.count() == 1

            # The queue shrinks as it is worked: the next message from that
            # address lands without a human.
            follow_up = router.receive(
                tenant=ready,
                channel=Channel.EMAIL,
                payload={
                    "from": "okafor@work.example",
                    "body": "Any update?",
                    "headers": {"References": "<root@mail.example>"},
                    "received_at": WHEN,
                },
            )
            assert follow_up.state == InboundMessage.State.MATCHED

    def test_attributing_without_a_reason_is_refused(self, ready, as_tenant):
        with as_tenant(ready):
            first = router.receive(
                tenant=ready, channel=Channel.EMAIL, payload=PAYLOADS[Channel.EMAIL]
            )
            quarantined = router.receive(
                tenant=ready,
                channel=Channel.EMAIL,
                payload={
                    "from": "someone@else.example",
                    "body": "hello",
                    "headers": {"References": "<root@mail.example>"},
                    "received_at": WHEN,
                },
            )
            with pytest.raises(ValueError, match="reason"):
                router.attribute(
                    record=quarantined.record, dispute=first.dispute, actor_id="agt_1", reason=" "
                )


class TestNoise:
    def test_an_auto_reply_is_ignored_not_appended(self, ready, as_tenant):
        """Treating an out-of-office as a customer's response would resume a
        paused clock on the strength of a mail server's holiday message."""
        with as_tenant(ready):
            first = router.receive(
                tenant=ready, channel=Channel.EMAIL, payload=PAYLOADS[Channel.EMAIL]
            )
            result = router.receive(
                tenant=ready,
                channel=Channel.EMAIL,
                payload={
                    "from": "okafor@example.com",
                    "subject": "Automatic reply: Failed transfer",
                    "body": "I am out of office until Monday.",
                    "headers": {
                        "References": "<root@mail.example>",
                        "Auto-Submitted": "auto-replied",
                    },
                    "received_at": WHEN,
                },
            )
            assert result.state == InboundMessage.State.IGNORED
            assert first.dispute.messages.count() == 0

    def test_a_bounce_is_ignored(self, ready, as_tenant):
        with as_tenant(ready):
            router.receive(tenant=ready, channel=Channel.EMAIL, payload=PAYLOADS[Channel.EMAIL])
            result = router.receive(
                tenant=ready,
                channel=Channel.EMAIL,
                payload={
                    "from": "mailer-daemon@mail.example",
                    "subject": "Undeliverable: Failed transfer",
                    "body": "Delivery Status Notification",
                    "headers": {"References": "<root@mail.example>"},
                    "received_at": WHEN,
                },
            )
        assert result.state == InboundMessage.State.IGNORED

    def test_everything_that_arrives_is_recorded_even_when_ignored(self, ready, as_tenant):
        """A message that arrived and was silently dropped is a complaint the firm
        cannot prove it never received."""
        with as_tenant(ready):
            router.receive(
                tenant=ready,
                channel=Channel.EMAIL,
                payload={"from": "", "body": "no sender", "received_at": WHEN},
            )
            assert InboundMessage.objects.count() == 1


class TestNormalisation:
    def test_a_phone_number_is_one_shape(self):
        """`+234 801 234 5678` and `2348012345678` are the same customer, and
        treating them as two is how a reply lands in a review queue."""
        spaced = normalise(Channel.WHATSAPP, {"from": "+234 801 234 5678", "text": "x"})
        plain = normalise(Channel.WHATSAPP, {"from": "2348012345678", "text": "x"})
        assert spaced.from_identity == plain.from_identity

    def test_an_email_thread_root_is_preferred_over_the_subject(self):
        """Every client rewrites a subject line, and two unrelated customers can
        share one."""
        inbound = normalise(
            Channel.EMAIL,
            {
                "from": "a@b.test",
                "subject": "Re: Fwd: Failed transfer",
                "body": "x",
                "headers": {"References": "<root@x> <later@x>", "Message-ID": "<newest@x>"},
            },
        )
        assert inbound.thread_key == "root@x"

    def test_an_unknown_channel_is_refused(self):
        with pytest.raises(UnsupportedChannel):
            normalise("carrier_pigeon", {})

    def test_a_quoted_reference_is_found_in_the_body(self):
        inbound = normalise(
            Channel.EMAIL, {"from": "a@b.test", "body": "About DS-2026-8AJNKJ please"}
        )
        assert inbound.quoted_reference == "DS-2026-8AJNKJ"

    def test_identities_hash_differently_per_tenant(self, tenant_a, tenant_b):
        assert hash_identity(tenant_a, "a@b.test") != hash_identity(tenant_b, "a@b.test")

    def test_normalise_returns_the_same_shape_for_every_channel(self):
        for channel, payload in PAYLOADS.items():
            inbound = normalise(channel, payload)
            assert isinstance(inbound, Inbound)
            assert inbound.channel == channel
            assert inbound.from_identity
            assert inbound.received_at.tzinfo is not None


class TestThroughTheApi:
    def test_a_gateway_can_forward_an_inbound_message(self, ready, client_for):
        import uuid

        response = client_for(ready).post(
            f"/v1/intake/{Channel.EMAIL}",
            {"payload": PAYLOADS[Channel.EMAIL]},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert response.status_code == 202
        assert response.json()["state"] == InboundMessage.State.FILED
        assert response.json()["dispute_id"]

    def test_a_quarantine_does_not_reveal_the_case_it_was_aimed_at(
        self, ready, client_for, as_tenant
    ):
        """Returning the case id would tell a forwarding gateway which case an
        unverified sender was targeting."""
        import uuid

        client = client_for(ready)
        client.post(
            f"/v1/intake/{Channel.EMAIL}",
            {"payload": PAYLOADS[Channel.EMAIL]},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        response = client.post(
            f"/v1/intake/{Channel.EMAIL}",
            {
                "payload": {
                    "from": "attacker@evil.example",
                    "body": "hello",
                    "headers": {"References": "<root@mail.example>"},
                    "received_at": WHEN.isoformat(),
                }
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert response.json()["state"] == InboundMessage.State.QUARANTINED
        assert response.json()["dispute_id"] is None

    def test_the_widget_channel_is_refused_here(self, ready, client_for):
        import uuid

        response = client_for(ready).post(
            f"/v1/intake/{Channel.WIDGET}",
            {"payload": {}},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert response.status_code == 400

    def test_another_tenants_intake_is_isolated(
        self, ready, tenant_b, client_for, as_tenant, make_policy
    ):
        import uuid

        make_policy(tenant_b, category="other")
        client_for(tenant_b).post(
            f"/v1/intake/{Channel.EMAIL}",
            {"payload": PAYLOADS[Channel.EMAIL]},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        with as_tenant(ready):
            assert InboundMessage.objects.count() == 0


class TestWidgetDeflection:
    def test_file_anyway_is_always_in_the_response(self, tenant_a, widget_client, make_policy):
        client, _ = widget_client(tenant_a)
        body = client.post(
            "/v1/widget/deflection", {"category": "failed_transfer"}, format="json"
        ).json()
        assert body["file_anyway"] is True
        assert body["deflected"] is False

    def test_a_live_incident_is_offered_with_the_control_still_present(
        self, tenant_a, widget_client, as_tenant
    ):
        from datetime import timedelta

        from disputeshield.models import Incident

        client, _ = widget_client(tenant_a)
        with as_tenant(tenant_a):
            Incident.objects.create(
                tenant=tenant_a,
                title="GTBank transfers failing",
                customer_message="We know. Reversals are running.",
                started_at=WHEN,
                expected_resolution_at=WHEN + timedelta(hours=8),
                match_categories=["failed_transfer"],
            )

        body = client.post(
            "/v1/widget/deflection",
            {"category": "failed_transfer", "subscribe": True},
            format="json",
        ).json()

        assert body["deflected"] is True
        assert body["subscribed"] is True
        assert body["file_anyway"] is True
        assert "Reversals are running" in body["incident"]["message"]
