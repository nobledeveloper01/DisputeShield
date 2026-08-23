# ADR-0001 — The widget runs in a sandboxed cross-origin iframe, not inline on the host page

**Status:** accepted
**Date:** 2026-08-23

## Context

DisputeShield is embedded by a fintech into a page that handles money. The obvious implementation —
the one every widget tutorial teaches, and the one most embeddable products ship — is a script that
mounts a React tree directly into the host's DOM.

Inline mounting shares one JavaScript context and one DOM between the host application and the
widget. That means two things simultaneously, and both matter:

- **The widget can read the host page.** Every form field on the page is reachable from widget code,
  including the account number the customer is typing, the balance rendered above it, and any session
  identifier in a hidden input. Nothing prevents it; only our good behaviour does.
- **The host page can read the widget.** A compromised host page — a bad dependency, a tag manager
  someone added, a supply-chain compromise in an analytics script — can read the widget's session
  token out of memory and use it to enumerate that customer's disputes.

The security question a customer's engineer will actually ask (Tunde, §3.1) is not "is your code
good?" It is "what can your code reach?" An inline widget's honest answer is "everything on the
page", and the follow-up — that we promise not to — is not a control. It is a policy, and policies do
not survive a compromised dependency.

## Decision

A ~4 KB loader runs on the host page. It creates a sandboxed cross-origin iframe and does nothing
else. The entire widget runs inside that iframe.

```html
<iframe
  src="https://widget.disputeshield.dev/v1/embed?k=pk_live_..."
  sandbox="allow-scripts allow-forms allow-same-origin"
  allow=""
  referrerpolicy="strict-origin">
</iframe>
```

Host and widget communicate only by `postMessage`, with a fixed message schema and strict origin
validation on both sides. `'*'` is never used as a target origin — passing `'*'` is the single most
common way a widget integration leaks data, and a lint rule fails the build on it.

The loader's job is deliberately tiny. It is the only DisputeShield code that runs in the host's
context, so it is small enough for a reviewing engineer to read in full before putting it on a
payments page — which is exactly what we want them to do.

## Consequences

**What this buys.**

- The boundary is enforced by the browser, not by us. The customer does not have to trust our
  discipline, and can verify the whole claim in devtools in ten seconds.
- The session token lives in a context the host page cannot reach.
- Our CSS and the host's CSS cannot collide in either direction, which removes an entire category of
  support ticket as a side effect.
- `frame-ancestors` (§10.1), generated per tenant, means a leaked publishable key still will not
  render the widget on an attacker's page.

**What this costs.**

- Anything crossing the boundary must be an explicit message. Height changes, focus management,
  deep links and the open/close state are all protocol, not function calls.
- Focus and keyboard management across a frame boundary is genuinely harder, and the accessibility
  gate in phase 4 is a keyboard-only walkthrough precisely because this decision makes that the
  likeliest thing to break.
- One extra document request on load, against the 500 ms p95 budget of §11.3. Mitigated by ADR-0002's
  split of the dynamic document from the immutably cached bundles.
- Older mobile browsers handle `sandbox` inconsistently; the loader detects and fails closed rather
  than falling back to inline. **There is no inline fallback.** A fallback that degrades to the
  insecure mode under conditions nobody tests is the insecure mode.

## Alternatives considered

**Inline React with a shadow DOM.** Shadow DOM encapsulates styling. It is not a security boundary:
same JavaScript context, same globals, full mutual access. It solves the CSS collision and none of
the reasons this decision exists.

**Web component with a closed shadow root.** Same objection. `closed` restricts convenient access,
not determined access.

**A hosted redirect to a full-page dispute portal.** Genuinely secure, and it abandons the product's
central claim — that a customer files from inside the app they are already using. The drop-off between
"tap here" and "leave the app, authenticate again, file" is the entire reason complaints end up on
Twitter instead.
