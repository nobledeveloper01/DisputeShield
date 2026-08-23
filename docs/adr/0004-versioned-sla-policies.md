# ADR-0004 — SLA policies are immutable versions; a dispute pins the version it was filed under

**Status:** accepted
**Date:** 2026-08-23

## Context

§5 gives `Dispute` a foreign key to `SLAPolicy`. §6.5 lets a compliance officer edit a policy from
the dashboard without a deploy — correctly, because filing an engineering ticket to correct a
regulatory window is absurd.

Together they produce a quiet failure. Editing a policy retroactively changes the standard every
open case is judged against, and because `ack_deadline` and `resolution_deadline` are stored columns
computed at filing time, stored deadlines silently stop matching the policy that supposedly produced
them.

The consequence appears months later. A supervisor asks why a case breached. The record shows a
48-hour window. The case actually ran against the 72-hour window in force at the time, and that
number no longer exists anywhere in the system. The audit trail is intact, the answer it gives is
wrong, and it is confidently wrong — which is worse than not having an answer at all.

## Decision

`SLAPolicy` is a container. `SLAPolicyVersion` holds the fields — windows, calendar, thresholds,
escalation percentage, `regulatory_reference`, `auto_close_after_hours`, `reopen_window_hours` — and
is immutable once any dispute references it.

`Dispute.sla_policy_version` is a `PROTECT` foreign key to the exact version in force at filing.
Editing a policy in the dashboard creates version n+1, recording its author and effective time. Open
cases keep the version they were filed under; a policy change applies to cases filed after it.

## Consequences

- One additional table and a version resolution at filing time.
- §6.5's promise that policy changes are "recorded, versioned and visible next to the breach data
  they affect" becomes structurally true rather than a reporting feature someone has to remember to
  build.
- Amplifier A9, the policy simulator, requires historical policy state to replay history correctly.
  Building it after the fact would mean adding this schema retroactively against data that could no
  longer support it — the old versions would simply be gone.
- A compliance officer who wants a change to apply to open cases must say so explicitly, per case or
  in bulk, and each application is an audited event with a reason. That is the correct amount of
  friction for retroactively changing the standard a live complaint is being judged against.
- Versions accumulate and are never deleted. They are small rows; the retention obligation on them is
  the same seven years as everything else, and `PROTECT` means a version referenced by any case
  cannot be removed even deliberately.
