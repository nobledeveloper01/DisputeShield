# Roadmap

Thirteen phases. Each has an **exit gate** that a machine checks — not a judgement call, not "looks
done". A phase is finished when its gate is green in CI and its review has been run. The gates
accumulate: every later phase must keep every earlier gate passing, which is what stops phase 9 from
quietly opening an edit path on a record phase 1 made immutable.

The specification's §12 delivery plan is six weeks and stops at v1.0. This is that plan with the
exit criteria made explicit, plus a phase 0 the specification assumes rather than schedules, plus
six phases that deliver the twenty capabilities in `docs/AMPLIFIERS.md`.

## Three things settled before phase 0, because they change what gets built

**The audit trail is built before anything worth auditing exists.** Phase 1 delivers the append-only
store, the database trigger, the hash chain and the doctor check that verifies the trigger actually
installed — before there is a single dispute in the system. The alternative ordering, where audit is
added once the domain works, produces a system where audit is a thing that gets called rather than a
thing that cannot be avoided. Every write path in every later phase is built on top of a store that
was already immutable, so "did we audit this?" is never a question anybody has to remember to ask.

**The SLA engine precedes the API.** `compute_deadline` is a pure function (§4.4) and its test
matrix — DST in both directions, holidays, pauses spanning holidays, windows shorter than one
business day — is the highest-value suite in the project. It is written in phase 2, against no HTTP
surface at all. A deadline bug found in phase 2 costs an afternoon; the same bug found after the
dashboard renders it costs a compliance incident at a customer.

**Phases 0–6 are a shippable, complete product.** They deliver everything §13 claims: the sandboxed
iframe, the server-minted customer-scoped session token, the pausable-clock-with-mandatory-reason,
the dead-man's switch, and `disputeshield_doctor`. Phases 7–12 make it more valuable, not more
finished.

Nothing in the amplifier phases is a prerequisite for anything in the v1.0 phases. That is
deliberate — when time runs short, and it will, the cut line is obvious and lands somewhere
defensible.

---

## Phase 0 — Foundations

Nothing about disputes. Only the machinery every later gate depends on.

**Deliverables**
- `pyproject.toml`, Python 3.12, ruff, pytest, coverage, `pip-audit`, `bandit`, `semgrep`, `gitleaks`
- `Makefile` whose targets are the ones CI runs, so local and CI cannot disagree
- `.github/workflows/ci.yml` with the five jobs of §11.8 wired but mostly empty
- `compose.yaml`: Postgres 16, Redis broker, Redis cache — **two Redis instances from the first
  commit** (§11.1), because a single instance is the kind of shortcut that only reveals itself when
  a cache flush destroys the task queue
- Pre-commit hooks: format, lint, `gitleaks`
- `docs/adr/0001-sandboxed-iframe-widget.md` — written now, before the code it justifies, because
  §12.2 says it is the first thing a security reviewer reads
- `disputeshield_doctor` with the checks that exist today. It grows at least one check per phase:
  every new failure mode adds its own preflight, so a self-hosting customer finds out at install
  time rather than during an incident
- `scripts/hello-world.sh` — empty database to a filed dispute with a computed SLA deadline,
  asserted in CI with a step count. §6.1 claims thirty minutes to integrate; this keeps the claim
  true when a later phase is tempted to add a step

**Exit gate**
- `make ci` green from a clean checkout
- Coverage gate configured at 85% and enforced (trivially passing on an empty package is fine — the
  point is that the gate exists before there is pressure to lower it)
- `python manage.py makemigrations --check --dry-run` is a blocking CI step
- The `dangerouslySetInnerHTML` grep of §11.8 is in CI and demonstrably fails a branch that adds it

**Review:** `/devex-review` — the one moment the setup experience can be judged before anyone is
invested in it.

---

## Phase 1 — Tenancy and the immutable audit trail

The security core. Built first, tested adversarially, never revisited under time pressure.

**Deliverables**
- `Tenant`, `Agent`, `APIKey` — keys Argon2id-hashed, prefix in plaintext, `ds_{env}_{random32}`,
  per-environment scoping (§8.2)
- `disputeshield/tenancy/` — `TenantScopedManager` whose `get_queryset()` **raises** without a
  tenant context. No default manager returns unscoped rows (§8.1, layer 2)
- Postgres RLS policies keyed on a session variable set from the authenticated tenant (§8.1, layer 3)
- `disputeshield/audit/` — `AuditRecord`, `default_permissions = ()`, per-tenant hash chain
- The migration that revokes `UPDATE`/`DELETE` from the application role and installs the
  `BEFORE UPDATE OR DELETE` trigger
- `disputeshield_doctor --strict` verifying grants and trigger installation, refusing to start if
  either is absent (§6.2)
- `tests/test_tenant_isolation.py`, `tests/test_immutability.py`

**Exit gate**
- Two tenants; every read endpoint that exists returns **404** — not 403 — when called across the
  boundary. Blocking CI gate (§8.1). *Phase 1 has no endpoints, so the model-layer half is asserted
  here and the HTTP half lands with the routes in phase 3 — a passing assertion over an empty
  URLconf is a green gate that checks nothing.*
- `AuditRecord.objects.filter(...).delete()` raises. A raw `UPDATE` executed as the application role
  raises **at the database level**, asserted by a test that bypasses the ORM entirely — an
  ORM-only assertion tests Django, not the database
- Tampering with any record in a chain of 1,000 invalidates every subsequent record, asserted
- `disputeshield_doctor --strict` **fails** on a database where the trigger migration was reverted.
  A doctor that only passes has never been shown to work
- A model without a tenant-scoped manager fails a metaprogramming test that walks the app registry
- Row level security is **FORCEd**, not merely enabled, asserted against `pg_class`. Plain `ENABLE`
  exempts the table owner, and in every self-hosted compose install the application role is the
  owner — the layer would look installed and do nothing

**Review:** `/cso` on the tenancy and audit modules. The security core gets a dedicated pass rather
than being folded into a general review.

---

## Phase 2 — The SLA engine

Pure functions and a scheduler. No HTTP.

**Deliverables**
- `SLAPolicy`, `BusinessCalendar`, `Holiday`, `SLAEvent`
- `compute_deadline(start, hours, calendar, paused_intervals)` — pure, side-effect free, UTC
  arithmetic with calendar boundaries resolved in the calendar's own timezone (§4.4)
- `tests/test_sla_deadlines.py` — the matrix: weekends, public holidays, DST transitions in both
  directions, multiple pause intervals, a pause spanning a holiday, windows shorter than one
  business day, a window that starts outside business hours
- Celery beat sweep, once a minute, idempotent: the notification is recorded **before** it is sent
- `disputeshield_sla_sweep_heartbeat` and the dead-man's-switch alert
- Catch-up mode for replaying a missed window
- Pause and resume, each requiring a reason, each writing an `SLAEvent` carrying
  `clock_remaining_seconds`

**Exit gate**
- Property-based test: for any start time, any window and any calendar, the computed deadline
  contains exactly the requested quantity of business time. Hypothesis, 10,000 examples, no falsifying
  case
- Deadline computation is deterministic across processes and across the machine's local timezone —
  the suite runs a second time under `TZ=Pacific/Kiritimati` and must produce identical results
- Chaos test: kill beat mid-sweep, restart, assert catch-up sends **exactly** the missed
  notifications and no duplicates (§11.9)
- The heartbeat alert fires within its 3-minute budget in a test that actually stops the sweep. §11.5
  makes this the most safety-critical component in the product; a monitor nobody has watched fail is
  a monitor nobody should trust
- No pause path exists that does not require a reason — asserted by introspecting the transition
  table, not by calling the endpoints

**Review:** `/review` on the diff, then `/investigate` on any falsifying Hypothesis case before it is
patched, because the interesting output of this phase is the bugs the property test found.

---

## Phase 3 — Management API and agent workspace

**Deliverables**
- `Dispute`, `DisputeMessage`, the state machine of §3.4, `on_delete=PROTECT` throughout
- Management API of §7.3, cursor-paginated, idempotency keys on every write
- Separate widget and management serializers (§10)
- Assignment: round-robin and category-based; auto-escalation at configurable thresholds
- Agent workspace: queue sorted by time-remaining ascending, breached cases pinned, filters by
  status, category, assignee, amount band
- Case view: conversation, internal notes, SLA clock, full action history
- `tests/test_serializer_leakage.py`

**Exit gate**
- The leakage test **introspects the widget serializer's full field graph** and asserts no path
  reaches internal content. Sampling outputs is not sufficient: a future field could open a path no
  sample exercises (§11.9)
- Every state transition in §3.4 writes an audit record containing actor, reason and the SLA clock
  state at that moment. A transition that writes no audit record fails a test that enumerates the
  transition table
- No `PATCH` or `DELETE` route resolves to any auditable record. Asserted by walking the resolved
  URLconf, not by convention
- Isolation suite extended to customer level: customer A's session token returns 404 for customer B's
  dispute. *The query-layer half is asserted in phase 3; the session-token half lands in phase 4 with
  the tokens themselves.*
- Queue p95 under 300 ms with 10,000 open disputes

**Review:** `/review`, then `/plan-design-review` on the queue and case view before they are built out.

> **Sequencing note.** Phase 3 delivers the workspace's data layer — the urgency
> ordering, the breach pinning, the filters and the case view's payload, all under
> the p95 gate. The React workspace itself moves to phase 4, which is where the
> frontend toolchain, the Playwright harness and the accessibility gates land. The
> alternative is standing that toolchain up twice, and the queue's behaviour is
> asserted here either way.

---

## Phase 4 — The widget

The phase with the highest security stakes and the most visible product surface.

**Deliverables**
- `loader/` — ~4 KB, no dependencies, creates the sandboxed cross-origin iframe and nothing else
- `widget/` — React app inside the iframe: filing flow, transaction picker, status timeline
- `POST /v1/sessions` (§7.1), 30-minute TTL, scoped to exactly one `customer_ref`
- Widget API (§7.2), session-token scoped
- `postMessage` protocol with a fixed schema and strict origin checking on **both** sides, never
  `'*'` as a target origin
- Per-tenant CSP with generated `frame-ancestors` (§10.1)
- Theming: colours, radius, font, logo, position, locale

**Exit gate**
- Playwright, inside a hostile host page: the widget cannot reach host globals; the host cannot read
  the session token, widget internals or the iframe's DOM. Both directions asserted
- A `postMessage` from an unregistered origin is ignored, and the attempt is recorded
- The publishable key alone cannot read, create or enumerate a single dispute — asserted against
  every widget endpoint, not a sample
- Widget p95 load under 500 ms on a throttled connection, measured in CI, failing the build on
  regression (SLO §11.3)
- axe-core clean, plus a **keyboard-only** walkthrough of the entire filing flow, in CI. §9 makes
  accessibility a regulatory obligation, not a nice-to-have
- Removing the CSP or widening `frame-ancestors` fails the build

**Review:** `/cso` on the widget boundary, `/design-review` on the filing flow, `/qa` on the
integration in a real host page.

---

## Phase 5 — Attachments, templates, context

**Deliverables**
- `DisputeAttachment` — magic-byte type allowlist, 10 MB cap, SHA-256, private object storage
- AV scanning; nothing retrievable until `scan_status == 'clean'`
- Separate serving origin, `Content-Disposition: attachment`, never rendered inline
- Response templates with variable substitution
- `POST /v1/disputes/{id}/context` (§7.3)
- Notifications: email and Slack, with the recorded-before-sent discipline of phase 2

**Exit gate**
- A polyglot file (valid GIF header, embedded HTML/JS) is rejected by magic-byte inspection.
  A zip bomb is rejected. A file renamed `.pdf` that is not a PDF is rejected
- An attachment with `scan_status != 'clean'` returns 404 to every caller including its uploader,
  asserted per role
- Attachment URLs are not guessable and expire
- Template substitution cannot inject content into an internal note or reach the widget serializer

**Review:** `/cso` on the upload path. It is the second most likely place in the product to be
attacked, after the widget.

---

## Phase 6 — Analytics, export, packaging — **v1.0**

**Deliverables**
- Breach analysis: by category, agent and cause; pause-duration analysis (§6.5)
- `GET /v1/reports/regulatory` — CSV and PDF, per-case history, integrity attestation
- `GET /v1/audit/verify`, nightly chain verification, signed checkpoints
- Object-storage replication of audit records with a write-once lock (§8.3)
- Packaging: PyPI `disputeshield` and `disputeshield-client`; npm `@disputeshield/react` and
  `@disputeshield/node` with provenance; CDN loader with content-hashed filenames
- Helm chart, docker-compose quickstart, Terraform module
- `docs/openapi.yaml` complete

**Exit gate**
- `pip install disputeshield` into a **bare** Django project, run `disputeshield_init` and
  `disputeshield_doctor`, file a dispute — asserted end to end in CI against a real PyPI-shaped
  artefact, not the working tree. §6.2 is a distribution promise and it is only true if it is tested
  as one
- The regulatory export for a known period is byte-reproducible, and its integrity attestation
  verifies against an independently recomputed chain
- Restore drill: restore from backup into an isolated environment, reconstruct a known case's full
  history, verify its hash chain (§11.7)
- Load: 500 concurrent agents, 10,000 open disputes, sweep completes within 60 s (§11.9)
- SBOM published, image signed, `trivy` clean at HIGH and CRITICAL

**Review:** `/cso` full audit, `/devex-review` on the install path, `/document-release`.

> **v1.0 ships here.** Everything below is `docs/AMPLIFIERS.md`.
>
> **Status: phases 0–6 complete.** Every exit gate above runs in CI. Two are worth
> naming because they changed the code rather than confirming it: the packaging
> gate found that `SET LOCAL` outside a transaction is silently discarded — the
> third appearance of that failure shape — and the load gate found the sweep
> missing §11.9's budget by 26%, which forced the batched audit append ADR-0003
> had anticipated.

---

## Phase 7 — Every complaint lands in the clock — **v1.1**

**Amplifiers:** A1 omnichannel intake · A2 deflection · A3 mass-incident mode

**Deliverables**
- Per-tenant ingest addresses; email, WhatsApp Business, USSD, call-log and web-form intake
- Thread matching with an explicit `unmatched_review` state
- Deflection: incident declaration, matching, "notify me" subscription, unconditional "file anyway"
- `MassEvent`: grouping, single investigation, fan-out resolution as individual appends

**Exit gate**
- A reply from an address that is not the case's verified contact is quarantined, never appended.
  Asserted for every channel, including the display-name-spoofing and reply-to-rewriting cases
- Every channel produces a case with an identical clock, identical audit shape and identical
  isolation guarantees. One parameterised suite runs the whole v1.0 case-lifecycle test matrix once
  per channel — a channel that skips a check is a channel that is not really in the clock
- Deflection cannot be configured to remove the "file anyway" control. Asserted against the
  configuration schema itself, so the guarantee holds for configurations nobody has written yet
- `deflections_total` is exported and rendered next to case volume in the dashboard
- Fan-out of a 5,000-case mass resolution writes 5,000 individual audit records and executes zero
  bulk `UPDATE`s. Asserted by counting statements, not by reading the code
- Removing a case from a mass event preserves everything that happened while it was a member

**Review:** `/cso` on intake — this phase adds four new untrusted input surfaces. `/plan-ceo-review`
on deflection before it is built, because it is the one feature here that can be wrong in a way that
looks like success.

> **Status: complete.** Every gate above runs in CI. The per-channel suite found a
> bug the earlier phases had not: `file_dispute` started each clock against a
> placeholder subject id, so `sla.started` was attributed to a subject that never
> existed and every case's history was missing the event that began its clock.

---

## Phase 8 — Evidence that survives a lawyer — **v1.2**

**Amplifiers:** A7 legal hold · A8 chain anchoring · A6 external escalation · A17 regulatory returns

**Deliverables**
- `LegalHold` over a case, customer, category or date range; suspends retention and erasure;
  two-person release
- RFC 3161 timestamping of nightly checkpoints; publication to an append-only transparency log
- External escalation track: reference, external deadlines, correspondence, determination
- Versioned, data-driven regulatory return templates with maker-checker sign-off

**Exit gate**
- A data-subject erasure request against held material is **refused with a recorded reason**, and the
  refusal is itself auditable. Both the refusal and the reason are asserted
- Retention sweeps skip held material; releasing a hold re-enters it into the normal schedule
- `GET /v1/audit/verify` reports chain status and anchor status as two independent facts
- With the timestamp authority unreachable, writes continue, checkpoints queue, the unanchored count
  is exported as a metric, and recovery anchors the backlog in order
- A case with an open external track cannot reach `closed`. Enforced in the state machine and
  asserted against every transition, not just the obvious one
- A return filed last year regenerates byte-identically under this year's template revision

**Review:** `/cso`, plus a legal read of the hold and erasure interaction. This is the phase where
being wrong is expensive in a way code review does not catch.

> **Status: complete.** The gate that taught something: a foreign key *into* an
> append-only table cannot be enforced, because Postgres locks the referenced row
> and a row lock needs the UPDATE privilege the append-only design revokes. The
> anchor table holds a plain reference instead.

---

## Phase 9 — The money side — **v1.3**

**Amplifiers:** A5 representment packs · A4 provider connectors · A16 financial exposure

**Deliverables**
- Reason-code mapping, per-code evidence checklists, scheme deadline as a second visible clock
- Representment pack export in acquirer-accepted format
- Read-only provider connectors: Paystack, Flutterwave, NIBSS, Stripe, generic REST
- Exposure view: value under dispute by category, age and provider; recorded refund liability;
  ledger reconciliation

**Exit gate**
- The scheme clock and the regulatory clock breach independently and alert independently, asserted
  with a case where one is comfortable and the other is not
- The connector interface exposes **no write method**. Asserted by introspecting the abstract base
  class — a connector cannot accidentally gain one, because there is nothing to override
- Connector credentials are envelope-encrypted per tenant; every outbound call is audited with the
  exact request made; a connector failure degrades the case to "context unavailable" and never blocks
  filing
- **No code path from the exposure view to money movement.** A call-graph test asserts nothing under
  `disputeshield/finance/` reaches a connector, a payment method or an outbound write. §3.3 puts this
  under permanent **Won't**, so it gets a permanent gate
- Exposure figures reconcile against a synthetic ledger, and the unreconciled delta is reported
  rather than hidden

**Review:** `/cso` on connector credential handling. `/plan-eng-review` on the two-clock model before
it is built.

> **Status: complete.** The two-clock model is structural rather than
> conventional: scheme deadlines are `pausable=False` rows on the same clock, so
> one sweep fires both and a pause moves only ours. The money-movement gate walks
> the AST rather than reading the code, and its first finding was a naming one —
> a function called `_settle` in the intake router.

---

## Phase 10 — Intelligence, strictly advisory — **v1.4**

**Amplifiers:** A11 triage · A12 copilot · A10 root-cause clustering · A13 repeat-claimant signals

Every deliverable in this phase proposes. None disposes. The exit gate exists mostly to keep it that
way, because this is the phase where the product could quietly stop being a system of record.

**Deliverables**
- Category, subcategory, priority and routing suggestions with model identifier and version recorded
- Copilot drafts grounded strictly in case content, tenant templates and resolved history
- Root-cause clustering with inspectable membership and evidence
- Repeat-claimant and first-party fraud signals, presented as context with their evidence

**Exit gate**
- Every suggestion, acceptance and override is an audit record naming the model and its version
- **No model output writes to `Dispute`.** Suggestions live on a separate `Suggestion` model with no
  path to case fields. Asserted by introspection of the write path
- A draft containing a date, an amount or a commitment absent from its retrieved sources is
  **blocked from insertion**, not flagged. Adversarial suite included
- No autonomous send exists on any channel. Asserted against the send path itself, so no
  configuration can enable one
- A fraud signal is structurally incapable of reaching an SLA policy, a priority, a channel gate or
  an outcome. Call-graph asserted, same discipline as phase 9's money gate
- Clustering executes zero writes to any case
- Suggestion accuracy is exported per tenant, so a model degrading is visible without anyone
  investigating

**Review:** `/cso` and `/plan-ceo-review`. The commercial pull toward "just let it auto-resolve the
easy ones" arrives in this phase, and the answer needs to have been decided before the pull does.

> **Status: complete.** Every gate is structural — an AST walk or an introspection
> — rather than a behavioural sample, because a behavioural test proves the paths
> we thought of are closed and this phase needs the ones we did not.

---

## Phase 11 — Operating the operation — **v1.5**

**Amplifiers:** A9 SLA simulator · A15 QA sampling · A14 outbound webhooks

**Deliverables**
- Policy simulator replaying a proposed change over 90 days of real cases, using the **historical**
  calendar and the **historical** pause intervals
- QA sampling: defensibly random selection, forced review for reopened, escalated, high-value and
  vulnerable-customer cases; rubric scoring; agent and team scorecards
- Outbound webhooks: signed with the §8.2 HMAC scheme, ordered per dispute, at-least-once with
  idempotency keys, parked rather than dropped

**Exit gate**
- The simulator produces zero writes, runs against the read replica, and its output is stored with
  the policy version it evaluated
- Simulating an unchanged policy over a historical period reproduces the breach count that actually
  occurred. Anything else means the replay is not using history, and a confident wrong number is
  worse than no number
- Sample selection is uniformly random over the eligible set, asserted statistically; forced-review
  criteria cannot be disabled
- **Webhook payloads pass the phase 3 serializer-leakage test unchanged.** The same test, not a
  parallel one — a second implementation of the same guarantee is a second thing to get wrong
- A customer endpoint down for 24 hours parks events and replays them in order on recovery, zero
  loss, zero duplicates at the consumer given idempotency keys

**Review:** `/review`, `/devex-review` on the webhook integration experience.

> **Status: complete.** The gate that earned its place: the simulator's
> self-check found that the replica is a separate connection with no RLS context,
> so anything reading from it returned zero rows silently. `analytics.py` had been
> claiming replica routing since phase 6 without doing it.

---

## Phase 12 — Enterprise and adoption — **v2.0**

**Amplifiers:** A20 residency/BYOK/crypto-shredding · A18 migration tooling · A19 sandbox and simulator

**Deliverables**
- Per-tenant region pinning, no cross-region replication of case content
- Customer-managed KMS keys; per-subject data keys; erasure by key destruction
- Importers: Zendesk, Freshdesk, Intercom, CSV, IMAP archive
- `test` environment per tenant; `disputeshield simulate`; one-command seeded demo tenant

**Exit gate**
- Case content for a pinned tenant never leaves its region. Asserted by a network-level test in a
  two-region staging environment, not by reading configuration
- After crypto-shredding, the hash chain still verifies and the content is unrecoverable. Both halves
  asserted — the first is what makes the shred acceptable, the second is what makes it a shred
- Shredding requires two-person authorisation and writes an audit record
- BYOK revocation renders exactly that tenant's data unreadable and no other tenant's
- Imported records are marked, excluded from live SLA computation, and carry no
  DisputeShield-witnessed integrity claim. A regulatory export visibly distinguishes imported history
  from native history
- **Clock offset is rejected at the model layer for `live` tenants.** Blocking CI gate, asserted
  against the model rather than the view, because a view-layer guard is one refactor away from being
  bypassed
- `disputeshield simulate` produces a demo tenant containing a breach, a pause, a reopening and a
  mass incident, in one command, in under 60 seconds

**Review:** `/cso` full audit, `/devex-review` on the import path, `/document-release`.

> **Status: complete.** The design that makes crypto-shredding possible is that
> the chain hashes *what is stored* — ciphertext and metadata, never plaintext —
> so destroying a key changes no row and nothing the chain covers moves. That
> property was decided in phase 1 and cashed in here.

---

## Phase-to-amplifier index

| # | Amplifier | Phase | Ships in |
|---|---|---|---|
| A1 | Omnichannel intake gateway | 7 | v1.1 |
| A2 | Deflection and known-issue layer | 7 | v1.1 |
| A3 | Mass-incident mode | 7 | v1.1 |
| A4 | Provider context connectors | 9 | v1.3 |
| A5 | Chargeback / representment packs | 9 | v1.3 |
| A6 | External escalation tracking | 8 | v1.2 |
| A7 | Legal hold and evidence vault | 8 | v1.2 |
| A8 | Audit chain anchoring | 8 | v1.2 |
| A9 | SLA policy simulator | 11 | v1.5 |
| A10 | Root-cause clustering | 10 | v1.4 |
| A11 | Triage and routing intelligence | 10 | v1.4 |
| A12 | Agent copilot | 10 | v1.4 |
| A13 | Repeat-claimant signals | 10 | v1.4 |
| A14 | Outbound webhooks and event stream | 11 | v1.5 |
| A15 | QA sampling and scorecards | 11 | v1.5 |
| A16 | Financial exposure and provisioning | 9 | v1.3 |
| A17 | Regulatory returns automation | 8 | v1.2 |
| A18 | Migration and import tooling | 12 | v2.0 |
| A19 | Sandbox, simulator, demo tenant | 12 | v2.0 |
| A20 | Residency, BYOK, crypto-shredding | 12 | v2.0 |

**All twelve phases are complete.** Every exit gate above runs in CI.

## The gates that never stop applying

Six assertions run in every phase from the moment they are introduced. They are listed separately
because they are the product's actual promises, and a phase that breaks one has broken the product
regardless of what it delivered.

| Gate | From | Asserts |
|---|---|---|
| Tenant and customer isolation | 1 | Cross-boundary reads return 404, never 403 |
| Database-level immutability | 1 | `UPDATE`/`DELETE` on audit raises in Postgres, not just the ORM |
| No edit or delete path | 3 | No route resolves to a mutation of an auditable record |
| Serializer leakage | 3 | No field path from customer-facing output to internal content |
| Widget isolation | 4 | Neither side of the iframe boundary can reach the other |
| No money movement | 9 | No call path from any module to a payment write |
