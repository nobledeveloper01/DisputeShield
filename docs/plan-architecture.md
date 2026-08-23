# Architecture plan

The product specification (`docs/product-specification.md`) fixes the shape of the system: a
sandboxed cross-origin iframe widget, server-minted customer-scoped session tokens, a
business-hours-aware SLA engine built on a pure deadline function, an append-only hash-chained audit
trail enforced at the database level, three-layer tenancy, and `PROTECT` rather than `CASCADE`
throughout.

This document decides the things the specification names but leaves open, and records the places
where following the specification literally would not meet its own stated goals. Each decision
carries its consequence, because a decision without a stated cost is a decision nobody checked.

---

## D1 — SLA policies are versioned and immutable; a dispute pins a version

**Problem.** §5 gives `Dispute` a foreign key to `SLAPolicy`, and §6.5 lets a compliance officer edit
a policy from the dashboard without a deploy. Both are right on their own and wrong together. Editing
a policy retroactively changes the standard every open case is being judged against, and — because
`ack_deadline` and `resolution_deadline` are stored columns — silently desynchronises stored
deadlines from the policy that supposedly produced them.

The failure is quiet and total. A supervisor asks why a case breached. The record shows a 48-hour
window. The case was actually run against the 72-hour window that was in force at the time, and that
number no longer exists anywhere. The audit trail is intact and the answer it gives is wrong, which
is worse than not having one.

**Decision.** `SLAPolicy` is immutable once referenced. An edit creates `SLAPolicyVersion` n+1;
`Dispute.sla_policy_version` is a `PROTECT` foreign key to the exact version in force at filing time.
The dashboard edits a policy; the system writes a version. Every version records its author, the time
it took effect, and its `regulatory_reference`.

**Consequence.** One more table and a version-resolution step at filing. In return, §6.5's promise
that policy changes are "recorded, versioned and visible next to the breach data they affect" becomes
structurally true rather than a reporting feature, and phase 11's simulator (A9) gets historical
policy state for free — it cannot replay history correctly without this, so building it later would
mean building this anyway, retroactively, against data that no longer supports it.

Recorded in ADR-0004.

---

## D2 — RLS session variables must be set with `SET LOCAL`, inside the transaction

**Problem.** §8.1 layer 3 is Postgres RLS keyed on a session variable set from the authenticated
tenant. The obvious implementation issues `SET disputeshield.tenant_id = '...'` after checking out a
connection. Under PgBouncer in transaction-pooling mode — which is what any Django deployment at
scale runs — a connection is returned to the pool at transaction end and handed to a different
request, **carrying that session variable with it**.

The result is a cross-tenant data leak produced by the layer that exists specifically to prevent
cross-tenant data leaks, appearing only under connection reuse, which means only under load, which
means only in production.

**Decision.** Tenant context is established with `SET LOCAL` inside an explicit transaction, on every
request, in middleware that wraps the entire request in `ATOMIC_REQUESTS`. `SET LOCAL` is scoped to
the transaction and is discarded on commit or rollback, so a pooled connection cannot carry it
anywhere.

Read-only analytics and export paths hit the replica and take the same treatment; a replica with no
tenant context set returns zero rows, which is the correct failure.

**Consequence.** Every request runs in a transaction, including reads. Long-running exports must be
chunked so they do not hold one open. The connection-reuse leak is covered by a test that runs the
isolation suite through PgBouncer in transaction-pooling mode rather than against Postgres directly —
against Postgres directly the bug is invisible, which is exactly why it survives review.

Recorded in ADR-0005.

---

## D3 — The hash chain is serialised per tenant by an advisory lock, and the chain is the write path

**Problem.** §8.3 requires each audit record's hash to cover the previous record's hash, per tenant.
That is a strict serial ordering over a table that every write path in the product appends to. Two
concurrent writes reading the same `prev_hash` produce two records claiming the same predecessor —
a fork. A fork is indistinguishable from tampering, and it will be found by the nightly verifier at
03:00 on a day when nothing was actually wrong.

**Decision.** Appending takes a Postgres transaction-scoped advisory lock keyed on the tenant id
(`pg_advisory_xact_lock`), reads the tenant's current head, writes the record, and releases on
commit. The lock is per tenant, so tenants never contend with each other. Within a tenant, audit
appends are serial by design — which is what "chain" means, and pretending otherwise is how the fork
gets built.

Rejected alternative: an asynchronous chainer that appends unchained and links records later. It
removes the write-path cost and introduces a window in which records exist outside the chain, which
is precisely the window an insider would use. §8.3's claim is that tampering invalidates everything
after it; that claim requires there be no unchained state to tamper with.

**Consequence.** A hard ceiling on audit writes per tenant per second, measured in phase 1 and
published in the operations documentation. The measured number is on the order of a thousand per
second, against a workload where a busy case generates a handful of records a day — so the ceiling is
three orders of magnitude clear of the demand. Mass-incident fan-out (A3) is the one workload that
approaches it, and phase 7 batches its appends within a single lock acquisition rather than taking
the lock five thousand times.

Recorded in ADR-0003.

---

## D4 — Session tokens are opaque and Redis-backed, not JWTs

**Problem.** §4.3 mints a 30-minute session token scoped to one customer. A JWT is the reflexive
choice and it cannot be revoked. Thirty minutes of unrevocable access to a customer's dispute history
is thirty minutes during which a leaked token is useful to whoever holds it, and the only available
response is to rotate a signing key and log every session out.

**Decision.** `dst_` tokens are opaque random strings. State lives in Redis under the token's hash,
holding tenant, `customer_ref` scope, the supplied transaction list, the issuing key and expiry.
Revocation is a delete. A tenant can revoke one session, every session for one customer, or every
session minted by one API key.

**Consequence.** A Redis round trip on every widget request, and Redis is now on the widget's
availability path. Accepted: the widget already fails closed and silently by §8.6 principle 1, so a
Redis outage degrades to "the widget does not load", which is the documented behaviour and does not
touch the host page. The alternative trades a revocation capability the product needs for a
dependency removal it does not.

---

## D5 — Encrypted case content means search is a blind index, not `LIKE`

**Problem.** §8.4 puts dispute descriptions, messages and transaction references in the
store-encrypted class under envelope encryption. §6.5 gives agents a queue they filter and, in
practice, search. Encrypted columns cannot be searched with SQL predicates, and this is the kind of
requirement that gets discovered in phase 3 by an engineer who then quietly decrypts a column to make
the feature work.

**Decision.** Structured filters — status, category, assignee, amount band, date — run against
plaintext columns, none of which are in the encrypted class. Free-text search runs against a
per-tenant blind index: content is tokenised, each token HMAC'd with the tenant's search key, and the
resulting digests are indexed. Search HMACs the query terms and matches digests.

Amount range queries are served by `amount_minor`, which stays plaintext. It is a value, not an
identifier, and it is meaningless without the case it belongs to.

**Consequence.** Exact-token search only — no substring, no fuzzy, no stemming across languages. This
is a real product limitation and it is documented in the agent workspace rather than hidden: the
search box states that it matches whole words. Blind indexes leak token frequency to anyone who can
read the index, which is mitigated by the index being per-tenant and inside the same trust boundary
as the ciphertext it describes. Any future move to richer search is a move to a searchable encryption
scheme, not a decision to decrypt.

---

## D6 — The sweep is watermark-driven and touches only cases near a boundary

**Problem.** §4.4 sweeps every minute for cases crossing a warning threshold or a breach boundary,
and §11.3 sets the tightest SLO in the product on sweep freshness. The naive implementation loads
every open dispute and evaluates its thresholds. At the §11.9 load target of 10,000 open disputes
that is 10,000 rows a minute per tenant, and the sweep's cost grows with the size of the queue rather
than with the number of events in it — so the compliance clock gets least reliable exactly when the
customer has most cases.

**Decision.** Warning and breach instants are **computed at filing time** and stored in a narrow
`SLADeadline` table: `(dispute_id, tenant_id, kind, fires_at, fired_at)`, indexed on
`(fires_at) WHERE fired_at IS NULL`. The sweep selects rows whose `fires_at` has passed and which
have not fired, in `fires_at` order, with `SKIP LOCKED`. Its cost is proportional to events due, not
to cases open.

Pause and resume recompute and rewrite the affected rows — the only two operations that move a
deadline, and both already write an `SLAEvent`, so the recomputation has a natural place to live.

**Consequence.** Deadline state exists in two places and they must not diverge. A nightly
reconciliation recomputes deadlines for all open cases from `compute_deadline` and asserts equality
with the stored rows, alerting on any mismatch. Catch-up mode (§11.5 step 4) becomes trivial: unfired
rows with a past `fires_at` are exactly the missed notifications, so the runbook's "it will send only
what was actually missed" is a property of the schema rather than a promise about the code.

---

## D7 — Notifications go through a transactional outbox

**Problem.** §4.4 requires that a notification is recorded before it is sent so a retry cannot
double-notify. Recording in Postgres and then calling an email provider is two systems and no
transaction across them. A crash between them loses the send; a crash after it double-sends on retry;
and §11.5 depends on neither happening during exactly the incident where both are most likely.

**Decision.** The sweep writes `NotificationOutbox` rows in the same transaction that marks the
deadline fired. A separate dispatcher claims rows with `SKIP LOCKED`, sends, and records the provider
response and its idempotency key. Delivery is at-least-once at the transport and exactly-once at the
provider, because every send carries a deterministic idempotency key derived from
`(dispute_id, deadline_kind, threshold)`.

**Consequence.** Notifications are delayed by the dispatcher's poll interval. Acceptable: the SLO in
§11.3 is on sweep freshness — the clock advancing — not on notification latency, and an SLA warning
arriving four seconds late has no consequence at all, while a duplicate breach page at 03:00 costs
trust in the alerting.

---

## D8 — 404-not-403 is an exception handler, not a convention

**Problem.** §8.1 and §10 both require that cross-boundary access returns 404, because 403 confirms
the resource exists. DRF's default `PermissionDenied` returns 403, and every serious ORM-level
protection in the product raises before a view can decide what to return. So the correct status
depends on every view remembering, and one that forgets leaks existence rather than data — which is
the kind of finding a penetration test produces and a code review does not.

**Decision.** A project-wide DRF exception handler maps `PermissionDenied`, `TenantScopeError` and
`SessionScopeError` to 404 with an identical body. Authentication failures — no key, malformed key,
expired key — remain 401, because those are statements about the caller, not about a resource.

**Consequence.** Debugging is harder: a genuine authorisation bug looks like a missing object. This
is paid for with structured logging that records the real reason and the discriminating code
internally while the response says nothing. A test asserts that no view in the resolved URLconf can
produce a 403 for an object-scoped route.

---

## D9 — The iframe document is dynamic; only the bundles are immutably cached

**Problem.** §10.1 generates `frame-ancestors` per tenant from their registered origins, and §11.1
serves the widget bundle from a CDN with immutable content-hashed filenames. A per-tenant CSP header
on an immutably cached document is a contradiction, and resolving it the wrong way — caching the
document with one tenant's CSP — hands every tenant another tenant's `frame-ancestors`, converting
the product's headline security control into a shared misconfiguration.

**Decision.** Two artefacts with two policies. `/v1/embed` is a small dynamic HTML document, rendered
per publishable key, `Cache-Control: private, max-age=60`, carrying the tenant's CSP and nothing
else. It references content-hashed JS and CSS bundles that are static, tenant-independent and cached
for a year. Theming is fetched at runtime, not baked into a bundle.

**Consequence.** One dynamic request per widget load, against the 500 ms p95 budget of §11.3. It is
served from the edge, returns under 20 ms, and it also becomes the natural place to enforce origin
checks and record a load attempt from an unregistered origin — which §11.6 needs anyway to make the
"tenant added a domain and forgot" diagnosis the first thing an operator sees rather than something
they deduce.

---

## D10 — The Django admin writes through the service layer, not the ORM

**Problem.** §6.5 mirrors admin actions into the audit trail with a signal handler. Signals fire on
`save()` and `delete()` and miss `bulk_update`, `bulk_create`, `queryset.update()` and raw SQL. The
admin's own bulk actions use exactly those. So the surface described as fully mirrored is mirrored
for single-object edits and silently unaudited for the bulk operations that change the most state —
and §6.5 correctly identifies that an admin panel writing outside the audit trail is a hole in the
evidence.

**Decision.** Admin `ModelAdmin` classes for auditable models override `save_model` and
`delete_model` to call the same service functions the API uses, and the default bulk actions are
removed. Where a bulk action is genuinely needed it is written explicitly as a loop through the
service layer, with its own confirmation and its own audit record per object. Signals stay as a
backstop and a test asserts they are never the only thing that fired.

**Consequence.** The admin is slower and less convenient, deliberately. It handles business
calendars, categories and tenant provisioning — things that change rarely — so the convenience being
traded is worth very little, and what is bought is the ability to say that no write path in the
product bypasses the audit trail, without qualification.

---

## D11 — Auto-close, reopen and the clock states the specification implies but does not size

**Problem.** §3.4's state machine has `awaiting_customer → auto_closed` "within the configured
period" and `resolved → reopened` "within the window". Neither period is a field anywhere in §5, and
both are regulatory quantities: auto-closing a complaint too early is a consumer-protection problem,
and a reopen window that differs from what the customer was told is a dispute about the dispute.

**Decision.** Both are fields on `SLAPolicyVersion`: `auto_close_after_hours` (default 168, one week,
business-hours-aware like every other window) and `reopen_window_hours` (default 336, two weeks,
wall-clock, because a customer's right to challenge an outcome does not observe the firm's office
hours). Both are quoted to the customer in the widget at the moment they become relevant, and both
are versioned with the policy, so what a case was actually subject to is recoverable.

**Consequence.** Two more deadline kinds in D6's table, and auto-close becomes a state transition the
sweep can perform. A system-initiated transition needs an actor, so audit records for auto-close name
the sweep, the policy version and the rule that produced it — an unattributed state change on a
complaint record is not evidence of anything.

---

## D12 — The bundled tenant model is the default; the pluggable one is opt-in and constrained

**Problem.** §6.2's `TENANT_MODEL = "accounts.Organisation"` lets DisputeShield attach to a host
project's existing tenant model. That is the right ergonomics for the installable-Django-app
distribution, and it means RLS policies, the audit chain's partition key and every scoped manager
depend on a model the library does not control — one that might be soft-deletable, might have a
non-integer primary key, might itself be tenant-scoped by the host's own middleware.

**Decision.** DisputeShield ships `disputeshield.Tenant` and uses it by default. `TENANT_MODEL`
points at a host model only if that model satisfies a checked contract: an immutable primary key, no
soft delete, and a stable `disputeshield_tenant_key` property. `disputeshield_doctor` verifies the
contract at install time and `apps.py` refuses to start if it is violated, rather than discovering the
violation when a query returns another organisation's cases.

**Consequence.** Some host projects will not qualify and will run the bundled model with their own
mapping table. That is a worse integration experience than §6.2 implies, and it is the honest one:
the alternative is a configuration option that appears to work and produces a cross-tenant leak in a
host project whose model did something reasonable that we did not anticipate.

---

## Deferred, with the trigger that reopens them

| Question | Deferred because | Reopen when |
|---|---|---|
| Multi-region active-active | Single region meets the §11.3 SLOs, and the audit chain's per-tenant serialisation makes multi-master genuinely hard | Phase 12 residency work (A20), or the first customer with a hard cross-region requirement |
| Read model / CQRS for the queue | The phase 3 gate is 300 ms p95 at 10,000 open disputes, which indexed Postgres serves comfortably | A tenant exceeds 100,000 open disputes, or queue p95 exceeds 250 ms sustained |
| Full-text search over encrypted content | D5's blind index covers agent search; richer search needs a searchable encryption scheme, not a decision to decrypt | A tenant demonstrates that whole-word search materially slows case handling |
| Replacing Celery | Celery beat's single-replica-with-leader-lock requirement (§11.1) is the sharpest operational edge in the product | The §11.5 runbook fires twice in production for the same cause |
