"""Prefixed, random, non-enumerable identifiers.

§10 lists dispute ID enumeration as a threat and answers it with random
identifiers plus a 404 on unauthorised access. Sequential integer primary keys
would defeat the first half of that answer, so nothing user-facing gets one.

The prefix is not decoration: it means a stray identifier in a log line, a
support ticket or a bug report is immediately attributable to a type, which is
worth more than the four bytes it costs.
"""

from __future__ import annotations

import secrets

# Crockford base32 without I, L, O and U — no character pair that a human
# transcribing an identifier from a screen into a ticket can confuse.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_id(prefix: str, length: int = 22) -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(length))
    return f"{prefix}_{body}"


def tenant_id() -> str:
    return new_id("tnt")


def agent_id() -> str:
    return new_id("agt")


def api_key_id() -> str:
    return new_id("key")


def audit_id() -> str:
    return new_id("aud")


def dispute_id() -> str:
    return new_id("dsp")


# Named rather than lambdas: Django serialises a field's `default` into the
# migration file, and a lambda cannot be serialised. Discovering that during
# `makemigrations` is cheap; discovering it after a model is in production and
# the migration graph has moved on is not.
def calendar_id() -> str:
    return new_id("cal")


def policy_id() -> str:
    return new_id("pol")


def policy_version_id() -> str:
    return new_id("plv")


def clock_id() -> str:
    return new_id("clk")


def sla_event_id() -> str:
    return new_id("sev")


def deadline_id() -> str:
    return new_id("dln")


def notification_id() -> str:
    return new_id("ntf")


def checkpoint_id() -> str:
    return new_id("ckp")


def ingest_address_id() -> str:
    return new_id("ing")


def inbound_message_id() -> str:
    return new_id("inb")


def incident_id() -> str:
    return new_id("inc")


def mass_event_id() -> str:
    return new_id("mev")


def membership_id() -> str:
    return new_id("mem")


def contact_id() -> str:
    return new_id("con")


def legal_hold_id() -> str:
    return new_id("hld")


def anchor_id() -> str:
    return new_id("anc")


def escalation_id() -> str:
    return new_id("esc")


def return_template_id() -> str:
    return new_id("rtt")


def regulatory_return_id() -> str:
    return new_id("ret")


def erasure_request_id() -> str:
    return new_id("ers")


def representment_id() -> str:
    return new_id("rep")


def connector_id() -> str:
    return new_id("cnx")


def provider_call_id() -> str:
    return new_id("pcl")


def settlement_id() -> str:
    return new_id("stl")


def suggestion_id() -> str:
    return new_id("sug")


def cluster_id() -> str:
    return new_id("clu")


def signal_id() -> str:
    return new_id("sig")


def message_id() -> str:
    return new_id("msg")


def idempotency_record_id() -> str:
    return new_id("idm")


def generate_api_key(environment: str, kind: str = "secret") -> tuple[str, str]:
    """Return (full_key, prefix).

    Secret keys are `ds_{env}_{random32}` per §8.2; publishable keys are
    `pk_{env}_{random32}`. The two prefixes are visually distinct on purpose — a
    secret key pasted into a public page is the mistake this naming exists to make
    obvious at a glance, in a code review and in a leaked-secret scan.

    The prefix is stored in plaintext so a key can be looked up and displayed in
    the dashboard; the remainder is Argon2id-hashed and never retrievable.
    """
    if environment not in {"test", "live"}:
        raise ValueError(f"environment must be 'test' or 'live', got {environment!r}")
    if kind not in {"secret", "publishable"}:
        raise ValueError(f"kind must be 'secret' or 'publishable', got {kind!r}")
    body = secrets.token_urlsafe(32)
    full = f"pk_{environment}_{body}" if kind == "publishable" else f"ds_{environment}_{body}"
    return full, full[:16]
