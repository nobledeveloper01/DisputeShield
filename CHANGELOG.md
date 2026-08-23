# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The PyPI package, the npm packages and the widget bundle are versioned together
and released from one tag.

## [Unreleased]

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
