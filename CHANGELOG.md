# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The PyPI package, the npm packages and the widget bundle are versioned together
and released from one tag.

## [Unreleased]

### Added — phase 4, the widget and its boundary

- `loader/` — **1,035 bytes gzipped**, a quarter of ADR-0001's 4 KB budget. It
  creates the sandboxed cross-origin iframe and does nothing else. No fetch, no
  cookie access, no DOM queries into the host page — asserted by a test that
  greps its own source, because the budget protects reviewability and the grep
  protects the claim.
- The `postMessage` protocol, symmetric and validated on both sides: fixed
  envelope, protocol version, an allowlist per direction, origin **and**
  `event.source` checked, and never `'*'` as a target origin.
- Session tokens (§4.3, ADR-0002): opaque, Redis-backed, hashed at rest, scoped
  to exactly one customer, revocable one session at a time, per customer, or per
  minting key. The last is the response to a leaked secret key and is available
  immediately rather than after a rotation completes.
- **The token is handed to the widget over `postMessage`, never in the iframe
  URL** (§10). It is sent only after the widget announces it is listening, only
  to the widget's own origin, and only once.
- `POST /v1/sessions`, the widget API of §7.2, and the transaction picker fed
  from the list the fintech supplied at mint time — so a customer can only
  dispute their own transactions, enforced rather than assumed.
- Publishable keys as a distinct kind with a distinct `pk_` prefix and a distinct
  principal class, so one cannot satisfy a permission written for the other by
  accident.
- `AllowedOrigin` with validation that refuses a path, a wildcard or `null` — a
  path is the dangerous one, because `frame-ancestors` ignores it and the tenant
  believes they restricted a page when they authorised a host.
- `GET /v1/embed` (D9): dynamic, per-tenant CSP, privately cached for a minute,
  referencing bundles that are static and cached for a year. A load from an
  unregistered origin is refused **and recorded**, because §11.6 says that is the
  most common widget support ticket by a wide margin.
- The React widget: one decision per screen, the expected resolution date shown
  before submission, and focus moved to each step's heading so a screen reader
  announces it.
- `/healthz` and `/readyz`. Readiness includes the audit immutability trigger —
  a deployment that can accept writes but cannot make them immutable should not
  be taking traffic.

### Fixed — during phase 4

- **`role="radiogroup"` on a `<ul>` stripped its list semantics**, orphaning the
  `<li>` children. Found by axe-core, not by review.
- **A customer's own message could not be audited.** `add_message` passed
  `customer` as an actor type and the audit trail accepted only
  system/user/api_key. Recording a customer's words as `api_key` would attribute
  them to the fintech's integration, so `customer` is now a first-class actor
  identified by the pseudonymous hash the case already carries.
- **The embed document answered an unknown key with a JSON 401.** That body is
  what would render inside a customer-facing page on a fintech's site. It now
  fails closed *and quietly* — empty 403, deny-everything CSP — via an
  authenticator used on that one surface and nowhere else.
- **The widget's inbound and outbound message allowlists were the same set**, so
  a type added for one direction became valid in the other.
- **The direct-write grep gate flagged reads.** `Dispute.objects.filter` in a
  view is a scoped queryset, not a bypassed audit trail; the matcher now targets
  write methods only.

### Tests — 213 Python, 19 loader, 13 browser

- `tests/isolation.spec.js` — Playwright, two real origins, a deliberately
  hostile host page holding a fake account number in a form field, on `window`,
  in a cookie and in `localStorage`. Asserted in both directions: the host cannot
  read the iframe's document or globals; the widget cannot reach
  `window.parent.HOST_SECRET`, the host's DOM, its cookies or its storage.
- `tests/keyboard.spec.js` — the **complete** filing flow driven by Tab, Enter,
  Space and typing only, ending at a filed case with a reference. §9 makes this a
  regulatory obligation, and the iframe boundary is what makes it hard.
- `tests/a11y.spec.js` — axe-core at **every** step, not just the first. The
  screens a customer reaches after a decision are the ones nobody looks at.
- `tests/test_widget_api.py` — the publishable key is asserted to reach no data
  route, parameterised over every route rather than sampling one; a session token
  cannot cross customers or tenants; a transaction reference outside the session
  is refused.
- `tests/test_widget_embed.py` — per-tenant `frame-ancestors`, no
  `unsafe-inline`, never publicly cacheable, and the document contains no inline
  script its own CSP would block.

### Added — phase 3, the management API and the case lifecycle

- `Dispute` and `DisputeMessage`. Messages are immutable with no edit path — a
  correction is a new message, for the same reason the audit trail appends: a
  conversation that can be edited afterwards is not evidence of what was said.
- §3.4's state machine as a **table** (`disputeshield/disputes/states.py`), so the
  tests enumerate it. A transition added in a later phase is automatically covered
  by the assertion that every transition records actor, reason and the SLA clock
  state at that instant, rather than only if somebody wrote a test beside it.
- `disputeshield.disputes.service` — the only supported way to write a case. The
  API calls it, the admin will call it (D10), commands call it. That is what lets
  the audit trail be complete without qualification.
- Management API (§7.3): cursor-paginated queue sorted by urgency with breached
  cases pinned, filters by status/category/assignee/amount/risk, and actions for
  transition, pause, resume, resolve, assign, messages and SLA.
- Two unrelated serializer families. Widget and management share **no base class**
  — inheritance is how a field added for agents silently appears in a customer's
  response.
- API key authentication (Argon2id), the §6.5 role model, and an acting-agent
  header so the audit trail names the person rather than the key.
- D8's 404-not-403 exception handler, project-wide rather than per-view.
- Idempotency on every write, stored rather than cached, with a request
  fingerprint so reusing a key with a different body is a 409 instead of silently
  returning the first response and hiding a client bug.

### Fixed — during phase 3

- **The role permission classes did not require authentication.** Setting
  `permission_classes` on a view *replaces* `IsAuthenticated` rather than adding
  to it, so an anonymous request passed the permission check and reached the
  queryset. Nothing leaked — the scoped manager raised — but the last layer was
  doing the first layer's job, and the caller got a 500 instead of a 401.
- **The tenant contextvar outlived its request.** Authentication set it and
  nothing reset it; worker threads are reused, so the next request began with the
  previous request's tenant in scope, and an anonymous request never overwrote it.
  `TenantContextMiddleware` now owns the lifetime and resets in a `finally`.
  Found by test-ordering pollution — which is exactly how it would have been found
  in production.
- **RLS on the API key table made authentication impossible.** The lookup happens
  before a tenant context exists, so the blanket policy from migration 0003
  returned zero rows and every request answered 401. The model docstring already
  said the lookup could not be tenant-scoped; the migration contradicted it. The
  policy is now split by command: SELECT unscoped (the row holds a prefix and an
  Argon2id hash), writes still tenant-scoped.
- **The queue serializer computed business-time remaining per row.** That walk
  needs the calendar, the pause intervals and a deadline row for every case, so a
  page of fifty cost fifty calendar walks plus an N+1. It passed in isolation and
  failed in a full run — the worst way for a performance defect to behave. The
  list now reports the denormalised deadline and breach flags, which is what the
  urgency sort and the breach pinning actually read; business-time remaining moved
  to the detail view, where it is one case rather than fifty. The whole suite went
  from 169s to 37s as a side effect.
- **Idempotency records could not store their own response.** The SLA block began
  returning datetimes, which a `JSONField` cannot hold — so the write failed on
  the *original* request, for a feature that exists only to make retries safe.
  Responses are now stored as rendered JSON, which is also the only way a replay
  can return what the client actually received.
- **The queue performance gate was measuring one row.** The load fixture reused
  the seed case's clock, which is a OneToOne, so every bulk insert violated the
  constraint and `ignore_conflicts=True` swallowed all 9,999 of them. The fixture
  now builds a clock per case and asserts the row count before measuring — a
  performance gate that silently measures nothing is worse than no gate, because
  it reports success.

### Tests — 172 passing, 91% coverage

- `test_serializer_leakage.py` — walks the widget serializers' **full field
  graph**, checks `source` aliases, asserts the widget and management serializers
  share no base class, and enumerates the module so a new widget serializer added
  without coverage fails the build.
- `test_no_mutation_routes.py` — walks the **resolved URLconf** asserting no route
  binds PUT/PATCH/DELETE and no `ModelViewSet` exists, then greps the API package
  for direct ORM writes to auditable models.
- `test_dispute_transitions.py` — drives every entry in the transition table.
- `test_management_api.py` — authentication (including that an unknown prefix and
  a wrong secret are byte-identical responses), the queue's urgency ordering and
  breach pinning, cursor pagination, idempotent replay, 409 on key reuse, and
  role separation.
- `test_queue_performance.py` — p95 under 300 ms at 10,000 open cases, plus a
  query-plan assertion so the budget is not met by a sequential scan that stops
  being fast at the next order of magnitude.
- Cross-tenant and cross-customer isolation extended to the HTTP layer: 404, never
  403, on both reads and writes.

### Added — phase 2, the SLA engine

- `compute_deadline` and `business_time_between` (`disputeshield/sla/deadlines.py`)
  — pure, Django-free, and written as each other's check rather than sharing an
  implementation, so their agreement is evidence instead of tautology.
- `BusinessCalendar` as a plain value object: weekday windows, local-date
  holidays, continuous (24/7) mode, and a search horizon that raises
  `ImpossibleCalendar` rather than looping forever on a calendar that is never
  open — a hung sweep is a stopped compliance clock for every tenant on the worker.
- `elapsed_fraction` — warning thresholds are percentages of *business* time. A
  case filed Friday afternoon is not 60% consumed by Sunday.
- Models: `BusinessCalendar`, `BusinessHoursWindow`, `Holiday`, `SLAPolicy`,
  `SLAPolicyVersion` (immutable, ADR-0004), `SLAClock`, `SLAEvent`, `SLADeadline`,
  `NotificationOutbox`, `SweepHeartbeat`. RLS enabled and **FORCEd** on all of them.
- Clock lifecycle: `start`, `pause`, `resume`, `stop`. Pause and resume require a
  reason at the service layer **and** at the database layer — a `CheckConstraint`
  makes a reasonless pause event unrepresentable, because a reason enforced only
  in a service is one refactor from optional.
- Every clock event records `clock_remaining_seconds` at the moment it happened.
  That field is what makes a breach explainable six months later.
- The watermark-driven sweep (ADR-0007): claims due deadlines with `SKIP LOCKED`,
  writes the outbox row in the same transaction that marks the deadline fired,
  under an idempotency key derived from *what* the notification is about rather
  than *when* it was generated. That is what makes §11.5's catch-up step safe to
  run during an incident instead of causing a second one.
- `SweepHeartbeat`, written even on a quiet sweep — "nothing was due" and "the
  scheduler is dead" look identical from outside, and only one is an incident.
- `disputeshield_sweep` management command with `--catch-up`, `--to` and
  `--dry-run`, so §11.5 step 4 is an executed procedure rather than prose.
- Nightly reconciliation of materialised deadlines against the pure function.
  Divergence is **reported, never repaired** — a mismatch may be a bug or an owed
  backfill, and rewriting the row destroys the evidence needed to tell which.
- `disputeshield_doctor` gained a heartbeat check. Non-fatal by design: refusing
  to serve because the scheduler stalled would turn a silent compliance outage
  into a loud availability one, and take down the dashboard showing which cases
  are affected.

### Fixed — during phase 2

- **An open pause interval was measured to `now()` rather than to the instant
  being evaluated.** A clock evaluated at the moment it was paused counted its own
  pause as already elapsed and reported zero time remaining — wrong, and exactly
  backwards. `paused_intervals_of` now takes the instant under evaluation.
- **`disputeshield.sla.sweep` was both a module and an exported function**, so the
  package export shadowed the module and `from disputeshield.sla import sweep`
  yielded whichever was imported last. The module is now `sweeper`.

### Tests — 121 passing, 93% coverage

- `test_sla_deadlines.py` — the matrix: weekends, holidays as *local* dates, DST
  in both directions, windows starting before opening and after closing, windows
  shorter than a business day, multiple pauses, a pause spanning a holiday and one
  spanning a weekend, overlapping pauses merged rather than double-subtracted.
  Runs a second time under `TZ=Pacific/Kiritimati`.
- Two Hypothesis properties, run at **10,000 examples** in CI with no falsifying
  case: the round trip (a computed deadline contains exactly the business time
  requested) and monotonicity (a longer window never yields an earlier deadline).
- `test_sla_clock.py` — pause discipline asserted four ways, including by
  introspecting the service signature so a future overload with a defaulted
  `reason` fails the build.
- `test_sla_sweep.py` — firing, idempotency, catch-up, late-detection recording,
  the heartbeat's 3-minute alert budget, reconciliation, and the runbook's shell
  command run twice to prove it pages nobody twice.

### Added — phase 1, tenancy and the immutable audit trail

- `Tenant`, `Agent`, `APIKey` and `AuditRecord`, with `on_delete=PROTECT`
  throughout (ADR-0006) and random prefixed identifiers rather than sequential
  keys, so §10's answer to ID enumeration holds before the 404 is reached.
- `TenantScopedManager`: the default manager on every tenant-scoped model
  **raises** without a tenant context. `all_tenants()` is the only unscoped path
  and is named so that using it is a decision rather than an inherited default.
- Postgres row level security on every tenant-scoped table, **FORCEd** so that it
  applies to the table owner too — plain `ENABLE` exempts the owner, and in every
  self-hosted compose install the application role *is* the owner, which would
  leave the third isolation layer looking installed and doing nothing.
- Audit immutability enforced by the database: `BEFORE UPDATE OR DELETE` trigger,
  a separate statement-level `BEFORE TRUNCATE` trigger, and `UPDATE`/`DELETE`/
  `TRUNCATE` revoked from the application role.
- Hash-chained audit records appended synchronously under a per-tenant advisory
  lock (ADR-0003), inside the same transaction as the domain write.
- `audit.correct()` — the only correction mechanism. Appends a compensating
  record; the original stays.
- `audit.verify_tenant()` — walks a tenant's chain and reports content
  alteration, broken links and sequence gaps.
- `db_tenant_context` — restores the previous RLS scope on exit rather than
  leaving the last tenant's scope in place for the rest of the transaction.
- `disputeshield_doctor` now passes and fails for real, verified by a test that
  reverts the immutability migration and asserts the command refuses to proceed.

### Fixed — during phase 1

- **Chain verification did not deliver §8.3's claim.** Recomputing each record
  against its own stored `prev_hash` flagged only an edited record, leaving the
  rest of the chain apparently healthy. Verification now recomputes against the
  expected predecessor and carries it forward, so an edit invalidates every
  record after it. Amendment recorded in ADR-0003.
- **`SET LOCAL` is scoped to the transaction, not to the block that set it.** A
  transaction touching more than one tenant kept the last one's scope for
  everything that followed. Amendment recorded in ADR-0005.

### Tests — 53, all passing

- `test_tenant_isolation.py` — each of the three isolation layers asserted
  independently, including a registry walk that fails any future model given an
  unscoped default manager, and a demonstration that `all_tenants()` escapes
  layer 2 but not layer 3.
- `test_immutability.py` — every assertion bypasses the ORM. `UPDATE`, `DELETE`
  and `TRUNCATE` are each shown to raise in Postgres.
- `test_audit_chain.py` — tampering invalidates the edited record **and every
  record after it**; a deleted record shows as a sequence gap.
- `test_audit_concurrency.py` — eight concurrent writers, forty appends, one
  unbroken chain with contiguous sequences (ADR-0003).
- `test_doctor_detects_missing_trigger.py` — the doctor is shown to fail, because
  a preflight nobody has watched fail is a preflight nobody should trust.
- `test_foundations.py` — identifiers, key lifecycle, context restoration,
  middleware, and the §10.2 startup invariants.

### Changed

- CI now asserts what exists at the current phase. The frontend and image jobs are
  gated on their artefacts existing and light up in phases 4 and 6 — a job that
  asserts something the code cannot yet do is a red build that teaches people to
  ignore red builds, and one that passes vacuously is worse because it looks like
  coverage.
- Container image vulnerability gating split in two: HIGH and CRITICAL findings
  **with an available fix** block the build, while the full report including
  unfixed findings is published as an artefact. A gate that fails on
  vulnerabilities with no patch anywhere can never go green, and the only thing
  it reliably produces is a team that has learned to bypass it.
- The image applies `apt-get upgrade` at build time, so a Debian package fixed
  upstream cannot sit unapplied while §8.5's published remediation SLAs run down.
- `ruff format` no longer rewrites Python code blocks inside Markdown. The
  specification, the ADRs and the README are source documents whose examples are
  written for a reader.
- Local host ports moved to a dedicated block (Postgres 55433, PgBouncer 56432,
  Redis 56380/56381, Mailpit 8026) so DisputeShield coexists with the sibling
  projects on one machine.

### Added — phase 0, foundations

- Repository scaffold: the installable Django app (`disputeshield/`), the
  standalone server project (`server/`), and placeholders for the widget, loader,
  dashboard, SDKs and deployment artefacts.
- `pyproject.toml` targeting Python 3.12 with ruff, pytest, coverage at 85%,
  `bandit`, `pip-audit` and `gitleaks`.
- `Makefile` whose targets are exactly the ones CI runs.
- `compose.yaml` with Postgres 16, **two** Redis instances (§11.1), PgBouncer in
  transaction-pooling mode for the isolation suite (ADR-0005), and Mailpit.
- `.github/workflows/ci.yml` — five jobs, with the blocking gates split into
  their own job so a failure is unmissable rather than buried in a test run.
- `disputeshield_doctor` with its first four checks: audit immutability trigger,
  audit table grants, RLS applicability of the connecting role, and clock skew.
- Production invariant assertions in `disputeshield/conf.py`, enforced from
  `AppConfig.ready()` — the app refuses to start with `DEBUG=True`,
  `ALLOWED_HOSTS=['*']`, insecure cookies or `ATOMIC_REQUESTS` disabled.
- `TenantContextMiddleware`, establishing RLS context with `SET LOCAL`.
- `scripts/no-dangerous-html.sh` and `scripts/check-loader-size.sh` as CI gates.
- `scripts/hello-world.sh` with a CI-asserted step count.

### Documentation

- `docs/product-specification.md` — the complete product specification.
- `docs/plan-architecture.md` — twelve decisions the specification left open,
  each with its consequence, plus four deferred questions with the trigger that
  reopens them.
- `docs/ROADMAP.md` — thirteen phases with machine-checkable exit gates.
- `docs/AMPLIFIERS.md` — twenty capabilities beyond v1.0, each with a guardrail.
- `docs/runbook-sla-sweep.md` — the most important runbook in the product.
- ADR-0001 sandboxed iframe · ADR-0002 opaque session tokens · ADR-0003
  synchronous audit chain · ADR-0004 versioned SLA policies · ADR-0005 RLS under
  transaction pooling · ADR-0006 `PROTECT` not `CASCADE` · ADR-0007 materialised
  deadlines.
