"""Settings: API keys, the team, and the retention position (§7.3, §8.2, §11.7).

Everything here is owner-only to change. These are the settings that decide who
can reach the product and under what credentials, which is a narrower question
than compliance work and belongs to a narrower role.

Three properties are load-bearing, and each exists because the failure it
prevents is permanent:

  * **A key's value is returned exactly once, at creation.** Only an Argon2id
    hash is stored, so there is nothing to show later. The audit record carries
    the prefix and never the key — an audit trail that records credentials is a
    credential store with a retention policy attached.
  * **The last active owner cannot be demoted or deactivated.** There is no
    recovery path from a tenant with no owner: nobody left can mint a key, change
    a role, or register an origin. The check is here rather than in the interface
    because an interface is one client of several.
  * **Nobody changes their own role.** Self-promotion is the obvious reason;
    self-demotion is the likelier accident, and it produces the same locked-out
    tenant by a different route.

Retention is reported, not configured. §11.7 sets seven years, and a tenant that
could shorten its own retention window below the mandated one would be using a
settings screen to fall out of compliance. What the screen offers instead is the
position: what is past the window, what is held, and what the sweep would do.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from disputeshield.api.authentication import hash_key
from disputeshield.api.mixins import ActingAgentMixin
from disputeshield.api.permissions import CanReadOnly, IsOwner
from disputeshield.identifiers import generate_api_key
from disputeshield.models import Agent, APIKey


class APIKeyView(ActingAgentMixin, APIView):
    permission_classes = [CanReadOnly]

    def get(self, request):
        keys = APIKey.objects.filter(tenant=request.user.tenant).order_by(
            "revoked_at", "-created_at"
        )
        return Response(
            {
                "data": [_key(row, _current_key_id(request)) for row in keys],
                "can_manage": IsOwner().has_permission(request, self),
            }
        )

    def post(self, request):
        _require_owner(request, self)
        from disputeshield import audit

        name = str(request.data.get("name") or "").strip()
        environment = str(request.data.get("environment") or "").strip()
        kind = str(request.data.get("kind") or APIKey.Kind.SECRET).strip()

        if not name:
            return _invalid("A key needs a name. An unnamed key is one nobody can safely revoke.")
        if environment not in {"test", "live"}:
            return _invalid("environment must be 'test' or 'live'.")
        if kind not in {APIKey.Kind.SECRET, APIKey.Kind.PUBLISHABLE}:
            return _invalid("kind must be 'secret' or 'publishable'.")

        full, prefix = generate_api_key(environment, kind=kind)

        with transaction.atomic():
            row = APIKey.objects.create(
                tenant=request.user.tenant,
                name=name,
                environment=environment,
                kind=kind,
                prefix=prefix,
                key_hash=hash_key(full),
            )
            audit.append(
                tenant=request.user.tenant,
                event_type="api_key.created",
                subject_type="api_key",
                subject_id=row.pk,
                actor_type="user",
                actor_id=request.acting_agent.pk,
                # The prefix, never the key. An audit trail that records
                # credentials is a credential store that outlives them.
                payload={"name": name, "environment": environment, "kind": kind, "prefix": prefix},
            )

        return Response(
            {
                **_key(row, None),
                # The only time this value exists outside the caller's hands. It
                # is not stored, so it cannot be shown again, and the client is
                # told so rather than left to discover it.
                "key": full,
                "shown_once": True,
            },
            status=201,
        )


class APIKeyDetailView(ActingAgentMixin, APIView):
    permission_classes = [CanReadOnly]

    def delete(self, request, key_id: str):
        """Revoke. Immediate, and permitted even on the key making the request.

        Refusing that case would be the wrong protection: the reason to revoke a
        key in a hurry is usually that it has leaked, and the person holding it is
        the one who noticed. The response says what happened instead.
        """
        _require_owner(request, self)
        from disputeshield import audit

        row = APIKey.objects.filter(tenant=request.user.tenant, pk=key_id).first()
        if row is None:
            raise NotFound

        if row.revoked_at is None:
            with transaction.atomic():
                row.revoked_at = timezone.now()
                row.save(update_fields=["revoked_at"])
                audit.append(
                    tenant=request.user.tenant,
                    event_type="api_key.revoked",
                    subject_type="api_key",
                    subject_id=row.pk,
                    actor_type="user",
                    actor_id=request.acting_agent.pk,
                    payload={"name": row.name, "prefix": row.prefix},
                )

        current = _current_key_id(request)
        return Response({**_key(row, current), "revoked_the_current_key": row.pk == current})


class TeamView(ActingAgentMixin, APIView):
    permission_classes = [CanReadOnly]

    def get(self, request):
        people = Agent.objects.order_by("-is_active", "email")
        return Response(
            {
                "data": [_agent(person, request.acting_agent) for person in people],
                "roles": [{"value": value, "label": label} for value, label in Agent.Role.choices],
                "can_manage": IsOwner().has_permission(request, self),
                "active_owners": _active_owners().count(),
            }
        )

    def post(self, request):
        _require_owner(request, self)
        from disputeshield import audit

        email = str(request.data.get("email") or "").strip().lower()
        name = str(request.data.get("display_name") or "").strip()
        role = str(request.data.get("role") or Agent.Role.AGENT).strip()

        try:
            validate_email(email)
        except DjangoValidationError:
            return _invalid(f"{email!r} is not an address.")
        if role not in Agent.Role.values:
            return _invalid(f"role must be one of: {', '.join(Agent.Role.values)}.")
        if Agent.objects.filter(email=email).exists():
            return _invalid(f"{email} is already on this team.")

        with transaction.atomic():
            person = Agent.objects.create(
                tenant=request.user.tenant,
                email=email,
                display_name=name or email,
                role=role,
            )
            audit.append(
                tenant=request.user.tenant,
                event_type="team.member_added",
                subject_type="agent",
                subject_id=person.pk,
                actor_type="user",
                actor_id=request.acting_agent.pk,
                payload={"email": email, "role": role},
            )

        return Response(_agent(person, request.acting_agent), status=201)


class TeamMemberView(ActingAgentMixin, APIView):
    permission_classes = [CanReadOnly]

    def patch(self, request, agent_id: str):
        _require_owner(request, self)
        from disputeshield import audit

        person = Agent.objects.filter(pk=agent_id).first()
        if person is None:
            raise NotFound

        if person.pk == request.acting_agent.pk and "role" in request.data:
            # Self-promotion is the obvious reason. Self-demotion is the likelier
            # accident and produces the same locked-out tenant.
            return _invalid(
                "You cannot change your own role. Ask another owner — a role you can raise is "
                "not a role, and a role you can lower by accident is a lockout."
            )

        changed = {}
        role = str(request.data.get("role") or "").strip()
        if role and role != person.role:
            if role not in Agent.Role.values:
                return _invalid(f"role must be one of: {', '.join(Agent.Role.values)}.")
            if problem := _would_strand_the_tenant(person, new_role=role):
                return _invalid(problem)
            changed["role"] = [person.role, role]
            person.role = role

        if "is_active" in request.data:
            active = bool(request.data["is_active"])
            if not active and (problem := _would_strand_the_tenant(person, deactivating=True)):
                return _invalid(problem)
            if active != person.is_active:
                changed["is_active"] = [person.is_active, active]
                person.is_active = active

        if not changed:
            return Response(_agent(person, request.acting_agent))

        with transaction.atomic():
            person.save(update_fields=["role", "is_active"])
            audit.append(
                tenant=request.user.tenant,
                event_type="team.member_changed",
                subject_type="agent",
                subject_id=person.pk,
                actor_type="user",
                actor_id=request.acting_agent.pk,
                payload={"email": person.email, "changed": changed},
            )

        return Response(_agent(person, request.acting_agent))


class RetentionView(ActingAgentMixin, APIView):
    """§11.7's position, reported rather than configured.

    The window is seven years and is not a per-tenant setting: a tenant able to
    shorten it below the mandated period would be using a settings screen to fall
    out of compliance. What is useful here is the state — what is past the
    window, what is held, and the fact that the sweep reports rather than deletes
    unless it is told otherwise.
    """

    permission_classes = [CanReadOnly]

    def get(self, request):
        from disputeshield.models import Dispute, LegalHold
        from disputeshield.retention import sweep

        cutoff = sweep.expired_before(now=timezone.now())
        expired = Dispute.objects.filter(closed_at__isnull=False, closed_at__lt=cutoff)

        return Response(
            {
                "years": sweep.RETENTION_YEARS,
                "cutoff": cutoff.isoformat(),
                "cases_past_window": expired.count(),
                "active_legal_holds": LegalHold.objects.filter(released_at__isnull=True).count(),
                "sealing_enabled": request.user.tenant.content_sealing_enabled,
                # The sweep's default. Stated because "nothing has been deleted"
                # and "the sweep has not run" look identical from a case count.
                "deletes_only_when_told": True,
            }
        )


def _would_strand_the_tenant(person: Agent, *, new_role: str = "", deactivating: bool = False):
    """The last active owner cannot be demoted or deactivated.

    There is no recovery path from a tenant with no owner: nobody left can mint a
    key, change a role, or register an embed origin. Checked here rather than in
    the dashboard because the dashboard is one client of several.
    """
    if person.role != Agent.Role.OWNER or not person.is_active:
        return None
    if new_role and new_role == Agent.Role.OWNER:
        return None
    if _active_owners().exclude(pk=person.pk).exists():
        return None

    action = "deactivate" if deactivating else "change the role of"
    return (
        f"This is the only active owner, so you cannot {action} them. A tenant with no owner "
        "cannot mint a key, change a role or register an origin, and there is no way back from "
        "that state. Promote another owner first."
    )


def _current_key_id(request) -> str | None:
    """The key this request authenticated with, when it used one.

    A session-token principal has no key, so this is None and nothing is marked
    as current — which is correct: an agent signed in through the dashboard is
    not holding the credential they are looking at.
    """
    return getattr(getattr(request.user, "api_key", None), "pk", None)


def _active_owners():
    return Agent.objects.filter(role=Agent.Role.OWNER, is_active=True)


def _require_owner(request, view) -> None:
    if not IsOwner().has_permission(request, view):
        # 404, never 403 (D8).
        raise NotFound


def _key(row: APIKey, current_id: str | None) -> dict:
    return {
        "id": row.pk,
        "name": row.name,
        "environment": row.environment,
        "kind": row.kind,
        # The prefix is stored in plaintext precisely so a key can be identified
        # here. The rest is hashed and unrecoverable.
        "prefix": row.prefix,
        "created_at": row.created_at.isoformat(),
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "is_active": row.revoked_at is None,
        # So the interface can warn before somebody revokes the credential they
        # are currently holding.
        "is_current": bool(current_id) and row.pk == current_id,
    }


def _agent(person: Agent, acting) -> dict:
    return {
        "id": person.pk,
        "email": person.email,
        "display_name": person.display_name,
        "role": person.role,
        "is_active": person.is_active,
        "created_at": person.created_at.isoformat(),
        "is_you": bool(acting) and person.pk == acting.pk,
    }


def _invalid(message: str) -> Response:
    return Response({"error": {"type": "invalid_request", "message": message}}, status=400)
