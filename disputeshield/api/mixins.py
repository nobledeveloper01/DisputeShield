"""Resolving the acting agent before permissions are evaluated.

Ordering is the whole content of this file. DRF's `initial()` runs
authentication, then permissions. The acting agent is resolved from a header and
needs the tenant context authentication establishes — so it has to happen between
those two steps, not after them.

Resolving it afterwards and re-running `check_permissions` almost works, and the
"almost" is instructive: a permission that admits a bare API key passes the first
check and gets corrected by the second, while a permission that does *not* admit
one — every compliance permission — raises on the first check and never reaches
the correction. The compliance officer is judged as the key they authenticated
with rather than as themselves.
"""

from __future__ import annotations

from disputeshield.api.middleware import resolve_acting_agent


class ActingAgentMixin:
    def perform_authentication(self, request) -> None:
        # Touching `request.user` forces authentication, which sets the tenant
        # context the agent lookup needs.
        super().perform_authentication(request)
        resolve_acting_agent(request)
