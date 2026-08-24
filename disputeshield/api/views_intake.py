"""Inbound channels and the deflection check.

Two surfaces with different callers:

  * `POST /v1/intake/{channel}` — the tenant's own backend, or the mail/WhatsApp
    gateway it operates, forwarding what arrived. Secret key.
  * `POST /v1/widget/deflection` — the widget, before it renders a filing form.
    Session token.

The deflection response always carries `file_anyway`, including when nothing was
deflected, so a client cannot render a flow that lacks the control by accident.
"""

from __future__ import annotations

from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from disputeshield.api.authentication import APIKeyAuthentication, SessionTokenAuthentication
from disputeshield.api.idempotency import IdempotentCreateMixin
from disputeshield.api.mixins import ActingAgentMixin
from disputeshield.api.permissions import CanWorkTheQueue
from disputeshield.api.views_widget import HasSessionToken
from disputeshield.intake import deflection, router
from disputeshield.intake.normalise import UnsupportedChannel
from disputeshield.models import Channel


class InboundSerializer(serializers.Serializer):
    """Deliberately permissive about shape and strict about size.

    Every channel sends something different, and `normalise` is what turns that
    into one shape. Validating each channel's payload here would put the same
    knowledge in two places.
    """

    payload = serializers.DictField()
    default_category = serializers.CharField(max_length=64, required=False, default="other")


class IntakeView(ActingAgentMixin, IdempotentCreateMixin, APIView):
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [CanWorkTheQueue]

    def post(self, request, channel: str):
        if channel == Channel.WIDGET:
            return Response(
                {
                    "error": {
                        "type": "invalid_request",
                        "message": "The widget files through /v1/widget/disputes, not intake.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = InboundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        body = serializer.validated_data

        def produce():
            try:
                routed = router.receive(
                    tenant=request.user.tenant,
                    channel=channel,
                    payload=body["payload"],
                    default_category=body["default_category"],
                )
            except UnsupportedChannel as exc:
                return Response(
                    {"error": {"type": "invalid_request", "message": str(exc)}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {
                    "inbound_id": routed.record.pk,
                    "state": routed.state,
                    "reason": routed.record.state_reason,
                    # The case id only when the message actually reached one.
                    # Returning it on a quarantine would tell a forwarding
                    # gateway which case an unverified sender was aiming at.
                    "dispute_id": (
                        routed.dispute.pk
                        if routed.dispute and routed.state in {"matched", "filed"}
                        else None
                    ),
                },
                status=status.HTTP_202_ACCEPTED,
            )

        return self.idempotent(request, f"intake.{channel}", produce)


class DeflectionSerializer(serializers.Serializer):
    category = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    transaction_ref = serializers.CharField(
        max_length=128, required=False, allow_blank=True, default=""
    )
    subscribe = serializers.BooleanField(required=False, default=False)


class WidgetDeflectionView(APIView):
    """Checked before the filing form renders.

    A `deflected` response is a suggestion, never a refusal: `file_anyway` is
    always true and the widget always renders the control. Deflection that is
    wrong is complaint suppression, which is the worst accusation a regulator can
    make about a complaints system.
    """

    authentication_classes = [SessionTokenAuthentication]
    permission_classes = [HasSessionToken]

    def post(self, request):
        serializer = DeflectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        body = serializer.validated_data

        tenant = request.user.tenant
        result = deflection.check(
            tenant=tenant,
            category=body["category"],
            transaction_ref=body["transaction_ref"],
        )

        if result.deflected and body["subscribe"]:
            deflection.subscribe(
                tenant=tenant,
                incident=result.incident,
                customer_ref_hash=request.user.session.customer_ref_hash,
                transaction_ref=body["transaction_ref"],
            )
            return Response({**result.as_dict(), "subscribed": True})

        return Response({**result.as_dict(), "subscribed": False})
