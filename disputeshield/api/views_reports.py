"""Analytics, the regulatory export, and the independent integrity check."""

from __future__ import annotations

from datetime import UTC, datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from disputeshield import audit
from disputeshield.api.mixins import ActingAgentMixin
from disputeshield.api.permissions import CanChangeCompliancePolicy, CanReadOnly
from disputeshield.audit.checkpoints import attestation
from disputeshield.models import ReportRecipient
from disputeshield.reports import analytics, delivery, regulatory

UTC = UTC


class PeriodMixin(ActingAgentMixin):
    def period(self, request) -> tuple[datetime, datetime]:
        return (
            _parse(request.query_params.get("from"), datetime(2000, 1, 1, tzinfo=UTC)),
            _parse(request.query_params.get("to"), datetime(2100, 1, 1, tzinfo=UTC)),
        )


class SLAPerformanceView(PeriodMixin, APIView):
    permission_classes = [CanReadOnly]

    def get(self, request):
        period_from, period_to = self.period(request)
        group_by = request.query_params.get("group_by", "category")
        try:
            rows = analytics.sla_performance(
                period_from=period_from, period_to=period_to, group_by=group_by
            )
        except ValueError as exc:
            return Response({"error": {"type": "invalid_request", "message": str(exc)}}, status=400)
        return Response(
            {
                "summary": analytics.summary(period_from=period_from, period_to=period_to),
                "group_by": group_by,
                "rows": rows,
                "causes": analytics.breach_causes(period_from=period_from, period_to=period_to),
            }
        )


FORMATS = frozenset({"zip", "json", "pdf", "csv"})


class RegulatoryReportView(PeriodMixin, APIView):
    """§6.5. Compliance-only: an export is a disclosure of the whole period."""

    permission_classes = [CanChangeCompliancePolicy]

    def get(self, request):
        period_from, period_to = self.period(request)
        export = regulatory.build(
            tenant=request.user.tenant, period_from=period_from, period_to=period_to
        )

        requested = request.query_params.get("format", "zip")
        if requested not in FORMATS:
            # An unrecognised format used to fall through to the zip, which meant
            # a typo returned a whole period's disclosure in a shape the caller
            # never asked for and would not notice was wrong.
            return Response(
                {
                    "error": {
                        "type": "invalid_request",
                        "message": f"Unknown format {requested!r}. "
                        f"Expected one of: {', '.join(sorted(FORMATS))}.",
                    }
                },
                status=400,
            )
        stem = f"disputeshield-{period_from.date()}-{period_to.date()}"

        if requested == "json":
            return Response(export.manifest)

        if requested == "pdf":
            # §7.3's `format=pdf`. The document a supervisor reads; the CSVs in
            # the zip are what their systems ingest.
            response = HttpResponse(export.files["report.pdf"], content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{stem}.pdf"'
            response["Cache-Control"] = "private, no-store"
            return response

        if requested == "csv":
            response = HttpResponse(export.files["cases.csv"], content_type="text/csv")
            response["Content-Disposition"] = f'attachment; filename="{stem}-cases.csv"'
            response["Cache-Control"] = "private, no-store"
            return response

        response = HttpResponse(export.as_zip(), content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{stem}.zip"'
        response["Cache-Control"] = "private, no-store"
        return response


class ReportRecipientView(PeriodMixin, APIView):
    """The allowlist of addresses a regulatory export may be sent to.

    Compliance-only in both directions. Reading it is a disclosure of who
    receives this firm's dispute data, and writing it decides where a whole
    period may go — the more dangerous of the two, and the reason registration is
    separated from sending at all.
    """

    permission_classes = [CanChangeCompliancePolicy]

    def get(self, request):
        recipients = ReportRecipient.objects.order_by("-is_active", "address")
        return Response({"data": [_recipient(r) for r in recipients]})

    def post(self, request):
        address = (request.data.get("address") or "").strip().lower()
        label = (request.data.get("label") or "").strip()
        reason = (request.data.get("reason") or "").strip()

        if not address or not label or not reason:
            return Response(
                {
                    "error": {
                        "type": "invalid_request",
                        "message": "address, label and reason are all required. A recipient "
                        "with no stated reason is one nobody can review later.",
                    }
                },
                status=400,
            )

        try:
            validate_email(address)
        except DjangoValidationError:
            return Response(
                {
                    "error": {
                        "type": "invalid_request",
                        "message": f"{address!r} is not an address.",
                    }
                },
                status=400,
            )

        actor = request.acting_agent.pk
        with transaction.atomic():
            recipient, created = ReportRecipient.objects.get_or_create(
                tenant=request.user.tenant,
                address=address,
                defaults={"label": label, "added_by": actor, "reason": reason},
            )
            if not created and not recipient.is_active:
                # Re-activation is the same decision as registration and is
                # recorded as one. The original row stays, so the history of who
                # could receive this firm's data remains answerable.
                recipient.is_active = True
                recipient.label = label
                recipient.reason = reason
                recipient.added_by = actor
                recipient.deactivated_by = ""
                recipient.deactivated_at = None
                recipient.save()
                created = True

            if created:
                audit.append(
                    tenant=request.user.tenant,
                    event_type="report.recipient_registered",
                    subject_type="report_recipient",
                    subject_id=recipient.pk,
                    actor_type="user",
                    actor_id=actor,
                    payload={"address": address, "label": label, "reason": reason},
                )

        return Response(_recipient(recipient), status=201 if created else 200)


class ReportRecipientDetailView(PeriodMixin, APIView):
    permission_classes = [CanChangeCompliancePolicy]

    def delete(self, request, recipient_id: str):
        """Deactivates. Nothing here deletes a row — see the model's docstring."""
        recipient = ReportRecipient.objects.filter(pk=recipient_id).first()
        if recipient is None:
            raise NotFound

        actor = request.acting_agent.pk
        with transaction.atomic():
            if recipient.is_active:
                recipient.is_active = False
                recipient.deactivated_by = actor
                recipient.deactivated_at = timezone.now()
                recipient.save(update_fields=["is_active", "deactivated_by", "deactivated_at"])
                audit.append(
                    tenant=request.user.tenant,
                    event_type="report.recipient_deactivated",
                    subject_type="report_recipient",
                    subject_id=recipient.pk,
                    actor_type="user",
                    actor_id=actor,
                    payload={"address": recipient.address},
                )
        return Response(_recipient(recipient))


class RegulatoryReportEmailView(PeriodMixin, APIView):
    """Queue a period's export for delivery to registered recipients.

    Queued rather than sent inline, deliberately. Building an export and waiting
    on a mail provider inside a request means a large period times out with no
    record of whether anything was sent — and the outbox already has the retry,
    the parking and the idempotency this needs.
    """

    permission_classes = [CanChangeCompliancePolicy]

    def post(self, request):
        period_from, period_to = self.period(request)
        addresses = request.data.get("recipients") or []
        if isinstance(addresses, str):
            addresses = [addresses]

        try:
            queued = delivery.request_delivery(
                tenant=request.user.tenant,
                period_from=period_from,
                period_to=period_to,
                addresses=list(addresses),
                requested_by=request.acting_agent.pk,
                note=str(request.data.get("note") or ""),
            )
        except delivery.UnknownRecipient as exc:
            return Response(
                {"error": {"type": "recipient_not_allowed", "message": str(exc)}}, status=400
            )
        except delivery.ReportTooLarge as exc:
            return Response(
                {"error": {"type": "report_too_large", "message": str(exc)}}, status=413
            )

        return Response(
            {
                "id": queued.notification_id,
                "status": "queued",
                "recipients": list(queued.recipients),
                "files": queued.files,
                "period": {"from": period_from.isoformat(), "to": period_to.isoformat()},
            },
            status=202,
        )


def _recipient(recipient: ReportRecipient) -> dict:
    return {
        "id": recipient.pk,
        "address": recipient.address,
        "label": recipient.label,
        "reason": recipient.reason,
        "is_active": recipient.is_active,
        "added_by": recipient.added_by,
        "created_at": recipient.created_at.isoformat() if recipient.created_at else None,
    }


class AuditVerifyView(ActingAgentMixin, APIView):
    """§8.3. Published so a customer's auditor can check the claim themselves.

    Available to any role that can read: an integrity check nobody but an owner
    may run is a check that gets run once, at onboarding.
    """

    permission_classes = [CanReadOnly]

    def get(self, request):
        return Response(attestation(request.user.tenant))


def _parse(value: str | None, default: datetime) -> datetime:
    if not value:
        return default
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
