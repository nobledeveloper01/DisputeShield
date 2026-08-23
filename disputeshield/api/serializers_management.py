"""What an agent or a compliance user sees. Deliberately unrelated to the widget
serializers — see the note at the top of `serializers_widget.py`.
"""

from __future__ import annotations

from rest_framework import serializers

from disputeshield.models import Dispute, DisputeMessage, SLAEvent
from disputeshield.models.dispute import Outcome, Status


class ManagementMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = DisputeMessage
        fields = (
            "id",
            "author_type",
            "author_id",
            "visibility",
            "body",
            "created_at",
        )
        read_only_fields = fields


class SLAEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = SLAEvent
        fields = (
            "id",
            "kind",
            "reason",
            "actor_type",
            "actor_id",
            "clock_remaining_seconds",
            "occurred_at",
        )
        read_only_fields = fields


class ManagementDisputeSerializer(serializers.ModelSerializer):
    sla = serializers.SerializerMethodField()
    assigned_to = serializers.CharField(source="assigned_to_id", read_only=True)

    class Meta:
        model = Dispute
        fields = (
            "id",
            "reference",
            "customer_ref_hash",
            "customer_display_name",
            "category",
            "subcategory",
            "description",
            "transaction_ref",
            "amount_minor",
            "currency",
            "status",
            "priority",
            "assigned_to",
            "submitted_at",
            "acknowledged_at",
            "resolved_at",
            "closed_at",
            "ack_deadline",
            "resolution_deadline",
            "breach_ack",
            "breach_resolution",
            "breach_reason",
            "outcome",
            "outcome_notes",
            "refund_amount_minor",
            "sla",
        )
        read_only_fields = fields

    def get_sla(self, dispute: Dispute) -> dict:
        from disputeshield.sla import clock as clock_service

        remaining = clock_service.remaining_seconds(dispute.clock)
        return {
            "state": dispute.clock.state,
            "remaining_seconds": remaining,
            # Breached reads as its own state, never as a negative number. A minus
            # sign is something a tired reader misses (DESIGN.md).
            "breached": remaining < 0 or dispute.breach_resolution,
            "paused_intervals": len(dispute.clock.paused_intervals),
        }


class DisputeDetailSerializer(ManagementDisputeSerializer):
    messages = ManagementMessageSerializer(many=True, read_only=True)
    sla_events = serializers.SerializerMethodField()

    class Meta(ManagementDisputeSerializer.Meta):
        fields = (*ManagementDisputeSerializer.Meta.fields, "messages", "sla_events")

    def get_sla_events(self, dispute: Dispute) -> list[dict]:
        return SLAEventSerializer(dispute.clock.events.all(), many=True).data


# -- write shapes --------------------------------------------------------------


class TransitionSerializer(serializers.Serializer):
    to = serializers.ChoiceField(choices=Status.choices)
    reason = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")


class PauseSerializer(serializers.Serializer):
    # Not optional, and not defaulted. §4.4/C3: a pausable clock is an abusable
    # clock, and a defaulted reason is an optional reason at every call site.
    reason = serializers.CharField(max_length=2000)


class ResolveSerializer(serializers.Serializer):
    outcome = serializers.ChoiceField(choices=Outcome.choices)
    notes = serializers.CharField(max_length=10_000)
    refund_amount_minor = serializers.IntegerField(required=False, allow_null=True, min_value=0)


class AssignSerializer(serializers.Serializer):
    agent_id = serializers.CharField(max_length=32, allow_null=True)
    reason = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")


class ManagementMessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=10_000)
    visibility = serializers.ChoiceField(choices=DisputeMessage.Visibility.choices)
