from __future__ import annotations

from celery import shared_task


@shared_task(name="disputeshield.audit.checkpoint")
def checkpoint() -> dict:
    """Verify every tenant's chain and publish a signed checkpoint."""
    from disputeshield.audit.anchoring import queue
    from disputeshield.audit.checkpoints import create_checkpoint
    from disputeshield.models import Tenant
    from disputeshield.tenancy.platform import for_each_tenant

    def work(tenant_id):
        result = create_checkpoint(Tenant.objects.get(pk=tenant_id))
        if result.checkpoint is not None:
            # Queued in the same pass, never sent here. Anchoring talks to a third
            # party, and a checkpoint job that blocks on one is a checkpoint job
            # that stops when they do.
            queue(result.checkpoint)
        return result

    verified = failed = 0
    for result in for_each_tenant(work):
        if result.verified:
            verified += 1
        else:
            failed += 1
    return {"verified": verified, "failed": failed}


@shared_task(name="disputeshield.audit.anchor")
def anchor() -> dict:
    """Drain the anchoring backlog. Separate from checkpointing on purpose."""
    from disputeshield.audit.anchoring import anchor_pending

    result = anchor_pending()
    return {"anchored": result.anchored, "pending": result.pending, "failed": result.failed}
