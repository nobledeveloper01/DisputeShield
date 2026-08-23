"""§8.1 layer 1 — an API key resolves to exactly one tenant. No key spans tenants.

Everything downstream depends on this being right: the scoped managers read the
tenant context this sets, and the RLS policy reads the session variable derived
from it. A mistake here is not a leak in one endpoint, it is a leak everywhere.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from django.utils import timezone
from rest_framework import authentication, exceptions

from disputeshield.models import APIKey
from disputeshield.tenancy import context
from disputeshield.tenancy.middleware import set_tenant_context

hasher = PasswordHasher()


def hash_key(raw_key: str) -> str:
    return hasher.hash(raw_key)


class APIKeyUser:
    """A non-Django principal. DRF wants `request.user`; we want a tenant."""

    is_authenticated = True

    def __init__(self, api_key: APIKey) -> None:
        self.api_key = api_key
        self.tenant = api_key.tenant
        self.environment = api_key.environment

    def __str__(self) -> str:
        return f"api_key:{self.api_key.prefix}"


class APIKeyAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode()
        if not header:
            return None
        parts = header.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            raise exceptions.AuthenticationFailed("Malformed Authorization header.")

        raw = parts[1]
        api_key = self._resolve(raw)

        # Establish both isolation contexts before any view code runs: the Python
        # contextvar the scoped managers read, and the Postgres session variable
        # RLS reads. Setting one without the other leaves half the mechanism off.
        context.set(api_key.tenant_id)
        set_tenant_context(api_key.tenant_id)

        APIKey.objects.filter(pk=api_key.pk).update(last_used_at=timezone.now())
        return (APIKeyUser(api_key), api_key)

    def _resolve(self, raw: str) -> APIKey:
        prefix = raw[:16]
        # Unscoped by necessity — the tenant is what we are resolving. This is the
        # only query in the product that legitimately runs before a tenant exists.
        candidates = APIKey.objects.filter(prefix=prefix, revoked_at__isnull=True).select_related(
            "tenant"
        )
        for candidate in candidates:
            try:
                hasher.verify(candidate.key_hash, raw)
            except VerifyMismatchError:
                continue
            if not candidate.tenant.is_active:
                raise exceptions.AuthenticationFailed("Tenant is not active.")
            return candidate

        # Identical message whether the prefix was unknown or the secret was
        # wrong. Distinguishing them tells an attacker when a prefix is real.
        raise exceptions.AuthenticationFailed("Invalid API key.")

    def authenticate_header(self, request) -> str:
        return self.keyword
