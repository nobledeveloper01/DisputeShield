# widget/

The React application that runs **inside** the sandboxed cross-origin iframe.

It never runs in the host page's context. Everything crossing the boundary is an
explicit `postMessage` with a fixed schema and origin validation on both sides —
`'*'` is never a target origin, and a lint rule fails the build on it.

Lands in phase 4. See [ADR-0001](../docs/adr/0001-sandboxed-iframe-widget.md) for
why the boundary exists, and `DESIGN.md` for the rule that no DisputeShield brand
colour ever renders inside a customer's page.

CI gates on this directory: Playwright isolation in both directions, axe-core, a
keyboard-only walkthrough of the complete filing flow, and a CSP violation check
against the built bundle.
