"""Refusing a draft that says something its sources do not (amplifier A12).

A drafted reply that invents a refund date is a **commitment made to a customer on
the firm's behalf by a system with no authority to make it**. In a regulated
complaints process that commitment is quotable back at the firm, and the firm has
no record of deciding it.

So a draft containing a date, an amount or a commitment that is absent from its
retrieved sources is **blocked from insertion, not flagged**. A warning next to a
draft is a warning an agent under queue pressure clicks past; a block is a thing
they have to resolve.

The three claim types are not arbitrary. They are the three a customer will hold
the firm to: *when* you will do it, *how much*, and *that you will*.
"""

from __future__ import annotations

import dataclasses
import re
from decimal import Decimal, InvalidOperation

# Amounts. Deliberately broad: "₦50,000", "50000", "NGN 50,000.00", "50k".
# The trailing guard is `(?!\w)(?!\.\d)` rather than `(?![\w.])`: it must reject a
# number that *continues* ("1.234.567"), not a sentence that ends. The stricter
# form rejected the most ordinary way an amount is written — last thing before a
# full stop — and a weaker alternative below then matched a prefix, so "₦9,000."
# was extracted as the claim "₦9". The gate compared the wrong value and, because
# a lone "9" turns up in almost any text, called it supported.
AMOUNT = re.compile(
    r"(?<![\w.])(?:[₦$€£]\s?)?\d{1,3}(?:[,\s]\d{3})+(?:\.\d{1,2})?(?!\w)(?!\.\d)"
    r"|(?<![\w.])(?:[₦$€£]\s?)\d+(?:\.\d{1,2})?(?!\w)(?!\.\d)"
    r"|(?<![\w.])\d+(?:\.\d{1,2})?\s?(?:k|K)(?!\w)(?!\.\d)"
    r"|(?<![\w.])\d{4,}(?:\.\d{1,2})?(?!\w)(?!\.\d)"
)

# Dates and relative deadlines. A customer holds the firm to "by Friday" exactly
# as firmly as to "by 24 August".
DATE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"
    r"|\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\b"
    r"|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}\b"
    r"|\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b"
    r"|\b(?:today|tomorrow|tonight)\b"
    r"|\bwithin\s+\d+\s+(?:hour|hours|day|days|week|weeks|working\s+days|business\s+days)\b"
    r"|\bin\s+\d+\s+(?:hour|hours|day|days|week|weeks)\b",
    re.IGNORECASE,
)

# Promises. Matched on the phrase rather than on sentiment, because the question
# is whether the firm committed, not whether the sentence sounded positive.
COMMITMENT = re.compile(
    r"\b(?:we\s+will|we'll|we\s+guarantee|you\s+will\s+receive|will\s+be\s+refunded"
    r"|will\s+be\s+credited|will\s+be\s+reversed|is\s+guaranteed|we\s+promise"
    r"|rest\s+assured|you\s+can\s+expect)\b",
    re.IGNORECASE,
)


class UngroundedDraft(ValueError):
    """The draft asserts something its sources do not support.

    Raised rather than returned. A caller that has to remember to check a flag is
    a caller that eventually forgets, and the thing they forget is a promise to a
    customer.
    """

    def __init__(self, claims: tuple[Claim, ...]) -> None:
        self.claims = claims
        detail = "; ".join(f"{c.kind}: {c.text!r}" for c in claims)
        super().__init__(
            "This draft states something its sources do not support and cannot be "
            f"inserted: {detail}. Edit the draft, or add the fact to the case."
        )


@dataclasses.dataclass(frozen=True)
class Claim:
    kind: str
    text: str


@dataclasses.dataclass(frozen=True)
class GroundingResult:
    grounded: bool
    unsupported: tuple[Claim, ...]


def extract_claims(text: str) -> tuple[Claim, ...]:
    """Every dated, priced or promised assertion in a draft."""
    claims = [Claim("amount", match.group(0).strip()) for match in AMOUNT.finditer(text or "")]
    claims += [Claim("date", match.group(0).strip()) for match in DATE.finditer(text or "")]
    claims += [
        Claim("commitment", match.group(0).strip()) for match in COMMITMENT.finditer(text or "")
    ]
    # Deduplicate on the pair, preserving order, so one repeated amount is one claim.
    seen: set[tuple[str, str]] = set()
    unique: list[Claim] = []
    for claim in claims:
        key = (claim.kind, claim.text.lower())
        if key not in seen:
            seen.add(key)
            unique.append(claim)
    return tuple(unique)


def check(draft: str, sources: list[str]) -> GroundingResult:
    """Which of the draft's claims the sources do not support.

    A commitment is unsupported unless the *same* commitment appears in a source.
    That is strict on purpose: "we will refund you" is not made true by the case
    containing the word "refund", and a looser rule is one that lets the model
    promise things because a related word was nearby.
    """
    haystack = _normalise(" \n ".join(sources))
    unsupported: list[Claim] = []

    for claim in extract_claims(draft):
        needle = _normalise(claim.text)
        if claim.kind == "amount":
            claimed = _amounts(needle)
            if claimed and claimed <= _amounts(haystack):
                continue
        elif needle in haystack:
            continue
        unsupported.append(claim)

    return GroundingResult(grounded=not unsupported, unsupported=tuple(unsupported))


def enforce(draft: str, sources: list[str]) -> str:
    """Return the draft, or refuse it. The only supported way to accept one."""
    result = check(draft, sources)
    if not result.grounded:
        raise UngroundedDraft(result.unsupported)
    return draft


def _normalise(text: str) -> str:
    return " ".join((text or "").lower().split())


def _amounts(text: str) -> set[Decimal]:
    """The numbers in a text, one value per token.

    Number by number, deliberately. Stripping every non-digit from the joined
    sources produces a single long digit string, and a substring search over it
    matches across boundaries that mean nothing: a draft promising ₦9,000 was
    judged supported because a case reference ran into a deadline's microseconds
    and the run of digits happened to contain 9000. The gate then passed a
    financial commitment the case did not support, which is the one thing it
    exists to prevent.

    Comparing values rather than digit strings also keeps "50,000" matching
    "50000" and "9,000.00" matching "9,000", which is why the strict form is
    affordable.
    """
    values: set[Decimal] = set()
    for token in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            values.add(Decimal(token.replace(",", "")))
        except InvalidOperation:
            continue
    return values
