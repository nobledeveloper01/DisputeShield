from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from disputeshield.identifiers import new_id
from disputeshield.tenancy.managers import TenantScopedModel


def allowed_origin_id() -> str:
    return new_id("org")


def widget_config_id() -> str:
    return new_id("wgt")


class AllowedOrigin(TenantScopedModel):
    """An origin permitted to embed this tenant's widget.

    Two things read this, and both matter (§10.1):

      * `frame-ancestors`, generated per tenant, so a leaked publishable key still
        will not render the widget on an attacker's page.
      * The `postMessage` origin check, on both sides of the boundary.

    §11.6 says the most common widget support ticket by a wide margin is a tenant
    adding a domain without registering it here. That is why a load from an
    unregistered origin is *recorded* rather than merely refused — so an operator
    reads the diagnosis instead of deducing it.
    """

    id = models.CharField(
        primary_key=True, max_length=32, default=allowed_origin_id, editable=False
    )
    origin = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_allowedorigin"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "origin"], name="uniq_origin_per_tenant")
        ]

    def __str__(self) -> str:
        return self.origin

    def clean(self) -> None:
        validate_origin(self.origin)

    def save(self, *args, **kwargs):
        validate_origin(self.origin)
        return super().save(*args, **kwargs)


def validate_origin(origin: str) -> None:
    """An origin is scheme + host + optional port. Nothing else.

    Rejecting a trailing path is not pedantry: `frame-ancestors` ignores the path,
    so `https://app.acme.io/dashboard` silently authorises the whole of
    `https://app.acme.io`. A tenant who wrote the longer form believes they
    restricted something they did not.
    """
    from urllib.parse import urlparse

    if origin == "null":
        raise ValidationError(
            "'null' is the origin of a sandboxed or data: document and would let "
            "any such document frame the widget."
        )

    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError(f"{origin!r} must start with http:// or https://")
    if not parsed.netloc:
        raise ValidationError(f"{origin!r} has no host")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValidationError(
            f"{origin!r} contains a path. An origin is scheme, host and port only — "
            "frame-ancestors ignores the path, so this would authorise the whole host."
        )
    if "*" in origin:
        raise ValidationError(
            f"{origin!r} contains a wildcard. Wildcard origins defeat the boundary "
            "the iframe exists to create (ADR-0001)."
        )


class WidgetConfig(TenantScopedModel):
    """Theming and the category list. Read with a publishable key; nothing else is.

    Everything here is deliberately non-sensitive, because the publishable key
    that reads it is embedded in a public page. If a field would matter when
    disclosed, it does not belong on this model.
    """

    id = models.CharField(primary_key=True, max_length=32, default=widget_config_id, editable=False)
    primary_colour = models.CharField(max_length=9, default="#0B5FFF")
    radius = models.CharField(max_length=8, default="8px")
    logo_url = models.URLField(blank=True)
    position = models.CharField(max_length=16, default="bottom-right")
    locale = models.CharField(max_length=8, default="en")
    categories = models.JSONField(default=list)

    class Meta:
        db_table = "disputeshield_widgetconfig"
        constraints = [
            models.UniqueConstraint(fields=["tenant"], name="uniq_widget_config_per_tenant")
        ]

    def __str__(self) -> str:
        return f"widget config for {self.tenant_id}"

    def frame_ancestors(self) -> str:
        origins = list(
            AllowedOrigin.objects.filter(tenant_id=self.tenant_id).values_list("origin", flat=True)
        )
        # No registered origin means the widget frames nowhere. Failing closed is
        # the only safe default: 'none' renders an error the tenant can diagnose,
        # while a permissive default would ship a working widget with no boundary.
        return " ".join(origins) if origins else "'none'"
