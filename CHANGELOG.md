# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The PyPI package, the npm packages and the widget bundle are versioned together
and released from one tag.

## [Unreleased]

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
