# ADR-0008 — An emailed export is rebuilt and verified at send time, never stored in the queue

**Status:** accepted
**Date:** 2026-08-24

## Context

§6.5's regulatory export is a disclosure of every case in a period. Emailing it removes the last manual
step in producing a supervisory return, and it is also the only feature in the product whose purpose is
to move that data outside the system.

Deliveries ride the notification outbox (D7), which gives retry, parking and idempotency for free. The
obvious implementation attaches the built bundle to the outbox row and sends it when the dispatcher
gets there. Two things are wrong with that:

- The queue table becomes a store of complete case content, in a shape no other part of the system
  treats as case content — outside the retention sweep, outside the residency rules, and readable by
  anything that can read a queue.
- A bundle built at request time and sent minutes later is a claim about a period made at one moment
  and delivered as though it were made at another. If the period changed in between, the recipient
  receives a document whose digests disagree with the ones the requester was told to expect, and
  nothing detects it.

The alternative — rebuild at send time and send whatever comes out — has the second problem without
the first: the recipient still gets something other than what was requested, and still nothing notices.

## Decision

The outbox row carries the period, the recipients and the **digests the export had when it was
requested**. It carries no case content at all.

At send time the dispatcher rebuilds the bundle and compares its digests against the promise. Identical
digests mean the export is sent. Different digests mean the period changed between request and
delivery, and the delivery **fails** — loudly, into the outbox's existing retry and parking, rather
than sending a bundle nobody promised.

This is only possible because the export is byte-reproducible, and it is the first thing in the system
that depends on that property rather than merely asserting it.

## Consequences

- Case content never sits in a queue table. The outbox payload is a period, a list of addresses and a
  list of hex digests.
- A changed period refuses instead of sending. That is the intended behaviour: a supervisor who
  receives a bundle whose digests disagree with the ones they were promised has been handed a reason to
  doubt all of it, and a late report is a smaller problem than an unexplained one.
- A refusal is visible. It retries, then parks, and a parked delivery is something an operator can see
  — unlike a send that quietly differed.
- The export must **stay** byte-reproducible for a closed period, which is a stronger constraint than
  it first appears. It cost one real defect during implementation: the PDF's cover page printed the
  tenant's live audit-chain head and running record count, so a closed period produced different bytes
  whenever anything at all was written anywhere in the tenant — including the audit record that
  requesting the delivery writes for itself. Every emailed report would have refused to send. Facts
  about *now* are published in `manifest.json` and at `GET /v1/audit/verify`; the document contains
  facts about the *period*.
- The reproducibility gate had to get stronger to match. Building twice in a row cannot see this class
  of defect; `tests/test_pdf_report.py` now builds either side of an unrelated audit write, and
  separately asserts that a period which genuinely gains a case *does* produce a different document —
  so reproducibility cannot be achieved by ignoring changes.
- Delivery is idempotent on (period, recipients), so a retried request during an incident cannot page a
  regulator's inbox twice.
