"""Preflight checks for a DisputeShield installation.

The point of this command is stated in §6.2: a self-hosted deployment where the
audit immutability migration silently failed has an audit trail that is not
actually immutable, and the customer has no way to know. Checking it explicitly,
and refusing to start in strict mode when it fails, is the difference between
claiming immutability and having it.

One rule for this file: every failure mode the product learns about gets a check
here. A doctor that only ever passes has never been shown to work, which is why
`tests/test_doctor_detects_missing_trigger.py` reverts the trigger and asserts
that this command fails.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


@dataclasses.dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fatal: bool = True


class Command(BaseCommand):
    help = "Verify that this DisputeShield installation is safe to serve traffic."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit non-zero on any fatal failure. Use this in deployment.",
        )

    def handle(self, *args, **options) -> None:
        checks = [
            self._check_audit_trigger(),
            self._check_audit_grants(),
            self._check_row_level_security(),
            self._check_clock_skew(),
            self._check_sweep_heartbeat(),
        ]

        for check in checks:
            mark = self.style.SUCCESS("ok  ") if check.ok else self.style.ERROR("FAIL")
            self.stdout.write(f"{mark} {check.name}: {check.detail}")

        failed = [c for c in checks if not c.ok and c.fatal]
        if failed and options["strict"]:
            raise CommandError(f"{len(failed)} fatal check(s) failed. Refusing to serve.")

    # -- checks ------------------------------------------------------------

    def _check_audit_trigger(self) -> Check:
        """§8.3: a BEFORE UPDATE OR DELETE trigger raises regardless of role."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT tgname FROM pg_trigger
                WHERE tgrelid = to_regclass('disputeshield_auditrecord')
                  AND NOT tgisinternal
                """
            )
            triggers = {row[0] for row in cursor.fetchall()}

        expected = "disputeshield_auditrecord_immutable"
        if expected in triggers:
            return Check("audit immutability trigger", True, f"{expected} installed")
        return Check(
            "audit immutability trigger",
            False,
            f"{expected} is MISSING — the audit trail is immutable by convention only",
        )

    def _check_audit_grants(self) -> Check:
        """§8.3: the application role holds INSERT and SELECT on audit, nothing else."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT privilege_type FROM information_schema.role_table_grants
                WHERE table_name = 'disputeshield_auditrecord'
                  AND grantee = current_user
                """
            )
            granted = {row[0] for row in cursor.fetchall()}

        forbidden = granted & {"UPDATE", "DELETE", "TRUNCATE"}
        if forbidden:
            return Check(
                "audit table grants",
                False,
                f"role {connection.settings_dict['USER']!r} holds {sorted(forbidden)} on the "
                "audit table — it must hold INSERT and SELECT only",
            )
        return Check("audit table grants", True, "INSERT and SELECT only")

    def _check_row_level_security(self) -> Check:
        """§8.1 layer 3: RLS enabled, and the connecting role does not bypass it."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user"
            )
            row = cursor.fetchone()

        if row and (row[0] or row[1]):
            return Check(
                "row level security",
                False,
                f"role {connection.settings_dict['USER']!r} bypasses RLS (superuser or "
                "BYPASSRLS) — the third isolation layer is inert",
            )
        return Check("row level security", True, "connecting role is subject to RLS")

    def _check_sweep_heartbeat(self) -> Check:
        """§11.3/§11.5: the compliance clock only advances if the sweep runs.

        A never-started beat is the dangerous case, not the reassuring one: the
        API is up, the dashboard renders, and every clock is frozen. This check is
        a warning rather than fatal because a fresh installation has legitimately
        never swept — but it says so in those words, so nobody reads a blank as
        healthy.
        """
        from disputeshield.sla.sweeper import heartbeat_age_seconds

        age = heartbeat_age_seconds()
        if age is None:
            return Check(
                "sla sweep heartbeat",
                False,
                "the sweep has never run — SLA clocks are not advancing. Start Celery "
                "beat (exactly one replica, holding a leader lock).",
                fatal=False,
            )
        if age > 180:
            return Check(
                "sla sweep heartbeat",
                False,
                f"last swept {age:.0f}s ago, past the 3-minute budget — see "
                "docs/runbook-sla-sweep.md",
                fatal=False,
            )
        return Check("sla sweep heartbeat", True, f"last swept {age:.0f}s ago")

    def _check_clock_skew(self) -> Check:
        """Every deadline in the product is computed against a clock. It should be the same one."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT now() AT TIME ZONE 'utc'")
            db_now = cursor.fetchone()[0].replace(tzinfo=UTC)

        skew = abs((datetime.now(UTC) - db_now).total_seconds())
        if skew > 2.0:
            return Check(
                "clock skew",
                False,
                f"application and database clocks differ by {skew:.1f}s — SLA deadlines "
                "and audit timestamps would disagree about when things happened",
            )
        return Check("clock skew", True, f"{skew:.2f}s between application and database")
