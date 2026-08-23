# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The PyPI package, the npm packages and the widget bundle are versioned together
and released from one tag.

## [Unreleased]

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
