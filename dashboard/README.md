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

**Built so far:** the queue (`src/queue/`), the case view (`src/case/`) and
report delivery (`src/reports/`). Breach analysis, SLA policies, widget config
and settings are still to come.

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
