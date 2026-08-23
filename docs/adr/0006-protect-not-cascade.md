# ADR-0006 — `on_delete=PROTECT` on every relation in the evidence graph

**Status:** accepted
**Date:** 2026-08-23

## Context

Django requires an `on_delete` policy on every foreign key. `CASCADE` is the common choice and the
one most examples use: delete the parent, the children go with it, no orphans.

On a system whose product is evidence, `CASCADE` means that deleting one `Tenant` row silently
destroys every dispute, every message, every SLA event and every audit record belonging to a customer
— in one statement, with no confirmation beyond the one the operator already clicked, and with the
audit trail that would have recorded the destruction going down in the same transaction.

The regulatory obligation is seven years of retrievable complaint records (§9). A single mistaken
delete, an over-broad test fixture teardown pointed at the wrong database, or a tenant-offboarding
script written on a Friday can end that obligation instantly and unrecoverably.

## Decision

Every foreign key in the evidence graph uses `on_delete=models.PROTECT`. `Agent` references use
`SET_NULL`, because an agent leaving the company must not be able to make their own past actions
undeletable-or-deletable in either direction — the action record stays, attributed to an agent id
that no longer resolves to an active user.

Deleting a tenant with any dispute attached raises `ProtectedError`. Offboarding is a documented
procedure — export, verify, legal-hold check, retention expiry, then removal — not a row deletion.

## Consequences

- Test fixtures cannot tear down by deleting parents. Tests use transactional rollback, which they
  should anyway, and the ones that cannot are given explicit ordered cleanup.
- Genuine deletion is laborious and deliberate. That is the point: on this system, deletion should
  feel like the serious act it is.
- Data-subject erasure (§11.7) cannot be implemented as deletion, which is what pushes it toward
  crypto-shredding in amplifier A20 — the honest resolution, where the record and its chain stay
  verifiable while the content becomes unrecoverable.
- It is one word per relation, immediately legible to any Django reviewer as a statement about what
  kind of system this is.
