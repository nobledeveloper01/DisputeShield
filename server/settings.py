"""Standalone Django project wrapping the DisputeShield app (§6.3).

This is one of two deliverables the specification deliberately keeps separate:
`disputeshield` is the installable app that drops into a customer's existing
Django project, and `server` is the service we ship as a container. Anything
that belongs to the product lives in the app; only deployment concerns live here.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"{name} is required")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, "1" if default else "0").lower() in {"1", "true", "yes"}


SECRET_KEY = env("DISPUTESHIELD_SECRET_KEY")
DEBUG = env_bool("DISPUTESHIELD_DEBUG")
ALLOWED_HOSTS = [h for h in env("DISPUTESHIELD_ALLOWED_HOSTS", "localhost").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.admin",
    "rest_framework",
    "disputeshield",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Establishes the RLS tenant context with SET LOCAL, inside the request's
    # transaction. Must run after authentication and before anything queries.
    "disputeshield.tenancy.middleware.TenantContextMiddleware",
    "disputeshield.api.middleware.ActingAgentMiddleware",
]

REST_FRAMEWORK = {
    # Order matters: each authenticator returns None for a credential that is not
    # its own, so a session token never resolves as an API key and vice versa.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "disputeshield.api.authentication.SessionTokenAuthentication",
        "disputeshield.api.authentication.APIKeyAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    # D8: cross-boundary access answers 404, never 403. A handler rather than a
    # convention, because a convention is something one view eventually forgets.
    "EXCEPTION_HANDLER": "disputeshield.api.exceptions.exception_handler",
    "DEFAULT_PAGINATION_CLASS": "disputeshield.api.pagination.DisputeCursorPagination",
    "PAGE_SIZE": 50,
    "UNAUTHENTICATED_USER": None,
}

ROOT_URLCONF = "server.urls"
WSGI_APPLICATION = "server.wsgi.application"
ASGI_APPLICATION = "server.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]


def _database(url: str, *, atomic: bool) -> dict:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": parsed.username,
        "PASSWORD": parsed.password,
        "HOST": parsed.hostname,
        "PORT": parsed.port or 5432,
        # ADR-0005: every request runs in a transaction so that SET LOCAL has a
        # transaction to be local to. Without this, a read outside a transaction
        # runs with no tenant context and RLS has nothing to enforce.
        "ATOMIC_REQUESTS": atomic,
        "CONN_MAX_AGE": 0,  # PgBouncer owns pooling; Django must not also pool.
    }


DATABASES = {
    "default": _database(env("DISPUTESHIELD_DATABASE_URL"), atomic=True),
    # Analytics and exports only (§11.1). Never written to.
    "replica": _database(
        env("DISPUTESHIELD_DATABASE_REPLICA_URL", env("DISPUTESHIELD_DATABASE_URL")),
        atomic=False,
    ),
}

# Broker and cache are separate instances on purpose (§11.1): flushing the cache
# must not be capable of destroying the SLA sweep's task queue.
CELERY_BROKER_URL = env("DISPUTESHIELD_REDIS_BROKER_URL")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("DISPUTESHIELD_REDIS_CACHE_URL"),
    }
}

DISPUTESHIELD_ATTACHMENT_ROOT = os.environ.get(
    "DISPUTESHIELD_ATTACHMENT_ROOT", str(BASE_DIR / ".private" / "attachments")
)

DISPUTESHIELD = {
    "AV_SCANNER": os.environ.get(
        "DISPUTESHIELD_AV_SCANNER", "disputeshield.attachments.scanning.EicarScanner"
    ),
    "WIDGET_ORIGIN": os.environ.get("DISPUTESHIELD_WIDGET_ORIGIN"),
    "API_ORIGIN": os.environ.get("DISPUTESHIELD_API_ORIGIN"),
    "ENCRYPTION_KEY_REF": os.environ.get("DISPUTESHIELD_ENCRYPTION_KEY_REF"),
}

# §10.2. Enabled unconditionally rather than behind `if not DEBUG`, because the
# production assertion in disputeshield/conf.py checks them and a conditional
# would let a misconfigured DEBUG=False deployment start with them off.
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 0 if DEBUG else 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en"
TIME_ZONE = "UTC"  # §4.4: all arithmetic in UTC. Calendars resolve their own zone.
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
# The built widget bundle. Content-independent and tenant-independent, so it is
# cached for a year while the embed document that references it is not (D9).
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "logging.Formatter", "format": "%(levelname)s %(name)s %(message)s"}
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
