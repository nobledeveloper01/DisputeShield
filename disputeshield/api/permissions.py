"""§6.5's four roles, and the separation of duties they exist to make real.

An agent can resolve a case but cannot change an SLA policy; a compliance user
can change a policy but does not work the queue. Collapsing these into one
privilege level is how the person who missed the window becomes the person who
changes what the window was.

Denials raise `PermissionDenied`, which the exception handler turns into a 404 —
see D8. The role check is about capability, not about existence, but answering
403 to an agent probing a compliance endpoint still confirms the endpoint is real.
"""

from __future__ import annotations

from rest_framework import permissions

from disputeshield.models import Agent


class RolePermission(permissions.BasePermission):
    """Authentication first, then role.

    Setting `permission_classes` on a view **replaces** the default
    `IsAuthenticated` rather than adding to it, so a role class that only checks
    a role lets an unauthenticated request straight through to the queryset. The
    scoped manager catches that and nothing leaks — but the caller gets a 500
    where they should get a 401, and the only thing standing between an anonymous
    request and the data is the last layer rather than the first.
    """

    allowed_roles: tuple[str, ...] = ()

    def has_permission(self, request, view) -> bool:
        if not getattr(request.user, "is_authenticated", False):
            return False

        actor = getattr(request, "acting_agent", None)
        if actor is None:
            # A server-to-server key acts for the tenant rather than for a person.
            # It gets agent-level capability and nothing above it: changing a
            # compliance control is a human decision with a named author.
            return "agent" in self.allowed_roles
        return actor.role in self.allowed_roles


class CanWorkTheQueue(RolePermission):
    allowed_roles = (Agent.Role.OWNER, Agent.Role.COMPLIANCE, Agent.Role.AGENT)


class CanReadOnly(RolePermission):
    allowed_roles = (
        Agent.Role.OWNER,
        Agent.Role.COMPLIANCE,
        Agent.Role.AGENT,
        Agent.Role.READ_ONLY,
    )


class CanChangeCompliancePolicy(RolePermission):
    allowed_roles = (Agent.Role.OWNER, Agent.Role.COMPLIANCE)
