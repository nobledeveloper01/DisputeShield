from __future__ import annotations

import logging

from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class DisputeShieldConfig(AppConfig):
    name = "disputeshield"
    verbose_name = "DisputeShield"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from disputeshield import conf

        problems = conf.check_production_invariants()
        if not problems:
            return

        # In development these are noise; in production every one of them is a
        # reason not to serve traffic.
        if settings.DEBUG:
            for problem in problems:
                logger.warning("disputeshield: %s", problem)
            return

        raise ImproperlyConfigured(
            "DisputeShield refuses to start:\n  - " + "\n  - ".join(problems)
        )
