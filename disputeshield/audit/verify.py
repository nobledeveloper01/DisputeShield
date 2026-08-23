"""Chain verification.

Walks a tenant's records in sequence order, recomputing each hash and checking
that it links to its predecessor. This backs `GET /v1/audit/verify`, which exists
so a customer or their auditor can check the claim independently rather than
taking our word for it — a proof only we can run is a promise, not a proof.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator

from django.db import transaction

from disputeshield.audit.chain import GENESIS, compute_hash, record_content
from disputeshield.models import AuditRecord
from disputeshield.tenancy.middleware import db_tenant_context


@dataclasses.dataclass(frozen=True)
class Failure:
    sequence: int
    record_id: str
    reason: str


@dataclasses.dataclass(frozen=True)
class Result:
    tenant_id: str
    records_checked: int
    failures: tuple[Failure, ...]

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def first_break(self) -> int | None:
        """Where the chain first breaks — that is, where tampering began."""
        return self.failures[0].sequence if self.failures else None


def verify_tenant(tenant_id: str, *, batch_size: int = 1000) -> Result:
    # Verification is a platform operation with no ambient request context, so it
    # establishes its own — inside a transaction, because SET LOCAL needs one.
    with transaction.atomic(), db_tenant_context(tenant_id):
        return _verify(tenant_id, batch_size)


def _verify(tenant_id: str, batch_size: int) -> Result:
    failures: list[Failure] = []
    expected_prev = GENESIS
    expected_sequence = 1
    checked = 0

    for record in _iter_records(tenant_id, batch_size):
        checked += 1

        if record.sequence != expected_sequence:
            failures.append(
                Failure(
                    record.sequence,
                    record.pk,
                    f"sequence gap: expected {expected_sequence}, found {record.sequence}. "
                    "A missing sequence number is a deleted record.",
                )
            )
            expected_sequence = record.sequence

        if record.prev_hash != expected_prev:
            failures.append(
                Failure(record.sequence, record.pk, "prev_hash does not match the preceding record")
            )

        # Recompute against the *expected* predecessor rather than the stored
        # `prev_hash`, and carry the recomputed value forward. That single choice
        # is what makes §8.3's claim ("tampering anywhere invalidates every record
        # after it") literally true: once one record's content changes, every
        # later record's hash is computed from a different chain than the one it
        # was written against, so all of them fail.
        #
        # Verifying each record against its own stored prev_hash would flag only
        # the edited row — and one altered record in a million-record chain is
        # precisely the tampering most worth catching.
        recomputed = compute_hash(record_content(record), expected_prev)
        if recomputed != record.hash:
            failures.append(
                Failure(
                    record.sequence,
                    record.pk,
                    "content does not match its own hash — record altered",
                )
            )

        expected_prev = recomputed
        expected_sequence += 1

    return Result(tenant_id=tenant_id, records_checked=checked, failures=tuple(failures))


def _iter_records(tenant_id: str, batch_size: int) -> Iterator[AuditRecord]:
    # all_tenants() then an explicit tenant filter: verification is a platform
    # operation that runs outside any request, so there is no ambient context.
    queryset = AuditRecord.objects.all_tenants().filter(tenant_id=tenant_id).order_by("sequence")
    return queryset.iterator(chunk_size=batch_size)
