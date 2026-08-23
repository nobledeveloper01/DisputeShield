# sdk/

Client libraries. Apache-2.0, not BUSL — integrating with DisputeShield must never
require reference to the licence terms of the server.

| Package | Registry | Purpose |
|---|---|---|
| `node/` | `@disputeshield/node` | Server-side session token minting |
| `react/` | `@disputeshield/react` | `DisputeShieldProvider`, `DisputeButton` |
| `python/` | `disputeshield-client` | Server-side session token minting |

All three are versioned together with the server and released from one tag.

The token-minting clients strip the never-collect field set (§8.4) before the
request leaves the customer's process. The server independently rejects the same
data — two layers, because a customer on an old SDK version is a customer whose
SDK-side stripping is whatever it was last year.

Land in phase 6.
