"""Analytics, the regulatory export, and the independent integrity check."""

from __future__ import annotations

from datetime import UTC, datetime

from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework.views import APIView

from disputeshield.api.mixins import ActingAgentMixin
from disputeshield.api.permissions import CanChangeCompliancePolicy, CanReadOnly
from disputeshield.audit.checkpoints import attestation
from disputeshield.reports import analytics, regulatory

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


class RegulatoryReportView(PeriodMixin, APIView):
    """§6.5. Compliance-only: an export is a disclosure of the whole period."""

    permission_classes = [CanChangeCompliancePolicy]

    def get(self, request):
        period_from, period_to = self.period(request)
        export = regulatory.build(
            tenant=request.user.tenant, period_from=period_from, period_to=period_to
        )

        if request.query_params.get("format", "zip") == "json":
            return Response(export.manifest)

        response = HttpResponse(export.as_zip(), content_type="application/zip")
        response["Content-Disposition"] = (
            f'attachment; filename="disputeshield-{period_from.date()}-{period_to.date()}.zip"'
        )
        response["Cache-Control"] = "private, no-store"
        return response


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
