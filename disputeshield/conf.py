"""Settings namespace and the startup assertions that are not merely settings.

§10.2 requires DEBUG to be False in production *by assertion*, not by configuration.
A misconfigured DEBUG on a fintech dispute system exposes case data in a traceback,
and "we set it in the settings file" is how it ends up True on one host.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

DEFAULTS: dict[str, Any] = {
    # The bundled tenant model is the default. Pointing this at a host project's
    # own model is opt-in and contract-checked — see ADR-0012 in plan-architecture D12.
    "TENANT_MODEL": "disputeshield.Tenant",
    "WIDGET_ORIGIN": None,
    # The origin the widget is allowed to call, as it appears in the CSP's
    # connect-src. Configured rather than derived from the request: a security
    # header assembled from a request header is a header an attacker gets a vote
    # in, and ALLOWED_HOSTS narrowing that vote is not the same as removing it.
    "API_ORIGIN": None,
    "ENCRYPTION_KEY_REF": None,
    # Named for the session rather than the token: bandit reads a key containing
    # TOKEN with a literal value as a hardcoded credential, and a permanent
    # suppression to silence a false positive is worse than a clearer name.
    "SESSION_LIFETIME_SECONDS": 1800,
    "DEFAULT_SLA_POLICY": {
        "acknowledgement_minutes": 60,
        "resolution_hours": 72,
        "business_hours_only": True,
        "warning_thresholds": [50, 80, 95],
        "escalate_at_percent": 80,
        "auto_close_after_hours": 168,
        "reopen_window_hours": 336,
    },
    # Refuse to serve if the audit immutability trigger is not installed (§6.2).
    # No default scanner. An installation that has not configured one gets
    # attachments marked `failed` — invisible to everyone — rather than unscanned
    # files being served to agents.
    "AV_SCANNER": None,
    "NOTIFICATION_CHANNELS": {},
    # No default. An installation that has not configured one accumulates a
    # visible unanchored backlog rather than a false claim of attestation.
    "TIMESTAMP_AUTHORITY": None,
    "STRICT_IMMUTABILITY": True,
    "ADVISORY_LOCK_NAMESPACE": 8_140_1,
}


def get(name: str) -> Any:
    """Read a DISPUTESHIELD setting, falling back to the documented default."""
    configured = getattr(settings, "DISPUTESHIELD", {})
    if name in configured:
        return configured[name]
    if name in DEFAULTS:
        return DEFAULTS[name]
    raise ImproperlyConfigured(f"Unknown DisputeShield setting: {name!r}")


def check_production_invariants() -> list[str]:
    """Return a list of violated production invariants. Empty means safe.

    Called from AppConfig.ready(). These are assertions rather than documentation
    because each one has been someone's incident somewhere.
    """
    problems: list[str] = []

    if settings.DEBUG:
        problems.append("DEBUG is True. Case data would be exposed in tracebacks (§10.2).")

    if "*" in settings.ALLOWED_HOSTS:
        problems.append("ALLOWED_HOSTS contains '*' (§10.2).")

    if not getattr(settings, "SECURE_SSL_REDIRECT", False):
        problems.append("SECURE_SSL_REDIRECT is not enabled (§10.2).")

    if not getattr(settings, "SESSION_COOKIE_SECURE", False):
        problems.append("SESSION_COOKIE_SECURE is not enabled (§10.2).")

    if not getattr(settings, "CSRF_COOKIE_SECURE", False):
        problems.append("CSRF_COOKIE_SECURE is not enabled (§10.2).")

    if not getattr(settings, "ATOMIC_REQUESTS", None) and not _atomic_requests_on_default_db():
        # ADR-0005: SET LOCAL needs a transaction to be local to. Without
        # ATOMIC_REQUESTS a read runs outside one and RLS has no tenant context.
        problems.append("ATOMIC_REQUESTS is not enabled on the default database (ADR-0005).")

    if get("ENCRYPTION_KEY_REF") is None:
        problems.append("ENCRYPTION_KEY_REF is unset — §8.4 content cannot be encrypted.")

    return problems


def _atomic_requests_on_default_db() -> bool:
    return bool(settings.DATABASES.get("default", {}).get("ATOMIC_REQUESTS"))
