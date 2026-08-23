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


class PublishableKeyUser:
    """The principal behind a widget *configuration* request.

    Deliberately a different class from `APIKeyUser`. Permission classes check the
    type, so a publishable key cannot satisfy a permission written for a secret
    key by accident — which is the shape the §10 threat "publishable key used to
    enumerate disputes" would take.
    """

    is_authenticated = True

    def __init__(self, api_key: APIKey) -> None:
        self.api_key = api_key
        self.tenant = api_key.tenant

    def __str__(self) -> str:
        return f"publishable:{self.api_key.prefix}"


class PublishableKeyAuthentication(authentication.BaseAuthentication):
    """Reads a `pk_` key. Accepts it from a query parameter as well as a header,
    because the embed document is loaded by an `<iframe src>` that cannot set one.

    That is safe here and nowhere else: the publishable key is designed to be
    public, and §8.5's rule about not putting sensitive data in URLs applies to
    data that is sensitive.
    """

    keyword = "Bearer"

    def authenticate(self, request):
        raw = request.query_params.get("k") or self._from_header(request)
        if not raw or not raw.startswith("pk_"):
            return None

        api_key = _verify(raw, kind=APIKey.Kind.PUBLISHABLE)
        context.set(api_key.tenant_id)
        set_tenant_context(api_key.tenant_id)
        return (PublishableKeyUser(api_key), api_key)

    def _from_header(self, request) -> str | None:
        header = authentication.get_authorization_header(request).decode()
        parts = header.split()
        return parts[1] if len(parts) == 2 and parts[0] == self.keyword else None

    def authenticate_header(self, request) -> str:
        return self.keyword


def _verify(raw: str, *, kind: str) -> APIKey:
    """Resolve a key of a specific kind. Identical failure for every reason."""
    candidates = APIKey.objects.filter(
        prefix=raw[:16], kind=kind, revoked_at__isnull=True
    ).select_related("tenant")
    for candidate in candidates:
        try:
            hasher.verify(candidate.key_hash, raw)
        except VerifyMismatchError:
            continue
        if not candidate.tenant.is_active:
            raise exceptions.AuthenticationFailed("Tenant is not active.")
        return candidate
    raise exceptions.AuthenticationFailed("Invalid API key.")


class SilentPublishableKeyAuthentication(PublishableKeyAuthentication):
    """Never raises. Used by the embed document, and nowhere else.

    §8.6 principle 1 requires the widget to fail closed *and quietly*: an
    unknown or revoked key must not produce a JSON error body, because that body
    would be what renders inside a customer-facing page on a fintech's site.

    Returning `None` hands the decision to the view, which answers with an empty
    403 and a deny-everything CSP. Every other surface keeps the raising version,
    where a clear 401 is what an integrating engineer needs.
    """

    def authenticate(self, request):
        try:
            return super().authenticate(request)
        except exceptions.AuthenticationFailed:
            return None


class SessionUser:
    """The principal behind a widget request: one customer, of one tenant."""

    is_authenticated = True

    def __init__(self, session) -> None:
        self.session = session
        self.tenant = None  # set by the authenticator, which has already loaded it

    def __str__(self) -> str:
        return f"session:{self.session.customer_ref_hash[:12]}"


class SessionTokenAuthentication(authentication.BaseAuthentication):
    """Widget requests. The token's scope is the whole authorisation decision."""

    keyword = "Bearer"

    def authenticate(self, request):
        from disputeshield.api import sessions
        from disputeshield.models import Tenant

        header = authentication.get_authorization_header(request).decode()
        if not header:
            return None
        parts = header.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            raise exceptions.AuthenticationFailed("Malformed Authorization header.")
        if not parts[1].startswith(sessions.PREFIX):
            # Not a session token. Let another authenticator try — a widget route
            # reached with an API key must not be treated as a widget session.
            return None

        try:
            session = sessions.resolve(parts[1])
        except sessions.SessionExpired as exc:
            raise exceptions.AuthenticationFailed(str(exc)) from exc

        tenant = Tenant.objects.filter(pk=session.tenant_id, is_active=True).first()
        if tenant is None:
            raise exceptions.AuthenticationFailed("Tenant is not active.")

        context.set(tenant.pk)
        set_tenant_context(tenant.pk)

        user = SessionUser(session)
        user.tenant = tenant
        return (user, session)

    def authenticate_header(self, request) -> str:
        return self.keyword


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
        if raw.startswith("dst_"):
            return None  # a session token; SessionTokenAuthentication handles it
        if raw.startswith("pk_"):
            return None  # a publishable key; it authorises configuration only

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
