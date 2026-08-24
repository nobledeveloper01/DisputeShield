"""Variable substitution for response templates (§3.3 B3).

Deliberately **not** a template language. A compliance officer edits these in a
dashboard, and Django's own engine — or Jinja, or anything with the same shape —
gives whoever can write a template the ability to walk attribute chains into the
object graph. `{{ dispute.tenant.api_keys.first.key_hash }}` renders in a real
template engine, and the person who wrote it is not required to be an engineer or
to be honest.

So: a fixed set of names, `{{ name }}` and nothing else, no filters, no attribute
access, no conditionals, no loops. A template that asks for a name outside the
set renders the literal placeholder rather than an empty string — an empty string
is how a customer receives "Dear ," and nobody notices until they do.
"""

from __future__ import annotations

import dataclasses
import re

PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")

# Every name a template may use. Adding one is a decision about what a customer
# may be told, which is why the list is here and not derived from the model.
ALLOWED_VARIABLES = frozenset(
    {
        "customer_name",
        "reference",
        "category",
        "status",
        "transaction_ref",
        "amount",
        "currency",
        "expected_resolution_at",
        "agent_name",
        "tenant_name",
    }
)


@dataclasses.dataclass(frozen=True)
class Rendered:
    body: str
    unknown: tuple[str, ...]
    missing: tuple[str, ...]


class UnknownVariable(ValueError):
    """A template references a name outside the allowlist."""


def validate(body: str) -> tuple[str, ...]:
    """Names used by a template that are not permitted. Empty means valid."""
    return tuple(sorted({n for n in PLACEHOLDER.findall(body) if n not in ALLOWED_VARIABLES}))


def render(body: str, context: dict[str, object]) -> Rendered:
    unknown: set[str] = set()
    missing: set[str] = set()

    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name not in ALLOWED_VARIABLES:
            unknown.add(name)
            return match.group(0)
        if name not in context or context[name] in (None, ""):
            missing.add(name)
            return match.group(0)
        # Always a string, never the object. A value that renders as
        # `<Dispute: …>` is a value that leaked a repr into a customer's inbox.
        return str(context[name])

    return Rendered(
        body=PLACEHOLDER.sub(substitute, body),
        unknown=tuple(sorted(unknown)),
        missing=tuple(sorted(missing)),
    )


def context_for(dispute, *, agent_name: str = "") -> dict[str, object]:
    """The values a template may see. Nothing internal appears here.

    This function is the second half of the §10 guarantee that internal content
    cannot reach a customer: the widget serializer closes the field path, and
    this closes the substitution path. `outcome_notes`, `breach_reason` and the
    assigned agent's identity are all absent, and the leakage test asserts it.
    """
    amount = None
    if dispute.amount_minor is not None:
        amount = f"{dispute.amount_minor / 100:.2f}"

    return {
        "customer_name": dispute.customer_display_name,
        "reference": dispute.reference,
        "category": dispute.category.replace("_", " "),
        "status": dispute.get_status_display(),
        "transaction_ref": dispute.transaction_ref,
        "amount": amount,
        "currency": dispute.currency,
        "expected_resolution_at": dispute.resolution_deadline.isoformat(),
        "agent_name": agent_name,
        "tenant_name": dispute.tenant.name,
    }
