# ADR-0002 — Session tokens are opaque and Redis-backed, not JWTs

**Status:** accepted
**Date:** 2026-08-23

## Context

§4.3 has the fintech's backend mint a session token scoped to exactly one `customer_ref`, with a
30-minute TTL, which the widget then presents on every request. A JWT is the reflexive choice:
self-contained, no lookup, no shared state.

It is also unrevocable. A JWT is valid until it expires because validity is a property of the
signature, not of any record we control. If a token leaks — a customer's device is compromised, a
support engineer pastes a session into a ticket, a browser extension exfiltrates it — the available
responses are to wait thirty minutes or to rotate the signing key and invalidate every session for
every customer of every tenant.

Thirty minutes of unrevocable access to one customer's dispute history is a small breach. It is still
a breach we would have to describe to a regulator as one we watched happen.

## Decision

Tokens are opaque: `dst_` plus 32 bytes of CSPRNG output. Session state lives in Redis under the
SHA-256 of the token, holding tenant, `customer_ref` scope, the transaction list supplied at mint
time, the issuing API key and expiry.

Revocation is a delete, at three granularities: one session, every session for one customer, every
session minted by one API key. The last one matters — it is the response to a leaked secret key, and
it is available immediately rather than after a key rotation completes.

The token is stored hashed for the same reason API keys are: a Redis snapshot in a backup, a log
line, or an operator's `KEYS` output should not contain anything usable.

## Consequences

- One Redis round trip per widget request, which puts Redis on the widget's availability path.
  Accepted, because §8.6 principle 1 already requires the widget to fail closed and quietly: a Redis
  outage means the widget does not render, the host page is untouched, and the documented degradation
  is the one that happens.
- Session state is bounded and self-expiring via Redis TTL, so there is no cleanup job to forget.
- Horizontal scaling of the widget API requires shared Redis, which the deployment already has.
- The transaction list supplied at mint time (§7.1) has to live somewhere anyway. A JWT would have to
  carry it in the token, producing multi-kilobyte tokens on every request and putting the customer's
  recent transaction history into a bearer credential that travels through client-side storage.
  Opaque tokens keep it server-side, which is where §8.4's minimisation argument says it belongs.
