"""No route in the product resolves to a mutation of an auditable record.

Asserted by walking the **resolved URLconf**, not by convention and not by
reading the views. A `ModelViewSet` swapped in for a `ReadOnlyModelViewSet` in a
hurry adds `PUT`, `PATCH` and `DELETE` handlers that write through the ORM,
bypassing the service layer and therefore the audit trail — and it does so
without any line of code saying so.

Every write in this product goes through `disputeshield.disputes.service`. That
is what lets the audit trail be complete without qualification.
"""

from __future__ import annotations

import pytest
from django.urls import get_resolver
from rest_framework.viewsets import ViewSetMixin

pytestmark = pytest.mark.leakage

FORBIDDEN_METHODS = frozenset({"put", "patch", "delete"})

# Everything that carries evidence. A generic write to any of these is the bug.
AUDITABLE_MODELS = frozenset(
    {
        "AuditRecord",
        "Dispute",
        "DisputeMessage",
        "SLAEvent",
        "SLADeadline",
        "SLAClock",
        "SLAPolicyVersion",
    }
)


def disputeshield_patterns():
    """Every resolved URL pattern belonging to this app."""
    resolver = get_resolver()
    found = []

    def walk(patterns, prefix=""):
        for pattern in patterns:
            if hasattr(pattern, "url_patterns"):
                walk(pattern.url_patterns, prefix + str(pattern.pattern))
            else:
                found.append((prefix + str(pattern.pattern), pattern.callback))

    walk(resolver.url_patterns)
    return [
        (route, callback)
        for route, callback in found
        if getattr(callback, "cls", None) is not None
        and callback.cls.__module__.startswith("disputeshield")
    ]


class TestNoMutationRoutes:
    def test_no_route_binds_put_patch_or_delete(self):
        offenders = []
        for route, callback in disputeshield_patterns():
            actions = getattr(callback, "actions", None) or {}
            bound = {method.lower() for method in actions} & FORBIDDEN_METHODS
            if bound:
                offenders.append(f"{route} binds {sorted(bound)} → {actions}")

        assert not offenders, (
            "routes that mutate through the ORM, bypassing the service layer and "
            "therefore the audit trail:\n  " + "\n  ".join(offenders)
        )

    def test_no_viewset_is_a_full_modelviewset_over_an_auditable_model(self):
        from rest_framework.viewsets import ModelViewSet

        offenders = []
        for route, callback in disputeshield_patterns():
            view_class = callback.cls
            if not issubclass(view_class, ViewSetMixin):
                continue
            if issubclass(view_class, ModelViewSet):
                offenders.append(f"{route} → {view_class.__name__}")

        assert not offenders, (
            "ModelViewSet routes generic writes straight to the ORM:\n  " + "\n  ".join(offenders)
        )

    def test_the_auditable_models_refuse_updates_and_deletes_in_the_orm(self):
        """Belt and braces, one layer below the routing.

        Even if a route were added, these models refuse. The routing test is the
        one that catches the mistake early; this is the one that limits the blast
        radius when it is not caught.
        """
        from disputeshield.models import AuditRecord, DisputeMessage, SLAPolicyVersion

        for model in (AuditRecord, DisputeMessage, SLAPolicyVersion):
            assert "save" in vars(model), f"{model.__name__} does not guard save()"

    def test_the_service_layer_is_the_only_module_that_writes_a_dispute(self):
        """Greps the API package for direct ORM writes to auditable models.

        Crude, and it catches the realistic mistake: someone in a hurry reaching
        for `Dispute.objects.create` or `.update()` inside a view because the
        service function did not quite fit.
        """
        import pathlib

        api_dir = pathlib.Path(__file__).resolve().parent.parent / "disputeshield" / "api"
        offenders = []
        for path in api_dir.rglob("*.py"):
            source = path.read_text()
            for model in ("Dispute", "DisputeMessage", "SLAEvent", "AuditRecord"):
                for call in (
                    f"{model}.objects.create",
                    f"{model}.objects.update",
                    f"{model}.objects.filter",
                ):
                    if f"{call}(" in source and "update(last_used_at" not in source:
                        offenders.append(f"{path.name}: {call}")

        assert not offenders, (
            "the API layer writes auditable models directly instead of going "
            f"through disputeshield.disputes.service: {offenders}"
        )
