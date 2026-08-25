# dashboard/

The agent workspace and compliance views.

| Section | For | Contents |
|---|---|---|
| Queue | Agents | Cases sorted by time remaining; breached pinned; filters |
| Case | Agents | Conversation, internal notes, attachments, context timeline, SLA clock, action history |
| SLA policies | Compliance | Windows, calendars, thresholds, escalation — with change history |
| Breach analysis | Compliance | Breaches by category, agent and cause; pause-duration analysis |
| Reports | Compliance | Regulator-ready export with integrity attestation |
| Widget | Engineers | Theming, allowed origins, categories, live preview |
| Settings | Owners | API keys, team, SSO, retention |

Roles are Owner, Compliance, Agent and Read-only. An agent can resolve a case but
cannot change an SLA policy; a compliance user can change a policy, and the change
is recorded, versioned and rendered next to the breach data it affects.

**Built:** all seven sections — the queue (`src/queue/`), the case view
(`src/case/`), SLA policies (`src/policies/`), the widget configuration
(`src/widget/`), breach analysis (`src/analysis/`), report delivery
(`src/reports/`) and settings (`src/settings/`).

## Running it

```bash
npm install && npm run dev     # against a local API on the same origin
npm test                       # the schedule-health rules
npm run test:e2e               # keyboard and axe, against a stubbed API
```

The browser suite stubs the API deliberately. Those tests exist to assert that a
keyboard-only compliance officer can operate the surface and that it has no WCAG
AA violations; neither needs a database, and a suite slow enough to skip is an
accessibility gate that has stopped being one. The API contract underneath is
covered by the Python suite, through real authentication, roles and RLS.

## The clock

`src/clock.js` holds the rules DESIGN.md sets out — precision decreasing with
distance, `BREACHED · 2h 14m ago` rather than a minus sign, paused reading as
paused first, thresholds as percentages of the window. They are pure functions,
tested without a browser, and every surface renders what they return, so the
clock cannot read one way in the queue and another on the case.

**Which quantity it shows.** The server computes two different things:
`resolution_deadline` is the instant a case is due, already calculated using the
tenant's business hours; `sla.remaining_seconds` is *business* time left, and the
queue endpoint deliberately does not compute it per row because doing so cost an
N+1 and a calendar walk per case and put the queue over its 300ms p95.

Showing time-to-deadline in the queue and business time on the case under one
label would make the same case read "2d 16h left" in one place and "0h left" in
the other — and in the dangerous direction, because an agent triages from the
queue. So the clock is time-until-deadline everywhere, and the case additionally
shows business time under its own explicit label. Two figures with two labels is
honest; one label over two quantities is not.

## Settings

Every action there is either irreversible or capable of locking somebody out, and
none of them is urgent. So each control states its consequence *before* it is
used rather than explaining itself in a refusal afterwards.

Three things are worth knowing about the shape:

- **A key is shown once.** Only an Argon2id hash is stored, so there is nothing
  to show later. The panel says so, the value lives in component state and
  nowhere else, and the browser suite asserts it never reaches the URL,
  `localStorage` or `sessionStorage`.
- **The last active owner cannot be demoted or deactivated, and nobody changes
  their own role.** Both are enforced by the server; the dashboard mirrors them
  as disabled controls with the reason rendered visibly and wired through
  `aria-describedby`. A disabled control whose reason lives only in a `title` is
  a considered refusal that reads as a broken interface.
- **Retention is reported, not configured**, and there is **no SSO form**. Seven
  years is a regulatory floor rather than a preference, and nothing in this
  product implements SAML or OIDC — a settings screen offering a configuration
  that goes nowhere costs an evaluation and then a support ticket before anybody
  finds out.

## SLA policies

The screen is built around the fact that nothing on it is edited. Terms are
immutable and versioned (ADR-0004): a change publishes version n+1, and every
case keeps the version it was filed under. So the button says **"Publish version
3"** rather than "Save", the change history sits beside the terms rather than
behind a tab, and the form states that filed cases keep their version.

That is not decoration. An officer who leaves believing they edited a setting has
the wrong model of the system in the one situation that matters — a supervisor
asking which standard a case was judged against.

`§7.3` documents `PATCH /v1/sla-policies/{id}` while the architecture says the
terms are immutable. The two are reconciled in `disputeshield/sla/policies.py`
rather than by picking one: a PATCH is accepted and its effect is to publish a
new version. A sparse body carries forward what it did not mention, so a PATCH of
one field cannot publish a version whose other terms silently became defaults.

## The widget configuration, and the one exception to the colour rule

The preview is the only element in this console whose colour is not about time,
and the exception is deliberate. DESIGN.md's rule governs *our* chrome; the
colour in the preview belongs to the tenant, and the widget's whole point is that
it looks like their product rather than ours. A preview rendered in ink would be
a preview of something that does not exist.

Two things keep the exception from leaking: the colour is applied through an
inline custom property scoped to the preview subtree, so nothing outside it can
inherit a tenant's brand (asserted in the browser suite), and the preview is
drawn inside a labelled frame so it reads as a picture of another product.

The screen leads with a cross-check rather than with theming. A category the
widget offers with no SLA policy behind it lets a customer choose it and then
refuses their filing with "Unknown category" — and nobody on this side of the
product finds out. That warning is **monochrome**, and that is a decision rather
than an omission: a category with no policy is not a deadline, and stretching
"colour is reserved for time" a second time would leave the console with two
kinds of red meaning two different things. It gets first position, a heavy rule,
a label and the count instead.

## Breach analysis

Two things on that screen are placement decisions rather than layout, and both
come from reasoning already in the backend:

- **Deflections sit beside case volume**, never in their own panel. A feature
  that reduces recorded complaints has to be the most heavily instrumented thing
  in the product — a fall during an outage must be visibly a deflection rather
  than silently a suppression, and two numbers in two panels are two numbers
  nobody puts together.
- **Undocumented causes are separated from the ranked ones.** §11.5 requires
  every breach in an incident window to be annotated with its systems cause.
  Sorting causes by frequency buries "we don't know" behind whichever incident
  happened to be biggest.

There are deliberately **no breach-rate bands**. A green/amber/red scale at, say,
5% and 10% would be read as authoritative by the person least able to check it.
Any breach at all is a missed deadline, which is what earns colour; the bar
length carries the magnitude.

## Where the derived state comes from

A schedule's health — which months are owed, whether it is overdue — is computed
by the **server**, by the same code the runner uses, and rendered here. The month
arithmetic is subtle (closed months, the schedule's own timezone, a due date in
the month after the period) and a second implementation in JavaScript would
eventually disagree with the one that actually sends the mail. A dashboard that
says a schedule is healthy while the runner thinks a month is owed is worse than
no dashboard.

Read `DESIGN.md` first. The rule that governs this surface: **colour is reserved
for time.** A healthy queue is monochrome.

How that applies to report delivery, since it is not obviously a queue: a monthly
regulatory return that did not go out **is** a missed deadline, so a schedule's
health is rendered on the same scale a case's clock is — abandoned months take
the breached treatment and are pinned to the top, an overdue schedule is
saturated, a month recently due is a muted tint, and a schedule that is up to
date has no colour at all. A deactivated schedule takes the paused treatment,
hatch included, because DESIGN.md is explicit that a stopped clock must never
read as a comfortable one.

The recipients table has no colour anywhere. Nothing on it is about a deadline.
