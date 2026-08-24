"""The widget API (§7.2) and the session-minting endpoint (§7.1).

Every view here is scoped by the session token's `customer_ref_hash`. There is no
parameter a caller can supply that widens that scope, and no code path from a
publishable key to any of it.
"""

from __future__ import annotations

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from disputeshield.api import sessions
from disputeshield.api.authentication import (
    APIKeyAuthentication,
    PublishableKeyAuthentication,
    SessionTokenAuthentication,
)
from disputeshield.api.idempotency import IdempotentCreateMixin
from disputeshield.api.serializers_widget import (
    WidgetDisputeCreateSerializer,
    WidgetDisputeSerializer,
    WidgetMessageCreateSerializer,
    WidgetMessageSerializer,
)
from disputeshield.api.views_attachments import AttachmentActionsMixin, CustomerAttachmentSerializer
from disputeshield.disputes import service
from disputeshield.models import APIKey, Dispute, DisputeMessage, SLAPolicy, WidgetConfig


class HasSessionToken(BasePermission):
    """A session token, and nothing else, authorises widget data access.

    An API key reaching a widget route must not be treated as a customer — it has
    no customer scope, so it would either fail confusingly or, worse, succeed with
    a scope of "everyone".
    """

    def has_permission(self, request, view) -> bool:
        from disputeshield.api.authentication import SessionUser

        return isinstance(getattr(request, "user", None), SessionUser)


class HasPublishableKey(BasePermission):
    def has_permission(self, request, view) -> bool:
        from disputeshield.api.authentication import PublishableKeyUser

        return isinstance(getattr(request, "user", None), PublishableKeyUser)


# -- session minting: the fintech's backend calls this -------------------------


class TransactionSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=128)
    amount_minor = serializers.IntegerField(required=False, allow_null=True)
    currency = serializers.CharField(max_length=3, required=False, allow_blank=True)
    occurred_at = serializers.DateTimeField(required=False, allow_null=True)
    description = serializers.CharField(max_length=255, required=False, allow_blank=True)
    status = serializers.CharField(max_length=32, required=False, allow_blank=True)


class SessionRequestSerializer(serializers.Serializer):
    customer_ref = serializers.CharField(max_length=128)
    display_name = serializers.CharField(max_length=128, required=False, allow_blank=True)
    transactions = TransactionSerializer(many=True, required=False)
    ttl_seconds = serializers.IntegerField(required=False, min_value=60, max_value=3600)


class SessionView(APIView):
    """`POST /v1/sessions`. Secret key only — this is the scope decision."""

    authentication_classes = [APIKeyAuthentication]

    def post(self, request):
        if not isinstance(getattr(request, "user", None), object) or not hasattr(
            request.user, "api_key"
        ):
            return Response({"error": {"type": "unauthenticated"}}, status=401)
        if request.user.api_key.kind != APIKey.Kind.SECRET:
            # A publishable key minting sessions would let anyone reading the
            # page mint a session for any customer_ref they cared to name.
            return Response(
                {
                    "error": {
                        "type": "not_found",
                        "message": "No such resource.",
                    }
                },
                status=404,
            )

        serializer = SessionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        token, session = sessions.mint(
            tenant=request.user.tenant,
            customer_ref=payload["customer_ref"],
            api_key_id=request.user.api_key.pk,
            display_name=payload.get("display_name", ""),
            transactions=[dict(t) for t in payload.get("transactions", [])],
            ttl_seconds=payload.get("ttl_seconds"),
        )
        return Response(
            {"session_token": token, "expires_at": session.expires_at},
            status=status.HTTP_201_CREATED,
        )


# -- configuration: the publishable key's only capability ----------------------


class WidgetConfigView(APIView):
    """Theme, categories and locale. Everything here is safe to disclose."""

    authentication_classes = [PublishableKeyAuthentication]
    permission_classes = [HasPublishableKey]

    def get(self, request):
        tenant = request.user.tenant
        config = WidgetConfig.objects.filter(tenant=tenant).first()
        categories = list(
            SLAPolicy.objects.filter(tenant=tenant).values_list("category", flat=True)
        )
        return Response(
            {
                "theme": {
                    "primary": config.primary_colour if config else "#0B5FFF",
                    "radius": config.radius if config else "8px",
                    "logo": config.logo_url if config else "",
                    "position": config.position if config else "bottom-right",
                },
                "locale": config.locale if config else "en",
                "categories": config.categories if config and config.categories else categories,
            }
        )


# -- the customer's own cases --------------------------------------------------


class WidgetDisputeViewSet(
    AttachmentActionsMixin, IdempotentCreateMixin, viewsets.ReadOnlyModelViewSet
):
    """Read-only plus explicit creates. No generic write route exists (§10)."""

    authentication_classes = [SessionTokenAuthentication]
    permission_classes = [HasSessionToken]
    serializer_class = WidgetDisputeSerializer
    # A customer sees their own upload's name and size, and nothing else. A scan
    # verdict naming a signature tells an uploader which malware got through.
    attachment_serializer = CustomerAttachmentSerializer
    uploader_type = "customer"
    pagination_class = None
    lookup_value_regex = "[A-Za-z0-9_]+"

    def get_queryset(self):
        # The token's scope, and nothing a caller can influence. There is no query
        # parameter, header or body field that widens this.
        return Dispute.objects.filter(
            customer_ref_hash=self.request.user.session.customer_ref_hash
        ).order_by("-submitted_at")

    def create(self, request, *args, **kwargs):
        payload = _validated(WidgetDisputeCreateSerializer, request)
        session = request.user.session
        tenant = request.user.tenant

        policy = SLAPolicy.objects.filter(category=payload["category"]).first()
        if policy is None or policy.current_version is None:
            return Response(
                {
                    "error": {
                        "type": "invalid_request",
                        "message": f"Unknown category {payload['category']!r}.",
                    }
                },
                status=400,
            )

        transaction_ref = payload.get("transaction_ref", "")
        if transaction_ref and not any(
            t.get("reference") == transaction_ref for t in session.transactions
        ):
            # The transaction list was supplied by the fintech at mint time, so it
            # is the only set this customer may dispute. Accepting an arbitrary
            # reference would let a customer attach someone else's transaction to
            # their own case.
            return Response(
                {
                    "error": {
                        "type": "invalid_request",
                        "message": "That transaction is not one of yours.",
                    }
                },
                status=400,
            )

        def produce():
            transaction = next(
                (t for t in session.transactions if t.get("reference") == transaction_ref), {}
            )
            dispute = service.file_dispute(
                tenant=tenant,
                customer_ref_hash=session.customer_ref_hash,
                category=payload["category"],
                subcategory=payload.get("subcategory", ""),
                description=payload["description"],
                policy_version=policy.current_version,
                display_name=session.display_name,
                transaction_ref=transaction_ref,
                amount_minor=transaction.get("amount_minor"),
                currency=transaction.get("currency", "") or "",
                actor_type="api_key",
                actor_id=session.api_key_id,
            )
            return Response(WidgetDisputeSerializer(dispute).data, status=201)

        return self.idempotent(request, "widget.dispute.create", produce)

    @action(detail=True, methods=["post"])
    def messages(self, request, pk=None):
        dispute = self.get_object()
        payload = _validated(WidgetMessageCreateSerializer, request)

        def produce():
            message = service.add_message(
                dispute=dispute,
                body=payload["body"],
                author_type=DisputeMessage.AuthorType.CUSTOMER,
                # A customer cannot author an internal note. Not "should not" —
                # the visibility is fixed here and is not read from the request.
                visibility=DisputeMessage.Visibility.CUSTOMER,
                author_id="",
            )
            return Response(WidgetMessageSerializer(message).data, status=201)

        return self.idempotent(request, "widget.message.create", produce)

    @action(detail=False, methods=["get"])
    def transactions(self, request):
        """The picker's options (§3.2 A2), straight from the minted session.

        Only that customer's transactions are ever visible, because only that
        customer's were ever supplied.
        """
        return Response({"transactions": list(request.user.session.transactions)})


def _validated(serializer_class, request) -> dict:
    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data
