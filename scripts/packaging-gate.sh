#!/usr/bin/env bash
# §6.2 is a distribution promise, and it is only true if it is tested as one.
#
# This builds a wheel, installs it into a **bare** Django project in its own
# virtualenv, and drives the documented install path end to end. It never imports
# from the working tree — a test that does proves the code works, not that the
# package does, and the gap between those two is a missing file in
# MANIFEST/`packages` that nobody notices until a customer runs `pip install`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PYTHON="${PACKAGING_PYTHON:-python3.12}"
DB_URL="${DISPUTESHIELD_DATABASE_URL:?DISPUTESHIELD_DATABASE_URL must be set}"

echo "==> Building the wheel"
cd "$ROOT"
"$ROOT/.venv/bin/python" -m pip install -q --upgrade build >/dev/null
"$ROOT/.venv/bin/python" -m build --wheel --outdir "$WORK/dist" >/dev/null
WHEEL="$(ls "$WORK"/dist/*.whl)"
echo "    $(basename "$WHEEL")"

echo "==> Installing into a bare project"
"$PYTHON" -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install -q --upgrade pip
"$WORK/venv/bin/pip" install -q "$WHEEL" "psycopg[binary]" >/dev/null

# A host project that knows nothing about DisputeShield beyond what §6.2 says.
mkdir -p "$WORK/host/hostproject"
cat > "$WORK/host/hostproject/__init__.py" <<'EOF'
EOF
cat > "$WORK/host/hostproject/settings.py" <<EOF
import os
from urllib.parse import urlparse

SECRET_KEY = "packaging-gate-only"
DEBUG = True
ALLOWED_HOSTS = ["localhost"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "disputeshield",
]
MIDDLEWARE = ["disputeshield.tenancy.middleware.TenantContextMiddleware"]
ROOT_URLCONF = "hostproject.urls"

_p = urlparse("${DB_URL}")
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _p.path.lstrip("/"),
        "USER": _p.username,
        "PASSWORD": _p.password,
        "HOST": _p.hostname,
        "PORT": _p.port or 5432,
        "ATOMIC_REQUESTS": True,
        "CONN_MAX_AGE": 0,
    }
}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DISPUTESHIELD = {"ENCRYPTION_KEY_REF": "local://packaging-gate"}
EOF

# §6.2's documented urls.py, verbatim.
cat > "$WORK/host/hostproject/urls.py" <<'EOF'
from django.urls import include, path

urlpatterns = [path("disputes/", include("disputeshield.urls"))]
EOF

cat > "$WORK/host/manage.py" <<'EOF'
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hostproject.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
EOF

cd "$WORK/host"

echo "==> The package is not the working tree"
"$WORK/venv/bin/python" - <<'EOF'
import pathlib
import disputeshield

here = pathlib.Path(disputeshield.__file__).resolve()
assert "site-packages" in str(here), f"imported the working tree, not the wheel: {here}"
print(f"    imported {here.parent}")
EOF

echo "==> migrate"
"$WORK/venv/bin/python" manage.py migrate disputeshield --no-input >/dev/null
echo "==> disputeshield_init"
"$WORK/venv/bin/python" manage.py disputeshield_init --tenant-slug packaged
echo "==> disputeshield_doctor"
"$WORK/venv/bin/python" manage.py disputeshield_doctor

echo "==> Filing a dispute through the installed package"
"$WORK/venv/bin/python" - <<'EOF'
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hostproject.settings")
django.setup()

from django.db import transaction

from disputeshield.disputes import service
from disputeshield.models import SLAPolicy, Tenant
from disputeshield.tenancy import context
from disputeshield.tenancy.middleware import db_tenant_context

tenant = Tenant.objects.get(slug="packaged")

# A transaction, because `SET LOCAL` outside one is discarded and row level
# security would then match nothing. This script is also the documentation for
# how a host project drives the package, so it shows the supported shape.
with transaction.atomic(), context.tenant_context(tenant.pk), db_tenant_context(tenant.pk):
    policy = SLAPolicy.objects.get(category="failed_transfer")
    dispute = service.file_dispute(
        tenant=tenant,
        customer_ref="usr_packaging_gate",
        category="failed_transfer",
        description="Transfer failed but I was debited",
        policy_version=policy.current_version,
        actor_type="system",
    )
    assert dispute.reference.startswith("DS-"), dispute.reference
    assert dispute.resolution_deadline is not None
    print(f"    filed {dispute.reference}, due {dispute.resolution_deadline.isoformat()}")

    from disputeshield import audit

    result = audit.verify_tenant(tenant.pk)
    assert result.ok, result.failures
    print(f"    audit chain verified over {result.records_checked} records")
EOF

echo
echo "ok: pip install -> init -> doctor -> file a dispute -> verify the chain"
