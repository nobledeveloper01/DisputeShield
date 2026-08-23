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

Queue and case view land in phase 3; analytics and reports in phase 6.

Read `DESIGN.md` first. The rule that governs this surface: **colour is reserved
for time.** A healthy queue is monochrome.
