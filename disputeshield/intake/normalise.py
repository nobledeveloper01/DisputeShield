"""Turning whatever a channel sends into one shape.

Every channel produces the same `Inbound` value, and everything downstream —
matching, quarantine, filing — works on that value alone. The roadmap's exit gate
is that a case filed by WhatsApp is indistinguishable from one filed by the
widget: same clock, same audit shape, same isolation. That is only cheap to
guarantee if there is exactly one path after this module.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime

from django.utils import timezone

from disputeshield.models import Channel

# A case reference as a customer would quote it back: "DS-2026-8AJNKJ".
REFERENCE = re.compile(r"\bDS-\d{4}-[0-9A-HJKMNP-TV-Z]{6}\b")

# Auto-replies and bounces. Appending an out-of-office to a complaint thread is
# noise; treating one as a customer's response would resume a paused clock on the
# strength of a mail server's holiday message.
AUTO_REPLY_HINTS = (
    "auto-submitted",
    "x-autoreply",
    "x-autorespond",
    "out of office",
    "automatic reply",
    "delivery status notification",
    "undeliverable",
    "mail delivery failed",
)


class UnsupportedChannel(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Inbound:
    channel: str
    from_identity: str
    body: str
    received_at: datetime
    subject: str = ""
    thread_key: str = ""
    quoted_reference: str = ""
    is_auto_reply: bool = False
    transaction_ref: str = ""
    category: str = ""


def normalise(channel: str, payload: dict) -> Inbound:
    if channel not in Channel.values:
        raise UnsupportedChannel(f"{channel!r} is not a channel DisputeShield accepts.")
    return _ADAPTERS[channel](payload)


def _email(payload: dict) -> Inbound:
    headers = {k.lower(): v for k, v in (payload.get("headers") or {}).items()}

    # `From` is the envelope sender, never the display name. A display name is
    # attacker-chosen text — "DisputeShield Support <attacker@evil.example>" —
    # and matching on it is how a thread gets hijacked.
    sender = _address_only(payload.get("from", ""))

    # The thread root: the first Message-ID in References, else In-Reply-To, else
    # this message's own id. Deliberately not the subject line, which every
    # client rewrites and which two unrelated customers can share.
    references = (headers.get("references") or "").split()
    thread_key = (
        references[0]
        if references
        else headers.get("in-reply-to") or headers.get("message-id") or ""
    )

    body = payload.get("body", "")
    blob = " ".join(
        [body, payload.get("subject", ""), *headers.keys(), *map(str, headers.values())]
    )

    return Inbound(
        channel=Channel.EMAIL,
        from_identity=sender,
        subject=payload.get("subject", ""),
        body=body,
        thread_key=thread_key.strip("<> "),
        received_at=_received(payload),
        quoted_reference=_reference_in(body, payload.get("subject", "")),
        is_auto_reply=_looks_automated(blob),
    )


def _whatsapp(payload: dict) -> Inbound:
    return Inbound(
        channel=Channel.WHATSAPP,
        from_identity=_digits(payload.get("from", "")),
        body=payload.get("text", ""),
        thread_key=payload.get("conversation_id", ""),
        received_at=_received(payload),
        quoted_reference=_reference_in(payload.get("text", "")),
    )


def _ussd(payload: dict) -> Inbound:
    return Inbound(
        channel=Channel.USSD,
        from_identity=_digits(payload.get("msisdn", "")),
        body=payload.get("text", ""),
        # A USSD session is the conversation. It ends when the session does, so a
        # later session is a new conversation rather than a reply to this one.
        thread_key=payload.get("session_id", ""),
        received_at=_received(payload),
        category=payload.get("category", ""),
    )


def _phone(payload: dict) -> Inbound:
    """A call log written by the agent who took the call.

    The agent is the author, the customer is the subject. Recording the agent's
    summary as though the customer wrote it would put words in a complainant's
    mouth in a record a regulator reads.
    """
    return Inbound(
        channel=Channel.PHONE,
        from_identity=_digits(payload.get("caller", "")),
        body=payload.get("summary", ""),
        thread_key=payload.get("call_id", ""),
        received_at=_received(payload),
        quoted_reference=_reference_in(payload.get("summary", "")),
        category=payload.get("category", ""),
    )


def _social(payload: dict) -> Inbound:
    return Inbound(
        channel=Channel.SOCIAL,
        from_identity=payload.get("handle", "").lstrip("@"),
        body=payload.get("text", ""),
        thread_key=payload.get("conversation_id", ""),
        received_at=_received(payload),
        quoted_reference=_reference_in(payload.get("text", "")),
    )


def _web_form(payload: dict) -> Inbound:
    return Inbound(
        channel=Channel.WEB_FORM,
        from_identity=_address_only(payload.get("email", "")),
        body=payload.get("message", ""),
        received_at=_received(payload),
        transaction_ref=payload.get("transaction_ref", ""),
        category=payload.get("category", ""),
    )


def _widget(payload: dict) -> Inbound:
    return Inbound(
        channel=Channel.WIDGET,
        from_identity=payload.get("customer_ref", ""),
        body=payload.get("description", ""),
        received_at=_received(payload),
        transaction_ref=payload.get("transaction_ref", ""),
        category=payload.get("category", ""),
    )


_ADAPTERS = {
    Channel.EMAIL: _email,
    Channel.WHATSAPP: _whatsapp,
    Channel.USSD: _ussd,
    Channel.PHONE: _phone,
    Channel.SOCIAL: _social,
    Channel.WEB_FORM: _web_form,
    Channel.WIDGET: _widget,
}


def _address_only(value: str) -> str:
    """`"Support" <a@b.test>` becomes `a@b.test`. The display name is discarded."""
    match = re.search(r"<([^>]+)>", value or "")
    return (match.group(1) if match else (value or "")).strip().lower()


def _digits(value: str) -> str:
    """A phone number in one form. `+234 801 234 5678` and `2348012345678` are
    the same customer, and treating them as two is how a reply lands in review."""
    return re.sub(r"\D", "", value or "")


def _reference_in(*texts: str) -> str:
    for text in texts:
        match = REFERENCE.search(text or "")
        if match:
            return match.group(0)
    return ""


def _looks_automated(blob: str) -> bool:
    lowered = (blob or "").lower()
    return any(hint in lowered for hint in AUTO_REPLY_HINTS)


def _received(payload: dict) -> datetime:
    value = payload.get("received_at")
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return timezone.now()
