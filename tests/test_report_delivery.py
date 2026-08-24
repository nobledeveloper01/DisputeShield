"""Emailing a regulatory export (§6.5).

This is the one feature whose purpose is to move every case in a period outside
the system, so most of what is asserted here is what it refuses to do:

  * it will not send to an address nobody registered,
  * it will not send a bundle that no longer matches what was promised,
  * it will not quietly send to the subset of recipients it recognised,
  * and it will not mark a report delivered that never left.

Nothing in this file, and nothing in the seed command it exercises, uses an
address that can receive mail: `example.test` and `.invalid` are reserved by RFC
2606 and RFC 6761 precisely so a bug here cannot reach a real inbox.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime

import pytest
from django.core import mail

from disputeshield.models import Agent, NotificationOutbox, ReportRecipient
from disputeshield.notifications import dispatcher
from disputeshield.reports import delivery

pytestmark = pytest.mark.django_db

PERIOD_FROM = datetime(2026, 1, 1, tzinfo=UTC)
PERIOD_TO = datetime(2027, 1, 1, tzinfo=UTC)

ALLOWED = "compliance@example.test"
ALSO_ALLOWED = "supervision@example.test"
NEVER_REGISTERED = "attacker@example.invalid"


@pytest.fixture
def a_period_with_recipients(tenant_a, make_dispute, make_policy, as_tenant):
    version = make_policy(tenant_a, resolution_hours=8)
    cases = [
        make_dispute(tenant_a, policy_version=version, customer_ref=f"usr_{n}") for n in range(3)
    ]
    with as_tenant(tenant_a):
        for address, label in ((ALLOWED, "Compliance archive"), (ALSO_ALLOWED, "Supervision")):
            ReportRecipient.objects.create(
                tenant=tenant_a,
                address=address,
                label=label,
                added_by="agt_1",
                reason="Sample recipient for tests.",
            )
    return tenant_a, cases


@pytest.fixture
def a_test_tenant(tenant_a):
    """The seed command only runs on a non-live tenant, and `live` is the default."""
    from disputeshield.models import Tenant

    tenant_a.environment = Tenant.Environment.TEST
    tenant_a.save(update_fields=["environment"])
    return tenant_a


@pytest.fixture
def compliance_client(client_for, make_agent):
    def _make(tenant):
        officer = make_agent(tenant, email="adaeze@example.com", role=Agent.Role.COMPLIANCE)
        return client_for(tenant, agent=officer), officer

    return _make


class TestTheAllowlist:
    def test_an_unregistered_address_is_refused(self, a_period_with_recipients, as_tenant):
        tenant, _ = a_period_with_recipients
        with as_tenant(tenant), pytest.raises(delivery.UnknownRecipient) as exc:
            delivery.request_delivery(
                tenant=tenant,
                period_from=PERIOD_FROM,
                period_to=PERIOD_TO,
                addresses=[NEVER_REGISTERED],
                requested_by="agt_1",
            )
        assert NEVER_REGISTERED in str(exc.value)

    def test_one_bad_address_refuses_the_whole_request(self, a_period_with_recipients, as_tenant):
        """Not a partial send.

        A partial send is a supervisor waiting for a report that four of five
        people received, and nobody noticing for a week.
        """
        tenant, _ = a_period_with_recipients
        with as_tenant(tenant), pytest.raises(delivery.UnknownRecipient):
            delivery.request_delivery(
                tenant=tenant,
                period_from=PERIOD_FROM,
                period_to=PERIOD_TO,
                addresses=[ALLOWED, NEVER_REGISTERED],
                requested_by="agt_1",
            )
        assert (
            not NotificationOutbox.objects.all_tenants()
            .filter(event_type=delivery.EVENT_TYPE)
            .exists()
        )

    def test_a_deactivated_recipient_no_longer_receives(self, a_period_with_recipients, as_tenant):
        tenant, _ = a_period_with_recipients
        with as_tenant(tenant):
            recipient = ReportRecipient.objects.get(address=ALLOWED)
            recipient.is_active = False
            recipient.save(update_fields=["is_active"])

            with pytest.raises(delivery.UnknownRecipient):
                delivery.request_delivery(
                    tenant=tenant,
                    period_from=PERIOD_FROM,
                    period_to=PERIOD_TO,
                    addresses=[ALLOWED],
                    requested_by="agt_1",
                )

    def test_another_tenants_allowlist_is_not_visible(
        self, a_period_with_recipients, tenant_b, as_tenant
    ):
        """The table decides where a period's disclosure may be sent.

        A row readable across the boundary would let one tenant's allowlist
        authorise another tenant's export.
        """
        tenant, _ = a_period_with_recipients
        with as_tenant(tenant_b):
            assert not ReportRecipient.objects.filter(address=ALLOWED).exists()
        with as_tenant(tenant):
            assert ReportRecipient.objects.filter(address=ALLOWED).exists()


class TestQueueing:
    def test_a_request_queues_without_attaching_the_bundle(
        self, a_period_with_recipients, as_tenant
    ):
        """No case content sits in the outbox waiting to be sent."""
        tenant, _ = a_period_with_recipients
        with as_tenant(tenant):
            queued = delivery.request_delivery(
                tenant=tenant,
                period_from=PERIOD_FROM,
                period_to=PERIOD_TO,
                addresses=[ALLOWED],
                requested_by="agt_1",
            )
            row = NotificationOutbox.objects.get(pk=queued.notification_id)

        assert row.channel == delivery.CHANNEL
        assert set(row.payload["files"]) == {"cases.csv", "history.csv", "report.pdf"}
        serialised = str(row.payload)
        assert "usr_" not in serialised
        assert "DS-" not in serialised

    def test_asking_twice_sends_once(self, a_period_with_recipients, as_tenant):
        """A retried request during an incident cannot page a regulator twice."""
        tenant, _ = a_period_with_recipients
        with as_tenant(tenant):
            first = delivery.request_delivery(
                tenant=tenant,
                period_from=PERIOD_FROM,
                period_to=PERIOD_TO,
                addresses=[ALLOWED],
                requested_by="agt_1",
            )
            second = delivery.request_delivery(
                tenant=tenant,
                period_from=PERIOD_FROM,
                period_to=PERIOD_TO,
                addresses=[ALLOWED],
                requested_by="agt_1",
            )
            assert first.notification_id == second.notification_id
            assert NotificationOutbox.objects.filter(event_type=delivery.EVENT_TYPE).count() == 1

    def test_the_request_is_audited_before_anything_is_sent(
        self, a_period_with_recipients, as_tenant
    ):
        from disputeshield.models import AuditRecord

        tenant, _ = a_period_with_recipients
        with as_tenant(tenant):
            delivery.request_delivery(
                tenant=tenant,
                period_from=PERIOD_FROM,
                period_to=PERIOD_TO,
                addresses=[ALLOWED],
                requested_by="agt_1",
                note="Quarterly supervisory request.",
            )
            record = AuditRecord.objects.get(event_type="report.delivery_requested")

        assert record.payload["recipients"] == [ALLOWED]
        assert record.payload["note"] == "Quarterly supervisory request."
        assert record.actor_id == "agt_1"


class TestSending:
    def _queue_and_dispatch(self, tenant, as_tenant, addresses=(ALLOWED,)):
        with as_tenant(tenant):
            delivery.request_delivery(
                tenant=tenant,
                period_from=PERIOD_FROM,
                period_to=PERIOD_TO,
                addresses=list(addresses),
                requested_by="agt_1",
            )
        return dispatcher.dispatch()

    def test_the_export_arrives_as_an_attachment(self, a_period_with_recipients, as_tenant):
        tenant, _ = a_period_with_recipients
        result = self._queue_and_dispatch(tenant, as_tenant)

        assert result.sent == 1
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == [ALLOWED]

        name, content, content_type = message.attachments[0]
        assert name.endswith(".zip")
        assert content_type == "application/zip"
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            assert set(archive.namelist()) == {
                "cases.csv",
                "history.csv",
                "report.pdf",
                "manifest.json",
            }

    def test_the_body_publishes_the_digests_so_the_attachment_can_be_checked(
        self, a_period_with_recipients, as_tenant
    ):
        """A recipient who trusts the attachment because the email said so has
        verified nothing."""
        tenant, _ = a_period_with_recipients
        with as_tenant(tenant):
            queued = delivery.request_delivery(
                tenant=tenant,
                period_from=PERIOD_FROM,
                period_to=PERIOD_TO,
                addresses=[ALLOWED],
                requested_by="agt_1",
            )
        dispatcher.dispatch()

        body = mail.outbox[0].body
        for name, digest in queued.files.items():
            assert name in body
            assert digest in body

    def test_delivery_is_audited_only_after_it_succeeded(self, a_period_with_recipients, as_tenant):
        from disputeshield.models import AuditRecord

        tenant, _ = a_period_with_recipients
        self._queue_and_dispatch(tenant, as_tenant)
        with as_tenant(tenant):
            record = AuditRecord.objects.get(event_type="report.delivered")
        assert record.payload["recipients"] == [ALLOWED]

    def test_a_failed_send_is_not_recorded_as_delivered(
        self, a_period_with_recipients, as_tenant, monkeypatch
    ):
        """The audit trail must not contain a false statement about a disclosure."""
        from disputeshield.models import AuditRecord

        tenant, _ = a_period_with_recipients

        def refuse(*args, **kwargs):
            raise OSError("provider unavailable")

        monkeypatch.setattr("django.core.mail.EmailMessage.send", refuse)
        result = self._queue_and_dispatch(tenant, as_tenant)

        assert result.sent == 0
        assert not mail.outbox
        with as_tenant(tenant):
            assert not AuditRecord.objects.filter(event_type="report.delivered").exists()
            assert (
                NotificationOutbox.objects.get(event_type=delivery.EVENT_TYPE).status
                == NotificationOutbox.Status.PENDING
            )

    def test_a_changed_period_refuses_rather_than_sending(
        self, a_period_with_recipients, as_tenant, make_dispute, make_policy
    ):
        """The rebuild-and-verify, which is what makes storing no attachment safe.

        A supervisor who receives a bundle whose digests disagree with the ones
        they were promised has been handed a reason to doubt all of it.
        """
        tenant, _ = a_period_with_recipients
        with as_tenant(tenant):
            delivery.request_delivery(
                tenant=tenant,
                period_from=PERIOD_FROM,
                period_to=PERIOD_TO,
                addresses=[ALLOWED],
                requested_by="agt_1",
            )

        # A case filed after the request but inside the period.
        version = make_policy(tenant, resolution_hours=8)
        make_dispute(tenant, policy_version=version, customer_ref="usr_late")

        result = dispatcher.dispatch()

        assert result.sent == 0
        assert not mail.outbox
        with as_tenant(tenant):
            row = NotificationOutbox.objects.get(event_type=delivery.EVENT_TYPE)
        assert "no longer matches" in row.last_error

    def test_the_channel_is_never_the_silent_console_fallback(self):
        """An unconfigured report channel must not mark a report sent.

        Every other channel falls back to the console in development, which for
        an SLA warning is a convenience. Here it would be an audit record saying
        a period left the building when nothing did.
        """
        channel = dispatcher.get_channel(delivery.CHANNEL)
        assert isinstance(channel, dispatcher.ReportEmailChannel)
        assert not isinstance(channel, dispatcher.ConsoleChannel)


class TestThroughTheApi:
    def test_compliance_registers_a_recipient_then_sends(
        self, a_period_with_recipients, compliance_client
    ):
        tenant, _ = a_period_with_recipients
        client, _officer = compliance_client(tenant)

        registered = client.post(
            "/v1/reports/recipients",
            {
                "address": "returns@example.test",
                "label": "Returns inbox (sample)",
                "reason": "Quarterly supervisory returns.",
            },
            format="json",
        )
        assert registered.status_code == 201

        queued = client.post(
            "/v1/reports/regulatory/email",
            {"recipients": ["returns@example.test"], "note": "Q1"},
            format="json",
        )
        assert queued.status_code == 202
        assert queued.json()["recipients"] == ["returns@example.test"]

        dispatcher.dispatch()
        assert mail.outbox[0].to == ["returns@example.test"]

    def test_an_address_not_on_the_allowlist_is_refused_by_the_endpoint(
        self, a_period_with_recipients, compliance_client
    ):
        tenant, _ = a_period_with_recipients
        client, _officer = compliance_client(tenant)

        response = client.post(
            "/v1/reports/regulatory/email",
            {"recipients": [NEVER_REGISTERED]},
            format="json",
        )

        assert response.status_code == 400
        assert response.json()["error"]["type"] == "recipient_not_allowed"
        assert not mail.outbox

    def test_an_agent_cannot_send_a_period_anywhere(
        self, a_period_with_recipients, client_for, make_agent
    ):
        tenant, _ = a_period_with_recipients
        agent = make_agent(tenant, email="ngozi@example.com", role=Agent.Role.AGENT)

        response = client_for(tenant, agent=agent).post(
            "/v1/reports/regulatory/email", {"recipients": [ALLOWED]}, format="json"
        )

        assert response.status_code == 404
        assert not mail.outbox

    def test_an_agent_cannot_register_a_recipient(
        self, a_period_with_recipients, client_for, make_agent
    ):
        """The more dangerous half of the pair, and the reason they are separate."""
        tenant, _ = a_period_with_recipients
        agent = make_agent(tenant, email="ngozi@example.com", role=Agent.Role.AGENT)

        response = client_for(tenant, agent=agent).post(
            "/v1/reports/recipients",
            {"address": "x@example.test", "label": "x", "reason": "x"},
            format="json",
        )

        assert response.status_code == 404

    def test_registering_without_a_reason_is_refused(
        self, a_period_with_recipients, compliance_client
    ):
        tenant, _ = a_period_with_recipients
        client, _officer = compliance_client(tenant)

        response = client.post(
            "/v1/reports/recipients",
            {"address": "x@example.test", "label": "Somewhere"},
            format="json",
        )

        assert response.status_code == 400
        assert "reason" in response.json()["error"]["message"]

    def test_deactivating_keeps_the_row(self, a_period_with_recipients, compliance_client):
        """ "Who could receive our disputes data in March" stays answerable."""
        tenant, _ = a_period_with_recipients
        client, _officer = compliance_client(tenant)

        listed = client.get("/v1/reports/recipients").json()["data"]
        target = next(r for r in listed if r["address"] == ALLOWED)

        response = client.delete(f"/v1/reports/recipients/{target['id']}")

        assert response.status_code == 200
        assert response.json()["is_active"] is False
        assert any(
            r["address"] == ALLOWED for r in client.get("/v1/reports/recipients").json()["data"]
        )


class TestTheSeedCommand:
    def test_it_registers_only_unroutable_addresses(self, a_test_tenant, as_tenant):
        """A bug in delivery must not be able to reach a real inbox."""
        from django.core.management import call_command

        tenant_a = a_test_tenant
        call_command("disputeshield_seed_report_recipients", tenant=tenant_a.slug)

        with as_tenant(tenant_a):
            addresses = list(ReportRecipient.objects.values_list("address", flat=True))

        assert addresses
        for address in addresses:
            domain = address.split("@")[1]
            assert domain.endswith((".test", ".invalid")), address

    def test_it_refuses_a_live_tenant(self, tenant_a):
        """`live` is the default, so this is the state a careless operator is in."""
        from django.core.management import call_command
        from django.core.management.base import CommandError

        from disputeshield.models import Tenant

        tenant_a.environment = Tenant.Environment.LIVE
        tenant_a.save(update_fields=["environment"])

        with pytest.raises(CommandError, match="live tenant"):
            call_command("disputeshield_seed_report_recipients", tenant=tenant_a.slug)

    def test_running_it_twice_registers_nothing_new(self, a_test_tenant, as_tenant):
        from django.core.management import call_command

        tenant_a = a_test_tenant
        call_command("disputeshield_seed_report_recipients", tenant=tenant_a.slug)
        call_command("disputeshield_seed_report_recipients", tenant=tenant_a.slug)

        with as_tenant(tenant_a):
            assert ReportRecipient.objects.count() == 3


class TestTheDoctorCheck:
    """A configured allowlist with no way to reach it.

    The console and in-memory backends are right for development and wrong on an
    installation where somebody has registered a supervisor's address: delivery
    reports success, the audit trail records that a period left the building, and
    the report exists only in a log file.
    """

    def _report(self, **overrides) -> str:
        import io

        from django.core.management import call_command
        from django.test import override_settings

        out = io.StringIO()
        with override_settings(**overrides):
            call_command("disputeshield_doctor", stdout=out)
        return next(line for line in out.getvalue().splitlines() if "report email delivery" in line)

    def test_a_console_backend_with_no_recipients_is_fine(self, tenant_a):
        line = self._report(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
        assert line.startswith("ok")

    def test_a_console_backend_with_recipients_fails(self, a_period_with_recipients):
        line = self._report(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
        assert line.startswith("FAIL")
        assert "without leaving the machine" in line

    def test_a_real_backend_passes(self, a_period_with_recipients):
        line = self._report(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend")
        assert line.startswith("ok")
        assert "smtp" in line
