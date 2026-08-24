from __future__ import annotations

from celery import shared_task


@shared_task(name="disputeshield.audit.checkpoint")
def checkpoint() -> dict:
    """Verify every tenant's chain and publish a signed checkpoint."""
    from disputeshield.audit.checkpoints import create_checkpoint
    from disputeshield.models import Tenant
    from disputeshield.tenancy.platform import for_each_tenant

    verified = failed = 0
    for result in for_each_tenant(
        lambda tenant_id: create_checkpoint(Tenant.objects.get(pk=tenant_id))
    ):
        if result.verified:
            verified += 1
        else:
            failed += 1
    return {"verified": verified, "failed": failed}
