"""Widget configuration: theming, categories and embed origins (§7.3).

Two permissions on one screen, deliberately. Theming and the category list are
compliance-level changes. **Registering an embed origin is owner-only**: it
widens `frame-ancestors`, which is the boundary ADR-0001 exists to create and the
reason a leaked publishable key is harmless. Widening it is closer to changing
account settings than to changing a colour.

The cross-check this module exists for: a category offered by the widget with no
SLA policy behind it makes filing **fail for a real customer**, with "Unknown
category", after they have chosen it. Nothing in the data model prevents the
combination, so the API reports it and the dashboard refuses to hide it.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from disputeshield.api.mixins import ActingAgentMixin
from disputeshield.api.permissions import CanChangeCompliancePolicy, CanReadOnly, IsOwner
from disputeshield.models import AllowedOrigin, SLAPolicy, WidgetConfig

THEME_FIELDS = ("primary_colour", "radius", "logo_url", "position", "locale")
POSITIONS = ("bottom-right", "bottom-left")


class WidgetAdminView(ActingAgentMixin, APIView):
    permission_classes = [CanReadOnly]

    def get(self, request):
        config = WidgetConfig.objects.filter(tenant=request.user.tenant).first()
        return Response(
            {
                **_config(config, request.user.tenant),
                # So the dashboard can decline to offer a control it knows will be
                # refused. The refusal itself is still the server's — these are a
                # courtesy, never the check.
                "can_edit": CanChangeCompliancePolicy().has_permission(request, self),
                "can_change_origins": IsOwner().has_permission(request, self),
            }
        )

    def patch(self, request):
        _require(request, self, CanChangeCompliancePolicy)
        from disputeshield import audit

        tenant = request.user.tenant
        with transaction.atomic():
            config, _created = WidgetConfig.objects.get_or_create(tenant=tenant)
            changed = {}

            for field in THEME_FIELDS:
                if field not in request.data:
                    continue
                value = str(request.data[field] or "")
                if field == "position" and value not in POSITIONS:
                    return _invalid(f"position must be one of: {', '.join(POSITIONS)}.")
                if field == "primary_colour" and not _is_colour(value):
                    return _invalid(
                        f"{value!r} is not a hex colour. The widget renders this inside a "
                        "customer's page, and a value the browser cannot parse leaves their "
                        "page with an unstyled control on it."
                    )
                if getattr(config, field) != value:
                    changed[field] = [getattr(config, field), value]
                    setattr(config, field, value)

            if "categories" in request.data:
                categories = [
                    str(entry).strip() for entry in request.data["categories"] if str(entry).strip()
                ]
                if config.categories != categories:
                    changed["categories"] = [config.categories, categories]
                    config.categories = categories

            if not changed:
                return Response(_config(config, tenant))

            config.save()
            audit.append(
                tenant=tenant,
                event_type="widget.configured",
                subject_type="widget_config",
                subject_id=config.pk,
                actor_type="user",
                actor_id=request.acting_agent.pk,
                payload={"changed": changed},
            )

        return Response(_config(config, tenant))


class WidgetOriginView(ActingAgentMixin, APIView):
    """Embed origins. Owner-only to change, readable by anyone who can read."""

    permission_classes = [CanReadOnly]

    def post(self, request):
        _require(request, self, IsOwner)
        from disputeshield import audit

        origin = str(request.data.get("origin") or "").strip().rstrip("/")
        tenant = request.user.tenant

        try:
            AllowedOrigin(tenant=tenant, origin=origin).clean()
        except DjangoValidationError as exc:
            # The validator's messages explain the consequence rather than
            # restating the rule — a trailing path silently authorises the whole
            # host — so they are passed through unchanged.
            return _invalid(" ".join(exc.messages))

        with transaction.atomic():
            row, created = AllowedOrigin.objects.get_or_create(tenant=tenant, origin=origin)
            if created:
                audit.append(
                    tenant=tenant,
                    event_type="widget.origin_registered",
                    subject_type="allowed_origin",
                    subject_id=row.pk,
                    actor_type="user",
                    actor_id=request.acting_agent.pk,
                    payload={"origin": origin},
                )

        return Response({"id": row.pk, "origin": row.origin}, status=201 if created else 200)


class WidgetOriginDetailView(ActingAgentMixin, APIView):
    permission_classes = [CanReadOnly]

    def delete(self, request, origin_id: str):
        """Removing an origin stops the widget rendering there immediately.

        Audited before the row goes, because afterwards there is nothing left to
        say which origin was withdrawn or by whom — and "the widget stopped
        working on our checkout page" is a support ticket that needs an answer.
        """
        _require(request, self, IsOwner)
        from disputeshield import audit

        row = AllowedOrigin.objects.filter(pk=origin_id).first()
        if row is None:
            raise NotFound

        with transaction.atomic():
            audit.append(
                tenant=request.user.tenant,
                event_type="widget.origin_withdrawn",
                subject_type="allowed_origin",
                subject_id=row.pk,
                actor_type="user",
                actor_id=request.acting_agent.pk,
                payload={"origin": row.origin},
            )
            row.delete()

        return Response(status=204)


def _require(request, view, permission_class) -> None:
    if not permission_class().has_permission(request, view):
        # 404, never 403 (D8).
        raise NotFound


def _config(config: WidgetConfig | None, tenant) -> dict:
    categories = list(config.categories) if config else []
    with_policies = set(SLAPolicy.objects.values_list("category", flat=True))

    return {
        "theme": {field: getattr(config, field, "") if config else "" for field in THEME_FIELDS},
        "positions": list(POSITIONS),
        "categories": [
            {
                "name": name,
                # The combination nothing in the data model prevents: a category
                # a customer can pick and then cannot file under.
                "has_policy": name in with_policies,
            }
            for name in categories
        ],
        # Offered nowhere, but configured. Not an error — a policy without a
        # widget category is how a channel-only category works — so it is
        # reported rather than flagged.
        "policies_not_offered": sorted(with_policies - set(categories)),
        "origins": [
            {"id": row.pk, "origin": row.origin} for row in AllowedOrigin.objects.order_by("origin")
        ],
        "frame_ancestors": config.frame_ancestors() if config else "'none'",
    }


def _is_colour(value: str) -> bool:
    if not value.startswith("#") or len(value) not in {4, 7, 9}:
        return False
    return all(character in "0123456789abcdefABCDEF" for character in value[1:])


def _invalid(message: str) -> Response:
    return Response({"error": {"type": "invalid_request", "message": message}}, status=400)
