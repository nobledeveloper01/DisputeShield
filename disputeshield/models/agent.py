from __future__ import annotations

from django.db import models

from disputeshield.identifiers import agent_id
from disputeshield.tenancy.managers import TenantScopedModel


class Agent(TenantScopedModel):
    """A person who acts on cases. Roles are §6.5's four, and they are not a scale.

    An agent can resolve a case but cannot change an SLA policy; a compliance
    user can change a policy but cannot work the queue. That is separation of
    duties, and collapsing it into a single privilege level is how the person
    who missed the window becomes the person who changes what the window was.
    """

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        COMPLIANCE = "compliance", "Compliance"
        AGENT = "agent", "Agent"
        READ_ONLY = "read_only", "Read-only"

    id = models.CharField(primary_key=True, max_length=32, default=agent_id, editable=False)
    email = models.EmailField(max_length=254)
    display_name = models.CharField(max_length=128)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.AGENT)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputeshield_agent"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "email"], name="uniq_agent_email_per_tenant")
        ]

    def __str__(self) -> str:
        return f"{self.display_name} <{self.email}>"
