"""SLA policies (§7.3). Read by anyone who can read; changed by compliance only.

A policy change is a change to the standard every future case is judged against,
which is why the write permission here matches the one on the regulatory export
rather than the one on the queue.
"""

from __future__ import annotations

from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from disputeshield.api.mixins import ActingAgentMixin
from disputeshield.api.permissions import CanChangeCompliancePolicy, CanReadOnly
from disputeshield.models import AuditRecord, BusinessCalendar, SLAPolicy
from disputeshield.sla import policies


class SLAPolicyView(ActingAgentMixin, APIView):
    permission_classes = [CanReadOnly]

    def get(self, request):
        return Response(
            {
                "data": [_policy(policy) for policy in SLAPolicy.objects.order_by("category")],
                "calendars": [
                    {"id": c.pk, "name": c.name, "timezone": c.timezone_name}
                    for c in BusinessCalendar.objects.order_by("name")
                ],
            }
        )

    def post(self, request):
        self._require_compliance(request)
        category = (request.data.get("category") or "").strip()
        if not category:
            return _invalid("A policy needs a category.")
        return self._publish(request, category, _terms(request.data))

    def _publish(self, request, category: str, terms: dict, status_code: int = 201):
        calendar = None
        if calendar_id := request.data.get("calendar_id"):
            calendar = BusinessCalendar.objects.filter(pk=calendar_id).first()
            if calendar is None:
                raise NotFound

        try:
            published = policies.publish(
                tenant=request.user.tenant,
                category=category,
                terms=terms,
                calendar=calendar,
                actor_id=request.acting_agent.pk,
                description=str(request.data.get("description") or ""),
            )
        except policies.InvalidTerms as exc:
            return _invalid(str(exc))

        return Response(
            {**_policy(published.policy), "changed": published.changed}, status=status_code
        )

    def _require_compliance(self, request) -> None:
        for permission in [CanChangeCompliancePolicy()]:
            if not permission.has_permission(request, self):
                # 404, never 403 (D8).
                raise NotFound


class SLAPolicyDetailView(SLAPolicyView):
    def get(self, request, policy_id: str):
        policy = SLAPolicy.objects.filter(pk=policy_id).first()
        if policy is None:
            raise NotFound
        return Response(_policy(policy, with_history=True))

    def patch(self, request, policy_id: str):
        """§7.3's PATCH. Publishes version n+1 rather than editing in place.

        The two documents point in different directions — §7.3 says PATCH,
        ADR-0004 says the terms are immutable — and this is where they are
        reconciled. The *policy* is patched and its representation changes; the
        terms any case was judged under are untouched. Mutating in place would
        satisfy the endpoint documentation and destroy the evidence.
        """
        policy = SLAPolicy.objects.filter(pk=policy_id).first()
        if policy is None:
            raise NotFound
        self._require_compliance(request)

        # A PATCH carries only what changed, so the rest is carried forward from
        # the version in force. Sending the sparse body straight to `publish()`
        # would publish a version whose unmentioned terms were silently defaults.
        current = policy.current_version
        merged = {
            field: request.data.get(field, getattr(current, field) if current else None)
            for field in policies.TERMS
        }
        return self._publish(request, policy.category, merged, status_code=200)


def _terms(data) -> dict:
    terms = {}
    for field in policies.TERMS:
        if field in data:
            terms[field] = data[field]
    return terms


def _policy(policy: SLAPolicy, *, with_history: bool = False) -> dict:
    versions = list(policy.versions.order_by("-version"))
    current = versions[0] if versions else None

    body = {
        "id": policy.pk,
        "category": policy.category,
        "description": policy.description,
        "current": _version(current) if current else None,
        "version_count": len(versions),
    }
    if with_history:
        # Newest first, each carrying what changed when it was published. A list
        # of full snapshots leaves a reviewer to diff them by eye.
        changes = {
            record.subject_id + "/" + str(record.payload.get("version")): record
            for record in AuditRecord.objects.filter(
                event_type="sla_policy.published", subject_id=policy.pk
            )
        }
        body["history"] = [
            {
                **_version(version),
                "changed": (
                    changes.get(f"{policy.pk}/{version.version}").payload.get("changed")
                    if changes.get(f"{policy.pk}/{version.version}")
                    else {}
                ),
            }
            for version in versions
        ]
    return body


def _version(version) -> dict:
    return {
        "id": version.pk,
        "version": version.version,
        "calendar": version.calendar.name,
        "calendar_timezone": version.calendar.timezone_name,
        "created_at": version.created_at.isoformat(),
        "created_by": version.created_by,
        **{field: getattr(version, field) for field in policies.TERMS},
    }


def _invalid(message: str) -> Response:
    return Response({"error": {"type": "invalid_request", "message": message}}, status=400)
