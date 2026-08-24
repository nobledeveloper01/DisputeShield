"""§10 — an internal note is structurally incapable of reaching the customer.

This walks the widget serializers' **full field graph** rather than sampling
output, and the difference matters: sampling proves that the responses we happen
to generate today are clean. A field added to a shared base class next year, or a
nested serializer that gains a relation, opens a path no existing sample
exercises — and the first person to see it is the customer who receives another
customer's internal note.

The graph walk fails on the *shape*, before anyone has to think of the input that
would expose it.
"""

from __future__ import annotations

import pytest
from rest_framework import serializers

from disputeshield.api import serializers_widget
from disputeshield.api.serializers_widget import (
    WebhookDisputeSerializer,
    WidgetDisputeSerializer,
    WidgetMessageSerializer,
)
from disputeshield.models import DisputeMessage
from disputeshield.models.dispute import Status

pytestmark = pytest.mark.leakage

# Anything whose name implies internal content, agent identity or clock
# machinery. A widget serializer reaching any of these is the finding.
FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "visibility",
        "author_type",
        "author_id",
        "assigned_to",
        "assigned_to_id",
        "outcome_notes",
        "breach_reason",
        "priority",
        "customer_ref_hash",
        "policy_version",
        "policy_version_id",
        "clock",
        "clock_id",
        "sla_events",
        "internal_notes",
        "paused_intervals",
        "breach_ack",
        "breach_resolution",
        "refund_amount_minor",
        "tenant",
        "tenant_id",
    }
)

FORBIDDEN_SOURCES = frozenset({"visibility", "author_type", "author_id", "outcome_notes"})


def walk(serializer, path=(), seen=None) -> list[tuple[str, ...]]:
    """Every leaf field path reachable from a serializer, following nesting."""
    seen = seen if seen is not None else set()
    identity = id(serializer)
    if identity in seen:
        return []
    seen.add(identity)

    paths: list[tuple[str, ...]] = []
    for name, field in serializer.fields.items():
        here = (*path, name)
        if isinstance(field, serializers.BaseSerializer):
            child = field.child if isinstance(field, serializers.ListSerializer) else field
            nested = walk(child, here, seen)
            paths.extend(nested or [here])
        else:
            paths.append(here)
    return paths


class TestWidgetSerializerFieldGraph:
    def test_no_reachable_field_is_named_for_internal_content(self):
        # The webhook payload is walked by this same test, not a parallel one:
        # a second implementation of one guarantee is a second thing to get wrong.
        for serializer in (
            WidgetDisputeSerializer(),
            WidgetMessageSerializer(),
            WebhookDisputeSerializer(),
        ):
            for path in walk(serializer):
                assert path[-1] not in FORBIDDEN_FIELD_NAMES, (
                    f"{type(serializer).__name__} exposes {'.'.join(path)} to the customer. "
                    "Widget and management serializers share no base class precisely so "
                    "that this cannot happen by inheritance."
                )

    def test_no_field_sources_from_internal_data(self):
        """A field can be renamed and still read internal data through `source`."""
        for serializer in (
            WidgetDisputeSerializer(),
            WidgetMessageSerializer(),
            WebhookDisputeSerializer(),
        ):
            for name, field in serializer.fields.items():
                source = getattr(field, "source", None) or name
                assert source not in FORBIDDEN_SOURCES, (
                    f"{type(serializer).__name__}.{name} sources from {source!r}"
                )

    def test_the_widget_serializers_share_no_base_class_with_the_management_ones(self):
        """Inheritance is how a field added for agents silently appears in a
        customer's response. The absence of a shared base is the guarantee."""
        from disputeshield.api import serializers_management as management

        widget_bases = {
            base
            for serializer in (WidgetDisputeSerializer, WidgetMessageSerializer)
            for base in serializer.__mro__
        } - {
            serializers.ModelSerializer,
            serializers.Serializer,
            serializers.BaseSerializer,
            serializers.Field,
            object,
        }

        management_bases = {
            base
            for serializer in (
                management.ManagementDisputeSerializer,
                management.ManagementMessageSerializer,
                management.DisputeDetailSerializer,
            )
            for base in serializer.__mro__
        } - {
            serializers.ModelSerializer,
            serializers.Serializer,
            serializers.BaseSerializer,
            serializers.Field,
            object,
        }

        shared = widget_bases & management_bases
        assert not shared, f"widget and management serializers share: {shared}"

    def test_every_widget_serializer_in_the_module_is_covered_by_this_test(self):
        """A new widget serializer added without a test is a new leak path.

        Enumerating the module rather than a hand-written list is what keeps this
        true for serializers nobody has written yet.
        """
        declared = {
            name
            for name, obj in vars(serializers_widget).items()
            if isinstance(obj, type)
            and issubclass(obj, serializers.BaseSerializer)
            and obj.__module__ == serializers_widget.__name__
        }
        covered = {
            "WidgetDisputeSerializer",
            "WidgetMessageSerializer",
            "WidgetDisputeCreateSerializer",
            "WidgetMessageCreateSerializer",
            "WebhookDisputeSerializer",
        }
        assert declared == covered, (
            f"widget serializers not covered by the leakage test: {declared - covered}"
        )


@pytest.mark.django_db
class TestWidgetOutput:
    """The graph walk is the gate. These assert the behaviour it implies, because
    a structural guarantee nobody has observed working is a guarantee on paper."""

    def test_an_internal_note_never_appears_in_widget_output(
        self, tenant_a, make_policy, as_tenant
    ):
        from disputeshield.disputes import service

        version = make_policy(tenant_a)
        with as_tenant(tenant_a):
            dispute = service.file_dispute(
                tenant=tenant_a,
                customer_ref="usr_9931",
                category="failed_transfer",
                description="Transfer failed but I was debited",
                policy_version=version,
                actor_id="key_test",
            )
            service.add_message(
                dispute=dispute,
                body="Customer sounds like a repeat claimant, check history",
                author_type=DisputeMessage.AuthorType.AGENT,
                visibility=DisputeMessage.Visibility.INTERNAL,
                author_id="agt_1",
            )
            service.add_message(
                dispute=dispute,
                body="We are looking into this for you.",
                author_type=DisputeMessage.AuthorType.AGENT,
                visibility=DisputeMessage.Visibility.CUSTOMER,
                author_id="agt_1",
            )
            payload = WidgetDisputeSerializer(dispute).data

        rendered = str(payload)
        assert "repeat claimant" not in rendered
        assert "We are looking into this for you." in rendered
        assert len(payload["messages"]) == 1

    def test_the_agents_identity_is_not_exposed(self, tenant_a, make_policy, as_tenant):
        """A customer learns that the firm replied, not which named employee did."""
        from disputeshield.disputes import service

        version = make_policy(tenant_a)
        with as_tenant(tenant_a):
            dispute = service.file_dispute(
                tenant=tenant_a,
                customer_ref="usr_9931",
                category="failed_transfer",
                description="…",
                policy_version=version,
                actor_id="key_test",
            )
            service.add_message(
                dispute=dispute,
                body="Reply",
                author_type=DisputeMessage.AuthorType.AGENT,
                visibility=DisputeMessage.Visibility.CUSTOMER,
                author_id="agt_ngozi",
            )
            payload = WidgetDisputeSerializer(dispute).data

        assert "agt_ngozi" not in str(payload)
        assert payload["messages"][0]["author"] == "support"

    def test_the_customer_ref_hash_is_not_exposed(self, tenant_a, make_policy, as_tenant):
        from disputeshield.disputes import service

        version = make_policy(tenant_a)
        with as_tenant(tenant_a):
            dispute = service.file_dispute(
                tenant=tenant_a,
                customer_ref="usr_9931",
                category="failed_transfer",
                description="…",
                policy_version=version,
                actor_id="key_test",
            )
            payload = WidgetDisputeSerializer(dispute).data
        assert "customer_ref_hash" not in payload
        assert dispute.customer_ref_hash not in str(payload)

    def test_the_customer_is_told_the_expected_resolution_date(
        self, tenant_a, make_policy, as_tenant
    ):
        """§3.2 A3. The commitment is a regulatory quantity, so the customer
        should learn it from us rather than from silence."""
        from disputeshield.disputes import service

        version = make_policy(tenant_a)
        with as_tenant(tenant_a):
            dispute = service.file_dispute(
                tenant=tenant_a,
                customer_ref="usr_9931",
                category="failed_transfer",
                description="…",
                policy_version=version,
                actor_id="key_test",
            )
            payload = WidgetDisputeSerializer(dispute).data

        assert payload["expected_resolution_at"] is not None
        assert payload["status"] == Status.SUBMITTED
