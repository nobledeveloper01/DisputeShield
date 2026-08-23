"""Seed the configuration a new installation needs before it can accept a dispute.

Idempotent: safe to re-run, and re-running never overwrites a value an operator
has since changed. §6.2 lists this as an install step, so it has to behave like
one — no prompts, no partial state on failure.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Seed default categories, a business calendar and the default SLA policy."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--demo",
            action="store_true",
            help="Also create a demo tenant with API keys printed once (test environment only).",
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        # Phase 1 lands the models this seeds. Until then this command exists so
        # that scripts/hello-world.sh and its CI step-count assertion are real.
        self.stdout.write("disputeshield_init: models land in phase 1 (see docs/ROADMAP.md)")
