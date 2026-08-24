"""The provider connector interface.

**There is no write method here, and that is the whole design.** A connector
cannot accidentally gain one, because there is nothing to override: the abstract
base declares three reads and nothing else, and
`tests/test_connectors.py::test_the_interface_exposes_no_write_method`
introspects the class to keep it that way.

DisputeShield does not retry a payment, trigger a reversal or touch a rail. It
reads, records and shows. §3.3 puts moving money under permanent **Won't**, and a
connector is the one place in the product where the code is already holding
credentials that could.
"""

from __future__ import annotations

import abc
import dataclasses
from datetime import datetime

# Anything whose name suggests a state change at the provider. A subclass that
# defines one of these fails the interface test — including a subclass somebody
# writes next year.
FORBIDDEN_METHOD_HINTS = (
    "post",
    "put",
    "patch",
    "delete",
    "create",
    "update",
    "refund",
    "reverse",
    "retry",
    "charge",
    "capture",
    "void",
    "transfer",
    "payout",
    "settle",
    "submit",
    "write",
)


class ConnectorUnavailable(Exception):
    """The provider did not answer.

    Never fatal to the case. §8.6 principle 1: a case must be filable whether or
    not a third party is reachable, and a connector failure degrades the case to
    "context unavailable" rather than blocking a complaint.
    """


@dataclasses.dataclass(frozen=True)
class ProviderTransaction:
    reference: str
    status: str
    amount_minor: int | None = None
    currency: str = ""
    occurred_at: datetime | None = None
    description: str = ""


@dataclasses.dataclass(frozen=True)
class ProviderEvent:
    occurred_at: datetime
    kind: str
    summary: str
    detail: dict = dataclasses.field(default_factory=dict)


class Connector(abc.ABC):
    """Read-only by construction."""

    provider: str = "generic"

    def __init__(self, *, base_url: str = "", credential: str = "") -> None:
        self._base_url = base_url
        self._credential = credential

    @abc.abstractmethod
    def fetch_transaction(self, reference: str) -> ProviderTransaction:
        """One transaction, as the provider sees it."""

    @abc.abstractmethod
    def fetch_timeline(self, reference: str) -> list[ProviderEvent]:
        """The transaction's status transitions, including any reversal attempt.

        The answer to "did the reversal actually leave the rail?", which is the
        answer that closes the case and the one the fintech's own database
        cannot give.
        """

    @abc.abstractmethod
    def health(self) -> bool:
        """Whether the provider is answering at all."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        # Never the credential.
        return f"<{type(self).__name__} provider={self.provider} base_url={self._base_url!r}>"


def declared_methods(connector_class: type) -> set[str]:
    """Public callables a connector class introduces, for the interface test."""
    return {
        name
        for name in dir(connector_class)
        if not name.startswith("_") and callable(getattr(connector_class, name, None))
    }
