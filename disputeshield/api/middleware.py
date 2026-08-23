"""Resolves the acting agent from a header, for keys that act on a person's behalf.

A dashboard session is phase 6. Until then a server-to-server key can name the
agent it is acting for, and the audit trail records that person rather than the
key — because "an API key resolved this case" is not an answer a supervisor
accepts, and the fintech's own dashboard knows who clicked.

The header is trusted only as far as the tenant boundary: the agent must belong
to the key's tenant, which the scoped manager enforces without this code having
to remember to.
"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

HEADER = "HTTP_X_DISPUTESHIELD_ACTING_AGENT"


class ActingAgentMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.acting_agent = None
        return self.get_response(request)


def resolve_acting_agent(request) -> None:
    """Called from the view layer, after authentication has set the tenant."""
    from disputeshield.models import Agent

    agent_id = request.META.get(HEADER)
    if not agent_id:
        return
    request.acting_agent = Agent.objects.filter(pk=agent_id, is_active=True).first()
