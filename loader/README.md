# loader/

The ~4 KB script that runs on the host page. It creates the sandboxed iframe and
does nothing else.

This is the **only** DisputeShield code that executes in a customer's page
context, which is why it has a hard 4 KB gzipped budget enforced by
`scripts/check-loader-size.sh` in CI: it stays small enough for a reviewing
engineer to read in full before putting it on a payments page. That review is
something we want to happen, so the size budget is a product decision, not a
performance one.

There is no inline fallback. On a browser with unreliable `sandbox` support the
loader fails closed — a fallback that degrades to the insecure mode under
conditions nobody tests *is* the insecure mode.

Lands in phase 4.
