"""§3.4's state machine, as data rather than as scattered `if` statements.

Written as a table for one reason: the tests enumerate it. A transition added in
a later phase is automatically covered by the assertion that *every* transition
writes an audit record naming the actor, the reason and the state of the SLA
clock at that instant — rather than being covered only if somebody remembered to
add a test alongside it.

The clock effect of each transition lives here too. It is the part most easily
got wrong by reading the diagram: `awaiting_customer` pauses, and returning from
it resumes, and nothing else touches the clock.
"""

from __future__ import annotations

import dataclasses
from enum import Enum

from disputeshield.models.dispute import Status


class ClockEffect(Enum):
    NONE = "none"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"


@dataclasses.dataclass(frozen=True)
class Transition:
    source: str
    target: str
    trigger: str
    clock_effect: ClockEffect = ClockEffect.NONE
    # A transition that stops a clock or pauses one changes what the firm owes
    # the customer, so it may not happen anonymously or unexplained.
    requires_reason: bool = False
    actor_types: tuple[str, ...] = ("user", "system", "api_key")

    @property
    def key(self) -> tuple[str, str]:
        return (self.source, self.target)


TRANSITIONS: tuple[Transition, ...] = (
    Transition(Status.SUBMITTED, Status.ACKNOWLEDGED, "acknowledge"),
    Transition(Status.ACKNOWLEDGED, Status.INVESTIGATING, "pick_up"),
    Transition(Status.SUBMITTED, Status.INVESTIGATING, "pick_up"),
    Transition(
        Status.INVESTIGATING,
        Status.AWAITING_CUSTOMER,
        "request_information",
        clock_effect=ClockEffect.PAUSE,
        requires_reason=True,
    ),
    Transition(
        Status.AWAITING_CUSTOMER,
        Status.INVESTIGATING,
        "customer_responded",
        clock_effect=ClockEffect.RESUME,
        requires_reason=True,
    ),
    Transition(
        Status.AWAITING_CUSTOMER,
        Status.AUTO_CLOSED,
        "auto_close",
        clock_effect=ClockEffect.STOP,
        requires_reason=True,
        actor_types=("system",),
    ),
    Transition(Status.INVESTIGATING, Status.ESCALATED, "escalate", requires_reason=True),
    Transition(Status.ESCALATED, Status.INVESTIGATING, "de_escalate", requires_reason=True),
    Transition(
        Status.INVESTIGATING,
        Status.RESOLVED,
        "resolve",
        clock_effect=ClockEffect.STOP,
        requires_reason=True,
    ),
    Transition(
        Status.ESCALATED,
        Status.RESOLVED,
        "resolve",
        clock_effect=ClockEffect.STOP,
        requires_reason=True,
    ),
    Transition(Status.RESOLVED, Status.REOPENED, "reopen", requires_reason=True),
    Transition(Status.REOPENED, Status.INVESTIGATING, "pick_up"),
    Transition(
        Status.RESOLVED,
        Status.CLOSED,
        "close",
        clock_effect=ClockEffect.STOP,
        actor_types=("system", "user"),
    ),
)

TERMINAL = frozenset({Status.CLOSED, Status.AUTO_CLOSED})

_BY_KEY = {transition.key: transition for transition in TRANSITIONS}


class IllegalTransition(ValueError):
    """A move the state machine does not permit."""


def find(source: str, target: str) -> Transition:
    transition = _BY_KEY.get((source, target))
    if transition is None:
        allowed = sorted(t.target for t in TRANSITIONS if t.source == source)
        raise IllegalTransition(
            f"{source} → {target} is not a permitted transition. "
            f"From {source} a case may move to: {allowed or 'nothing — it is terminal'}."
        )
    return transition


def targets_from(source: str) -> tuple[str, ...]:
    return tuple(t.target for t in TRANSITIONS if t.source == source)
