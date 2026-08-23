"""Liveness and readiness.

`/healthz` says the process is up. `/readyz` says it can actually serve — which
for this product includes the audit immutability trigger being installed, because
a deployment that can accept writes but cannot make them immutable should not be
taking traffic (§6.2).
"""

from __future__ import annotations

from django.db import connection
from django.http import JsonResponse


def healthz(request):
    return JsonResponse({"status": "ok"})


def readyz(request):
    checks = {}
    status = 200

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.execute(
                "SELECT 1 FROM pg_trigger WHERE tgname = %s",
                ["disputeshield_auditrecord_immutable"],
            )
            checks["audit_immutable"] = cursor.fetchone() is not None
        checks["database"] = True
    except Exception:
        checks["database"] = False

    if not all(checks.values()):
        status = 503
    return JsonResponse(
        {"status": "ready" if status == 200 else "degraded", "checks": checks}, status=status
    )
