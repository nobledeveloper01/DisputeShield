"""The management API of §7.3. Agent-scoped.

Every write goes through `disputeshield.disputes.service`, never through the ORM
directly. That is what makes the audit trail complete without qualification —
there is no second path that writes a dispute, so "did we audit this?" is never a
question anyone has to remember to ask.
"""

from __future__ import annotations

from django.db.models import Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from disputeshield.api.idempotency import IdempotentCreateMixin
from disputeshield.api.middleware import resolve_acting_agent
from disputeshield.api.pagination import DisputeCursorPagination
from disputeshield.api.permissions import CanReadOnly, CanWorkTheQueue
from disputeshield.api.serializers_management import (
    AssignSerializer,
    DisputeDetailSerializer,
    ManagementDisputeSerializer,
    ManagementMessageCreateSerializer,
    PauseSerializer,
    ResolveSerializer,
    TransitionSerializer,
)
from disputeshield.disputes import service
from disputeshield.disputes.states import IllegalTransition
from disputeshield.models import Agent, Dispute, DisputeMessage
from disputeshield.models.dispute import Status
from disputeshield.sla import clock as clock_service


class ActingAgentMixin:
    """Resolve the acting agent before permissions run.

    `initial()` is the hook that runs after authentication (so the tenant context
    exists) and before permission checks (which depend on the agent's role).
    """

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        resolve_acting_agent(request)
        self.check_permissions(request)


class DisputeViewSet(ActingAgentMixin, IdempotentCreateMixin, viewsets.ReadOnlyModelViewSet):
    """Read the queue and a case; move a case through the actions below.

    `ReadOnlyModelViewSet` on purpose. A `ModelViewSet` would route `PUT`,
    `PATCH` and `DELETE` to generic ORM writes that bypass the service layer and
    the audit trail — and `tests/test_no_mutation_routes.py` walks the resolved
    URLconf to assert no such route exists anywhere in the product.
    """

    serializer_class = ManagementDisputeSerializer
    pagination_class = DisputeCursorPagination
    permission_classes = [CanReadOnly]
    lookup_value_regex = "[A-Za-z0-9_]+"

    def get_queryset(self):
        queryset = Dispute.objects.select_related("clock", "assigned_to", "policy_version")
        return self._filtered(queryset)

    def get_serializer_class(self):
        return DisputeDetailSerializer if self.action == "retrieve" else self.serializer_class

    def _filtered(self, queryset):
        params = self.request.query_params

        if status_filter := params.get("status"):
            queryset = queryset.filter(status__in=status_filter.split(","))
        if category := params.get("category"):
            queryset = queryset.filter(category__in=category.split(","))
        if assignee := params.get("assigned_to"):
            queryset = (
                queryset.filter(assigned_to__isnull=True)
                if assignee == "none"
                else queryset.filter(assigned_to_id=assignee)
            )
        if params.get("open") == "true":
            queryset = queryset.open()

        # The filter an agent actually reaches for (§3.2 B1): what is at risk.
        if risk := params.get("sla_risk"):
            now = timezone.now()
            if risk == "breached":
                queryset = queryset.filter(Q(breach_resolution=True) | Q(breach_ack=True))
            elif risk == "at_risk":
                queryset = queryset.open().filter(resolution_deadline__lte=now + _hours(24))

        if amount_min := params.get("amount_min"):
            queryset = queryset.filter(amount_minor__gte=int(amount_min))
        if amount_max := params.get("amount_max"):
            queryset = queryset.filter(amount_minor__lte=int(amount_max))

        return queryset.by_sla_urgency()

    # -- actions ---------------------------------------------------------------

    @action(detail=True, methods=["post"], permission_classes=[CanWorkTheQueue])
    def transition(self, request, pk=None):
        dispute = self.get_object()
        payload = _validated(TransitionSerializer, request)

        def produce():
            try:
                service.transition(
                    dispute=dispute,
                    to=payload["to"],
                    actor_type=_actor_type(request),
                    actor_id=_actor_id(request),
                    reason=payload["reason"],
                )
            except IllegalTransition as exc:
                return Response(
                    {"error": {"type": "invalid_transition", "message": str(exc)}},
                    status=status.HTTP_409_CONFLICT,
                )
            except service.ReasonRequired as exc:
                return Response(
                    {"error": {"type": "reason_required", "message": str(exc)}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(ManagementDisputeSerializer(dispute).data)

        return self.idempotent(request, "dispute.transition", produce)

    @action(detail=True, methods=["post"], permission_classes=[CanWorkTheQueue])
    def pause(self, request, pk=None):
        dispute = self.get_object()
        payload = _validated(PauseSerializer, request)

        def produce():
            service.transition(
                dispute=dispute,
                to=Status.AWAITING_CUSTOMER,
                actor_type=_actor_type(request),
                actor_id=_actor_id(request),
                reason=payload["reason"],
            )
            return Response(ManagementDisputeSerializer(dispute).data)

        return self.idempotent(request, "dispute.pause", produce)

    @action(detail=True, methods=["post"], permission_classes=[CanWorkTheQueue])
    def resume(self, request, pk=None):
        dispute = self.get_object()
        payload = _validated(PauseSerializer, request)

        def produce():
            service.transition(
                dispute=dispute,
                to=Status.INVESTIGATING,
                actor_type=_actor_type(request),
                actor_id=_actor_id(request),
                reason=payload["reason"],
            )
            return Response(ManagementDisputeSerializer(dispute).data)

        return self.idempotent(request, "dispute.resume", produce)

    @action(detail=True, methods=["post"], permission_classes=[CanWorkTheQueue])
    def resolve(self, request, pk=None):
        dispute = self.get_object()
        payload = _validated(ResolveSerializer, request)

        def produce():
            service.resolve(
                dispute=dispute,
                outcome=payload["outcome"],
                notes=payload["notes"],
                refund_amount_minor=payload.get("refund_amount_minor"),
                actor_type=_actor_type(request),
                actor_id=_actor_id(request),
            )
            return Response(ManagementDisputeSerializer(dispute).data)

        return self.idempotent(request, "dispute.resolve", produce)

    @action(detail=True, methods=["post"], permission_classes=[CanWorkTheQueue])
    def assign(self, request, pk=None):
        dispute = self.get_object()
        payload = _validated(AssignSerializer, request)

        def produce():
            agent = None
            if payload["agent_id"]:
                agent = Agent.objects.filter(pk=payload["agent_id"]).first()
                if agent is None:
                    # Cross-tenant or nonexistent are indistinguishable here, and
                    # that is the point (D8).
                    return Response(
                        {"error": {"type": "not_found", "message": "No such agent."}}, status=404
                    )
            service.assign(
                dispute=dispute,
                agent=agent,
                actor_type=_actor_type(request),
                actor_id=_actor_id(request),
                reason=payload["reason"],
            )
            return Response(ManagementDisputeSerializer(dispute).data)

        return self.idempotent(request, "dispute.assign", produce)

    @action(detail=True, methods=["get", "post"], permission_classes=[CanWorkTheQueue])
    def messages(self, request, pk=None):
        dispute = self.get_object()

        if request.method == "GET":
            from disputeshield.api.serializers_management import ManagementMessageSerializer

            return Response(ManagementMessageSerializer(dispute.messages.all(), many=True).data)

        payload = _validated(ManagementMessageCreateSerializer, request)

        def produce():
            message = service.add_message(
                dispute=dispute,
                body=payload["body"],
                visibility=payload["visibility"],
                author_type=DisputeMessage.AuthorType.AGENT,
                author_id=_actor_id(request),
            )
            from disputeshield.api.serializers_management import ManagementMessageSerializer

            return Response(ManagementMessageSerializer(message).data, status=201)

        return self.idempotent(request, "dispute.message", produce)

    @action(detail=True, methods=["get"], permission_classes=[CanReadOnly])
    def sla(self, request, pk=None):
        dispute = self.get_object()
        return Response(
            {
                "state": dispute.clock.state,
                "remaining_seconds": clock_service.remaining_seconds(dispute.clock),
                "ack_deadline": dispute.ack_deadline,
                "resolution_deadline": dispute.resolution_deadline,
                "breach_ack": dispute.breach_ack,
                "breach_resolution": dispute.breach_resolution,
                "policy_version": dispute.policy_version_id,
                "regulatory_reference": dispute.policy_version.regulatory_reference,
            }
        )


def _validated(serializer_class, request) -> dict:
    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _actor_type(request) -> str:
    return "user" if getattr(request, "acting_agent", None) else "api_key"


def _actor_id(request) -> str:
    agent = getattr(request, "acting_agent", None)
    return agent.pk if agent else request.user.api_key.pk


def _hours(count: int):
    from datetime import timedelta

    return timedelta(hours=count)
