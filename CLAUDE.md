# DisputeShield

Embeddable dispute and SLA management for fintechs. Read
`docs/plan-architecture.md` for the decisions the specification left open, and
`docs/ROADMAP.md` for what phase the project is in and what its exit gate is.
`PHASE` holds the current phase number.

## Design System

Always read `DESIGN.md` before making any visual or UI decision. Fonts, colours,
spacing and aesthetic direction are defined there. Do not deviate without
explicit approval.

Two rules from it are load-bearing and easy to break by accident:

- **The clock is the interface.** Time remaining is the primary visual quantity
  in the agent workspace. If a screen shows cases and does not show how close
  each one is to breaching, it is the wrong screen.
- **The widget must look like the host's product, not like ours.** No
  DisputeShield brand colour renders inside a customer's page. Theming is the
  tenant's; the only fixed thing is the layout.

In QA and review, flag any code that does not match `DESIGN.md`.

## The six things that are never traded

These outrank convenience, deadline and elegance. A change that weakens one is
wrong regardless of what it delivers.

1. **No edit or delete path on an auditable record.** Anywhere. Including the
   admin. A correction is an appended compensating record, never a rewrite.
2. **Cross-boundary reads return 404, not 403.** A 403 confirms existence.
3. **The widget serializer has no field path to internal content.** Adding a
   field to a shared base class is the way this breaks; the leakage test walks
   the field graph rather than sampling output.
4. **No code path from any module to money movement.** Refund amounts are
   recorded. Nothing acts on them.
5. **Every pause carries a mandatory reason.** A pausable clock is an abusable
   clock, and the reason is what makes abuse visible.
6. **RLS context is set with `SET LOCAL`, inside a transaction.** Plain `SET`
   leaks across pooled connections. ADR-0005 explains why the test suite runs
   through PgBouncer.

## Working on this repo

- `make ci` is the gate. It needs Postgres, PgBouncer and both Redis instances —
  `make up`, and copy `.env.example` to `.env` if those ports are taken.
- `make gates` runs the blocking suites alone: isolation, immutability,
  serializer leakage. These never go yellow.
- The deadline suite in `tests/test_sla_deadlines.py` runs twice, the second time
  under `TZ=Pacific/Kiritimati`. Deadline arithmetic must not depend on where the
  server is.
- The isolation suite talks to PgBouncer, not Postgres. Pointing it at Postgres
  directly makes it pass while testing nothing.
- Never widen `frame-ancestors` or relax the widget CSP to make something work.
  If the widget needs a capability the CSP forbids, the design is wrong.
- `dangerouslySetInnerHTML`, `v-html` and `innerHTML =` are banned in
  `widget/`, `dashboard/` and `loader/`. `scripts/no-dangerous-html.sh` enforces
  it in pre-commit and in CI.
- The loader has a 4 KB gzipped budget, enforced in CI. It is the only code that
  runs in a customer's page and it stays small enough to read in full.
- ADRs live in `docs/adr/`. Write one for any non-obvious decision, before the
  code that depends on it.

## Definition of Done

- [ ] Acceptance criteria met and demonstrated
- [ ] Tests written, coverage gate passed
- [ ] Tenant **and customer** isolation covered for any new data path
- [ ] Serializer leakage test updated if a new field was added
- [ ] No edit or delete path introduced on any auditable record
- [ ] Structured logging with no sensitive fields
- [ ] Metrics emitted for the new path
- [ ] Endpoint documented in `docs/openapi.yaml`
- [ ] Migration reviewed separately and reversible
- [ ] Accessibility checked if UI changed
- [ ] Runbook updated if a new failure mode was introduced
- [ ] `disputeshield_doctor` gained a check if a new install-time failure mode exists
- [ ] ADR written for any non-obvious decision

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool.
When in doubt, invoke the skill.

- Product ideas / brainstorming → `/office-hours`
- Strategy / scope → `/plan-ceo-review`
- Architecture → `/plan-eng-review`
- Design system / plan review → `/design-consultation` or `/plan-design-review`
- Full review pipeline → `/autoplan`
- Bugs / errors → `/investigate`
- QA / testing site behaviour → `/qa` or `/qa-only`
- Code review / diff check → `/review`
- Visual polish → `/design-review`
- Ship / deploy / PR → `/ship` or `/land-and-deploy`
- Security audit → `/cso`
