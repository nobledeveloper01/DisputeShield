"""What a customer is allowed to see. §10's most consequential boundary.

**Nothing in this module may reach internal content.** Not by a nested
serializer, not by a `related_name` traversal, not by a field inherited from a
shared base class. There is no shared base class with the management serializers,
deliberately: inheritance is how a field added for agents silently appears in a
customer's response six months later, and the leakage test walks the field graph
precisely because that arrival would be invisible in review.

`tests/test_serializer_leakage.py` introspects every field path reachable from
here and asserts none of them lands on internal data. It walks the graph rather
than sampling output, because a future field could open a path no sample happens
to exercise.
"""

from __future__ import annotations

from rest_framework import serializers

from disputeshield.models import Dispute, DisputeMessage


class WidgetMessageSerializer(serializers.ModelSerializer):
    """Customer-visible messages only.

    The queryset filter in the view is not the guarantee — this serializer is
    never handed an internal message, and if it were, `visibility` is the only
    thing it would reveal about one. The structural guarantee is the absence of
    any field that could carry internal content, which is what the leakage test
    checks.
    """

    author = serializers.SerializerMethodField()

    class Meta:
        model = DisputeMessage
        fields = ("id", "author", "body", "created_at")
        read_only_fields = fields

    def get_author(self, message: DisputeMessage) -> str:
        # The agent's identity is never exposed. A customer learns that the firm
        # replied, not which named employee replied — that is a safety property
        # for the employee and gives the customer nothing they need.
        return "you" if message.author_type == DisputeMessage.AuthorType.CUSTOMER else "support"


class WidgetDisputeSerializer(serializers.ModelSerializer):
    """A case, as its own customer sees it."""

    status = serializers.CharField(read_only=True)
    expected_resolution_at = serializers.DateTimeField(source="resolution_deadline", read_only=True)
    messages = serializers.SerializerMethodField()

    class Meta:
        model = Dispute
        fields = (
            "id",
            "reference",
            "category",
            "subcategory",
            "description",
            "transaction_ref",
            "amount_minor",
            "currency",
            "status",
            "submitted_at",
            "acknowledged_at",
            "resolved_at",
            "expected_resolution_at",
            "outcome",
            "messages",
        )
        read_only_fields = fields

    def get_messages(self, dispute: Dispute) -> list[dict]:
        visible = dispute.messages.filter(visibility=DisputeMessage.Visibility.CUSTOMER)
        return WidgetMessageSerializer(visible, many=True).data


class WidgetDisputeCreateSerializer(serializers.Serializer):
    """Filing. The customer supplies these and nothing else.

    Not a ModelSerializer: a ModelSerializer here would accept whatever fields
    the model grows, and the set a customer may write is a decision, not a
    consequence of the schema.
    """

    category = serializers.CharField(max_length=64)
    subcategory = serializers.CharField(max_length=64, required=False, allow_blank=True)
    description = serializers.CharField(max_length=10_000)
    transaction_ref = serializers.CharField(max_length=128, required=False, allow_blank=True)


class WidgetMessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=10_000)


class WebhookDisputeSerializer(serializers.ModelSerializer):
    """The projection an outbound webhook carries (amplifier A14).

    **Declared in this module deliberately.** A14 says the internal-note exclusion
    of §10 applies identically to webhooks, and the way to guarantee that is for
    the webhook payload to be covered by the *same* field-graph test that protects
    the widget — not by a parallel one. A second implementation of one guarantee
    is a second thing to get wrong, and the two would drift the first time
    somebody added a field to only one of them.

    So `tests/test_serializer_leakage.py` walks this class alongside the widget's,
    and its module enumeration fails the build if a new serializer appears here
    without coverage.
    """

    sla = serializers.SerializerMethodField()

    class Meta:
        model = Dispute
        fields = (
            "id",
            "reference",
            "category",
            "subcategory",
            "status",
            "transaction_ref",
            "amount_minor",
            "currency",
            "submitted_at",
            "acknowledged_at",
            "resolved_at",
            "outcome",
            "sla",
        )
        read_only_fields = fields

    def get_sla(self, dispute: Dispute) -> dict:
        """Deadlines and breach flags. No pause reasons, no agent, no notes."""
        return {
            "ack_deadline": dispute.ack_deadline,
            "resolution_deadline": dispute.resolution_deadline,
            "breached": dispute.breach_resolution or dispute.breach_ack,
        }
