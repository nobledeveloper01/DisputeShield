# Design System — DisputeShield

## Product Context

- **What this is:** Two surfaces with opposite jobs. A widget that lives inside
  somebody else's product and must not look like ours, and a dashboard where an
  agent works a queue of regulated deadlines and a compliance officer proves what
  happened.
- **Who it's for:** Ngozi, Support Lead — works the queue all day, needs to know
  what breaches next without reading. Adaeze, Head of Compliance — non-technical,
  accountable for the regulatory relationship, lives in reports. A fintech's
  customer — anxious, on a phone, possibly missing money, using the widget once.
- **Space:** Regulated fintech operations tooling. The category's visual language
  is helpdesk SaaS: friendly, colourful, and completely indifferent to time.
- **Project type:** Data-dense internal web app plus an embedded consumer widget.
  Not a marketing site, and the design should not borrow from one.

### The memorable thing

> **"I could see which case breaches next without reading anything."**

Every decision below serves that sentence. An agent opens the queue eleven times
a day. If they have to read timestamps and do arithmetic to find the case that
matters, the product has failed at the one job that separates it from a shared
inbox — and the failure is invisible, because the queue still looks fine.

---

## The two surfaces are governed differently

| | Widget | Dashboard |
|---|---|---|
| Whose brand | **The tenant's.** Colour, radius, font and logo come from their theme configuration | Ours |
| Density | Generous. One decision per screen | Dense. A queue is a working tool |
| Read by | A customer, once, possibly distressed, on a phone | An agent, all day, on a desktop |
| Failure mode to avoid | Looking bolted on, or looking like a third party is now involved | Looking calm when something is about to breach |

**No DisputeShield brand colour ever renders inside a customer's page.** The
widget's fixed properties are layout, spacing, motion and accessibility. Everything
visual is the tenant's. A widget that announces our brand on their page is a
widget their design team removes.

---

## Aesthetic Direction — dashboard

- **Direction:** Operations console. Sober, dense, legible under fluorescent
  light at the end of a shift.
- **Decoration level:** Minimal. Typography, spacing and one signal colour do all
  the work. No gradients, no illustration, no card shadows floating on grey.
- **Mood:** An air traffic control display, not a product tour. The queue should
  feel like something with consequences attached.
- **Not this:** the category default — rounded cards, a cheerful accent colour on
  every button, and a status badge palette where "breached" and "resolved" are
  the same visual weight in different hues.

---

## Colour is reserved for time

This is the load-bearing rule, and it is the one most likely to be broken by
someone making the interface "nicer".

**If a pixel is saturated, it is telling you about a deadline.** Nothing else in
the dashboard gets colour: not the navigation, not the primary button, not links,
not the logo, not a chart series chosen for variety.

| State | Meaning | Treatment |
|---|---|---|
| **Breached** | The window has passed | Solid fill, highest contrast available, pinned to the top of the queue |
| **Critical** | Past 95% of the window | Saturated, and the only other thing on screen allowed to move |
| **Warning** | Past 80% | Saturated, static |
| **Notice** | Past 50% | Muted tint |
| **Comfortable** | Under 50% | **No colour at all** |
| **Paused** | Clock stopped | Desaturated with a visible hatch — a paused case must never read as a comfortable one |

Two consequences fall out of this and both are deliberate:

- A healthy queue is monochrome. That is the desired resting state, and it means
  the first spot of colour on the screen is genuinely the thing to look at.
- **Colour is never the only encoding.** Every state also carries a position in
  the sort order, a text label and a remaining-time figure, because roughly one in
  twelve men has a colour vision deficiency and because §9 makes accessibility a
  regulatory obligation rather than a preference.

The paused treatment deserves its own note. A paused clock is not a safe clock —
it is a clock somebody stopped, for a reason, and possibly for the wrong reason.
Rendering it as calm is how pause abuse becomes invisible, which is exactly what
the mandatory-reason requirement exists to prevent.

---

## Typography

Three families, each with a job. Nothing is chosen for looks alone.

- **Display / headings: Instrument Serif.** A serif in an operations console is
  unusual, which is the point: it signals *record* rather than *app*, and it
  separates headings from data without a second colour or a heavier weight. 24px
  and above only — below that it loses both its argument and its legibility.

- **Body, UI and labels: Geist.** Neutral, excellent at 13–15px, genuine tabular
  figures. Not Inter: every product in this category uses Inter, and this one has
  a reason to look like it was designed rather than defaulted.

- **Data, tables and clocks: Geist with `font-variant-numeric: tabular-nums`.**
  Non-negotiable. A countdown whose digits reflow as it ticks is a countdown
  nobody can read at a glance, and a column of amounts whose digits do not align
  is a column somebody misreads.

---

## The clock component

The single most important element in the product. It appears in the queue, on the
case, in the breach analysis view and in the widget, and it must read identically
in all four.

- **Time remaining, not the deadline timestamp.** "4h 12m left" needs no
  arithmetic; "resolve by 18:42 Thursday" needs the reader to know what time it is
  now and which day it is.
- **Precision decreases with distance.** Under an hour: minutes. Under a day:
  hours and minutes. Beyond that: days. False precision on a three-day window is
  noise pretending to be information.
- **Negative time is not a minus sign.** A breached case reads `BREACHED · 2h 14m
  ago`, never `-2h 14m`. A minus sign is something a tired reader misses.
- **Paused reads as paused first.** `PAUSED · 22h left` with the reason
  one hover away, never a bare figure that looks live.
- **It ticks, but it does not animate.** A recomputation every thirty seconds. A
  smooth countdown animation on eleven rows is motion that costs attention and
  buys nothing.

---

## Layout and density

- **Queue rows are 44px.** Dense enough to see twenty cases without scrolling,
  tall enough to hit on a laptop trackpad.
- **The sort order is the design.** Default is time-remaining ascending, breached
  cases pinned. A user who has to sort the queue to find urgent work is using a
  table, not a queue.
- **8px spacing scale**, no exceptions, so vertical rhythm survives contributors
  who have not read this document.
- **The case view is two columns:** conversation on the left, clock and context
  fixed on the right. The clock never scrolls out of view, because the whole
  point is that it is always true.
- **Internal notes are visually incapable of being mistaken for customer
  messages.** Different background, different alignment, a persistent label, and
  a distinct composer. The structural guarantee (§10) prevents a leak; this
  prevents an agent believing they wrote one thing when they wrote another.

---

## Motion

- **Under 150ms or not at all.** The only exception is the critical-state pulse,
  and it is the only moving thing permitted on the screen.
- **No skeleton screens in the queue.** A queue that renders progressively invites
  action on a partial sort order. Render when the sort is known.
- **`prefers-reduced-motion` removes the pulse** and leaves the fill, the label
  and the position. The signal survives; only the movement goes.

---

## Widget-specific rules

- **Fails closed and silently.** If DisputeShield is unavailable the widget does
  not render. It never shows an error on a customer's page, and it never blocks
  the host page's load (§8.6).
- **One decision per screen.** Which transaction, then what happened, then
  confirm. A customer who has lost money is not reading a form.
- **The expected resolution date is shown at filing**, before submission, in
  plain language. The commitment is a regulatory quantity, so the customer should
  learn it from us rather than from silence.
- **Fully keyboard navigable, WCAG 2.1 AA**, including a keyboard-only path
  through the entire filing flow — asserted in CI. A dispute channel a
  screen-reader user cannot operate is a regulatory problem, not a usability one.
- **No host-page fonts inherited.** The iframe boundary means we choose, and the
  tenant's theme configuration overrides. Inheriting produces a widget that
  breaks when their design system ships a change.
