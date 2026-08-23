# Amplifiers

Twenty capabilities beyond the v1.0 specification (`docs/product-specification.md`), each one
chosen because it either removes an adoption blocker, deepens the evidentiary claim the product is
sold on, or turns dispute data into something the customer cannot get anywhere else.

They are not a wish list. Every entry below states the **guardrail** it must not cross, because most
of them are ways to accidentally turn a compliance system of record into a system that guesses, or
into one that moves money. Neither is acceptable, and the guardrail is the part that makes the
feature shippable rather than the idea.

Phase assignments are in `docs/ROADMAP.md`. Nothing here lands before v1.0 ships.

---

## The four things they are collectively for

| Goal | Amplifiers |
|---|---|
| **Every complaint lands in the clock** — a complaint that never becomes a case is an unmeasured regulatory exposure | A1, A2, A3, A4 |
| **The evidence survives contact with a lawyer or a supervisor** | A5, A6, A7, A8 |
| **Dispute data becomes an asset rather than a cost centre** | A9, A10, A11, A12, A13 |
| **The system is adoptable, operable and enterprise-sellable** | A14 – A20 |

---

## A1 — Omnichannel intake gateway

**What.** Email, WhatsApp Business, USSD session, inbound call log, X/Twitter DM and web form all
land as the same `Dispute` object, on the same clock, in the same queue. An email to
`complaints@tenant.com` is forwarded to a per-tenant ingest address; a threaded reply from the
customer becomes a `DisputeMessage` rather than a new case.

**Why it earns its place.** §2.1 of the specification names the real problem precisely — complaints
arrive through six channels and end up in a shared inbox. The widget solves exactly one of those
six. A fintech that installs DisputeShield and still runs a shared inbox has two systems of record
and a regulatory answer of "some of them are tracked", which is worse than one untracked inbox
because it looks like coverage.

**The hard part.** Thread identity. Matching a reply to an existing case across a client that
rewrites `Message-ID`, a customer who replies from a different address, and a WhatsApp number that
belongs to a shared family phone. Get it wrong in the permissive direction and you attach one
customer's message to another customer's case — a data breach, not a bug. The matcher must fail to
`unmatched_review` rather than guess.

**Guardrail.** Channel identity never grants case access on its own. An inbound message from an
address that is not the case's verified contact is quarantined for agent attribution, never
auto-appended, and never echoed back into the thread.

---

## A2 — Deflection and known-issue layer

**What.** Before the filing form renders, the widget checks whether the transaction the customer
selected is already covered by a declared incident. If it is, the customer sees the truth — *"we
know: GTBank transfers from 09:10–11:40 failed, reversals are running, expected by 18:00"* — and a
one-tap **"notify me"** subscription instead of a blank complaint box.

**Why it earns its place.** During an outage the queue receives thousands of copies of one
complaint. Every copy consumes an SLA clock, an acknowledgement, an agent touch and a resolution
record. Deflection is the only feature here that reduces load rather than adding it, and the
customer experience is better: an accurate answer now beats a case reference and a 72-hour wait.

**The hard part.** Deflection that is wrong is complaint suppression, and complaint suppression is
the single worst accusation a regulator can make about a complaints system. Every deflection is
therefore itself an audit record, is counted in a `deflections_total` metric reported next to case
volume, and always exposes an unconditional **"file anyway"** control that no configuration can
remove.

**Guardrail.** Deflection may never be the only path. A customer who wants a case gets a case,
always, in one tap, with a full SLA clock.

---

## A3 — Mass-incident mode

**What.** When one root cause produces many cases, an agent groups them into a `MassEvent`. The
investigation, the finding and the outcome are recorded once; applying the outcome fans out to
every member case, each receiving its own resolution record, its own customer notification and its
own audit entry.

**Why it earns its place.** This is the difference between a system that survives a bad day and one
that collapses on it. Four thousand cases about a single failed rail is one investigation and four
thousand records — and without this, an agent either resolves four thousand cases by hand or
resolves them dishonestly with a copy-paste that says nothing specific.

**The hard part.** Bulk resolution is a bulk-edit surface on immutable records, which is precisely
the thing §8.3 forbids. The implementation is a fan-out of individual appends, never an `UPDATE`
over a set. It is slow, it is auditable, and slow-and-auditable is correct here.

**Guardrail.** Membership of a mass event is per-case, individually recorded and individually
reversible. A case removed from an event keeps everything that happened to it while it was a member.
No case's clock is paused by group membership.

---

## A4 — Provider context connectors

**What.** Optional adapters — Paystack, Flutterwave, NIBSS, Stripe, a generic REST adapter — that
enrich a case at filing time with the provider's own view: the transaction, its status transitions,
the reversal attempt, the settlement record.

**Why it earns its place.** §11 of the specification measures the win as agent time not spent
gathering context, and §7.1 achieves that by having the fintech push context at token-mint time.
That works only for context the fintech's own database holds. The answer to *"did the reversal
actually leave the rail?"* lives at the provider, and it is the answer that closes the case.

**The hard part.** The specification's strongest security position is that *"DisputeShield never
queries the fintech's database and holds no standing access to it"* (§7.1). A connector holding
provider credentials weakens that, and pretending otherwise would be dishonest. So connectors are
opt-in per tenant, per provider; credentials are envelope-encrypted with a per-tenant key; every
outbound call is audited with the exact request made; and the connector is read-only by
construction — the adapter interface exposes no write method for a caller to reach.

**Guardrail.** Never a write path to a provider. DisputeShield does not retry a payment, trigger a
reversal or touch a rail. It reads, records and shows.

---

## A5 — Chargeback and scheme representment pack builder

**What.** For card disputes, assemble the acquirer-facing representment pack: reason code mapping,
the required evidence checklist for that code, the customer's own statements, the transaction and
authorisation record, the deadline for the scheme's own window — exported in the format the
acquirer accepts.

**Why it earns its place.** It is the one place where the product moves from cost avoidance to
recovered revenue. A representment that misses the scheme deadline or omits a required element is
money the fintech simply loses, and the evidence needed is already sitting in the case.

**The hard part.** Two clocks that are not the same clock. The regulatory resolution window and the
scheme representment window run concurrently, have different rules, and one can expire while the
other is comfortable. Both must be visible on the case, and a breach of either must alert
separately.

**Guardrail.** DisputeShield builds and exports the pack. It does not submit it, and it never
represents itself as having submitted it. Submission is the acquirer's channel and the fintech's
decision.

---

## A6 — External escalation and ombudsman tracking

**What.** When a customer escalates past the fintech — to the regulator's consumer protection desk,
an ombudsman, or a court — the case gains an external track: the external reference, that body's own
deadlines, the correspondence, and the final determination recorded against the internal outcome.

**Why it earns its place.** An escalated complaint is the highest-stakes case in the system and the
one most likely to be handled in a personal inbox. The internal case being closed while the external
one is live is exactly the state that produces "the firm was unresponsive" in a supervisory finding.

**The hard part.** The internal case must not close while an external track is open, and the closure
rule has to be enforced in the state machine rather than in a convention.

**Guardrail.** External determinations are recorded, never inferred. If the ombudsman's outcome
contradicts the internal one, both stand in the record, with the contradiction visible. Overwriting
the internal outcome to match would destroy the most interesting evidence in the case.

---

## A7 — Legal hold and the evidence vault

**What.** A named hold placed over a case, a customer, a category or a date range that suspends
every retention and deletion process touching it — including the data-subject deletion procedure of
§11.7 — until an authorised user releases it, with a reason.

**Why it earns its place.** §11.7 promises 7-year retention and a tested deletion procedure. The
moment a case is in litigation, those two promises point in opposite directions, and automated
deletion of material under legal hold is spoliation of evidence. There is no correct behaviour to
default to; the hold has to be an explicit object.

**The hard part.** Interaction with the right-to-erasure path. The hold wins, the requester is told
that it won, and the reason is recorded — because refusing an erasure request silently is its own
violation.

**Guardrail.** Placing and releasing a hold are both audited, both require reasons, and a release
requires a second authorised approver. A hold that one person can quietly lift is not a hold.

---

## A8 — Audit chain anchoring

**What.** The per-tenant hash chain of §8.3 gains external anchoring: RFC 3161 trusted timestamps on
each nightly checkpoint, and publication of checkpoint roots to an append-only transparency log.

**Why it earns its place.** The chain proves internal consistency — that no record was altered
relative to its neighbours. It does not prove *when* the chain existed, so an adversary with full
control could in principle rebuild a consistent chain after the fact. An external timestamp closes
that gap, and turns "we can show our records are consistent" into "we can show these records existed
on this date and a third party attests to it".

**The hard part.** Anchoring must degrade without stalling. A timestamp authority that is
unreachable cannot be allowed to block writes; unanchored checkpoints queue, are visibly reported as
unanchored, and are anchored on recovery.

**Guardrail.** Anchoring supplements the chain, never replaces it. `GET /v1/audit/verify` reports
chain status and anchor status as two separate facts, because they answer two separate questions.

---

## A9 — SLA policy simulator

**What.** Before a compliance officer saves a policy change, replay it against the last 90 days of
real cases: how many of those cases would have breached under the proposed window, which categories
absorb the change, which agents' queues get harder, what the acknowledgement load becomes.

**Why it earns its place.** §6.5 lets a compliance officer change an SLA policy without a deploy —
correctly, because filing a ticket to fix a regulatory window is absurd. But a policy change is a
change to a control, and the specification gives its author no way to see the consequence before
committing to it. This is the counterpart to ComplyLayer's approval diff: make the magnitude of the
change loud at the moment it is made.

**The hard part.** Replay must use the historical business calendar and the historical pause
intervals, not today's. A simulation against the current calendar is a confident wrong number, which
is worse than no number.

**Guardrail.** The simulator is read-only and runs against the read replica. It never writes an
SLA event, never notifies, and its output is stored with the policy version it evaluated so the
change record shows what the author was told at the time.

---

## A10 — Root-cause clustering and the defect feedback loop

**What.** Cluster cases by transaction attributes, provider, rail, product, error code and free-text
similarity. Surface the clusters as *causes* with a case count, a financial exposure and a trend,
and expose them to engineering as a ranked backlog.

**Why it earns its place.** §2.3 identifies the compounding effect: every unhandled transaction
failure becomes a dispute. Handling disputes well treats symptoms forever. This is the feature that
lets a Head of Compliance walk into an engineering planning session with *"these 340 cases and
₦4.1m of exposure are one bug in the airtime provider's timeout handling"*.

**The hard part.** A cluster is a hypothesis, and hypotheses presented with the confidence of facts
get acted on wrongly. Clusters show their membership, their evidence and their strength, and every
one is inspectable down to individual cases.

**Guardrail.** Clustering never modifies a case. It does not set category, priority, outcome or
assignment. It is a lens over the record, never a writer to it.

---

## A11 — Triage and routing intelligence

**What.** On filing, suggest category, subcategory, priority and the best-matched agent or team,
from the description, the transaction context and the tenant's own resolved history.

**Why it earns its place.** Category selection decides which SLA policy applies, and therefore which
regulatory window the case runs on. A customer picking the wrong item from a dropdown starts the
wrong clock, and nobody notices until the case breaches a window it was never on.

**The hard part.** The v1 scope table lists priority prediction under **Won't**. That exclusion is
right for anything that acts on its own, and this must not become a quiet reversal of it. So the
suggestion is advisory, pre-filled but freely overridable, and every acceptance or override is an
audit record — which also produces the training signal and the accuracy metric.

**Guardrail.** A model never sets the final category. It proposes; a human or the customer disposes.
Model identifier and version are recorded on every suggestion, so a shift in behaviour is
attributable.

---

## A12 — Agent copilot

**What.** A drafted reply, grounded strictly in the case's own content — the messages, the context,
the tenant's response templates and their resolved-case history — presented in the composer for the
agent to edit or discard.

**Why it earns its place.** Median time to first substantive response is the metric customers feel,
and most of an agent's time goes into re-typing a paragraph they have written two hundred times.
§3.3 already includes response templates; this is templates that know which one applies.

**The hard part.** Grounding. A drafted reply that invents a refund date is a commitment made to a
customer on the firm's behalf by a system with no authority to make it. Retrieval is restricted to
the case and the tenant's own artefacts, and any draft containing a date, an amount or a commitment
not present in its sources is blocked from insertion rather than flagged.

**Guardrail.** No autonomous send, ever, on any channel, under any configuration. The draft is
recorded alongside what the agent actually sent, so the difference between the two is itself
measurable evidence about the tool.

---

## A13 — Repeat-claimant and first-party fraud signals

**What.** Surface patterns that indicate abuse of the dispute process itself: the same customer
claiming non-receipt across many providers, disputes filed minutes after every successful delivery,
device and behavioural overlap across nominally distinct customers, claim rates far outside the
customer's own history.

**Why it earns its place.** Dispute abuse is a real and quantifiable loss for a fintech, and the
data that reveals it exists nowhere else in their stack — the pattern is only visible across
disputes.

**The hard part.** This is the amplifier with the most serious failure mode. A signal that
influences an outcome turns a complaints system into an automated denial system, which is a
consumer-protection violation with the audit trail helpfully documenting it.

**Guardrail.** Signals are visible to agents as context with their evidence attached. They are never
an input to an SLA policy, never change a priority, never gate a channel, and are structurally
incapable of producing an outcome. A rejection must always be justified by case-specific findings
recorded by a named human, and the case record shows the signal was present without showing it was
decisive.

---

## A14 — Outbound webhooks and event stream

**What.** Signed, ordered, replayable events — `dispute.created`, `dispute.acknowledged`,
`sla.warning`, `sla.breached`, `dispute.resolved`, `mass_event.applied` — delivered to the fintech's
own systems, using the HMAC scheme already defined in §8.2.

**Why it earns its place.** The specification has DisputeShield receiving context and offering
export, but nothing pushes outward in real time. A dispute resolved with an upheld outcome and a
recorded refund amount is an event the fintech's ledger, ops tooling and analytics all need, and
polling a management API for it is how integrations rot.

**The hard part.** Delivery guarantees. At-least-once with idempotency keys, ordered per dispute,
retried with backoff, parked rather than dropped when a customer endpoint is down for a day.

**Guardrail.** Webhook payloads carry the customer-visible projection only. The internal-note
exclusion of §10 applies identically here — the same serializer-graph test that protects the widget
protects the webhook, or it is not protected.

---

## A15 — Quality assurance sampling and agent scorecards

**What.** Supervisors sample a configurable percentage of resolved cases, score them against a
rubric — accuracy, tone, evidence quality, correct outcome, policy adherence — and the scores roll
up into per-agent and per-team coaching views.

**Why it earns its place.** §3.2 gives the compliance officer breach counts, which measure whether
cases were closed in time. Nothing measures whether they were closed *well*. A firm that resolves
every case within its window by consistently rejecting valid complaints has perfect SLA metrics and
a serious problem, and supervisory frameworks ask specifically how firms assure complaint-handling
quality.

**The hard part.** Sampling must be defensibly random, with an override to force-review any case
that meets risk criteria — reopened, escalated, high-value, vulnerable-customer flagged.

**Guardrail.** A QA score is not an audit record about the case; it is a record about the review.
Reviews attach to the case without altering its history, and an agent can see and respond to every
score about their own work.

---

## A16 — Financial exposure and provisioning view

**What.** Total value under dispute, sliced by category, age, provider and probability of being
upheld, derived from the tenant's own resolution history. Recorded refund liability from resolved
cases, reconciled against what the fintech's ledger says it actually paid.

**Why it earns its place.** It makes the product legible to a CFO, which is who signs the enterprise
contract. The `refund_amount_minor` field already exists on every resolved case and today it is
recorded and never summed.

**The hard part.** The reconciliation. DisputeShield knows what was *promised*; only the fintech's
ledger knows what was *paid*. The gap between the two is the interesting number, and surfacing it
requires the fintech to send back settlement confirmations — an integration, not a report.

**Guardrail.** §3.3 puts executing refunds under **Won't**, permanently. This view records, sums,
projects and reconciles. It never initiates a payment, and there is no code path from it to money
movement.

---

## A17 — Regulatory returns automation

**What.** Scheduled generation of the periodic complaints returns a licensed institution files —
volumes by category, resolution times, breach counts with reasons, outstanding cases by age — in the
template the supervisor expects, with a maker-checker sign-off before release.

**Why it earns its place.** §6.5's regulator-ready export answers an ad-hoc request. Returns are the
recurring obligation: the same shape, every month or quarter, assembled by hand under deadline. This
converts a scheduled fire drill into a review-and-approve.

**The hard part.** Templates are jurisdiction-specific and change. They live as versioned,
data-driven definitions rather than code, so a template revision is a configuration change with its
own history, and a return produced last year can still be reproduced exactly as it was filed.

**Guardrail.** Nothing is filed automatically. A return is generated, reviewed, approved by a named
person, and the approved artefact is hashed into the audit chain — so what was filed is provable
later.

---

## A18 — Migration and import tooling

**What.** Importers for Zendesk, Freshdesk, Intercom, a CSV export and an IMAP mailbox archive.
Historical cases arrive with their original timestamps, their message history and their original
resolution, marked as imported and excluded from live SLA computation.

**Why it earns its place.** It is the single largest adoption blocker in the product. A compliance
officer cannot adopt a system that starts empty, because their retention obligation covers cases
that already exist, and running the old system for seven years alongside the new one is not an
answer anyone accepts.

**The hard part.** Imported history must be clearly distinguishable from natively recorded history,
forever. An imported case's audit trail carries no integrity claim from us — we did not witness it —
and the chain must state that plainly rather than absorbing foreign data and implying we vouch for
it.

**Guardrail.** Imported records enter the chain as attested imports: hashed at import time, with
their source, importer identity and import time recorded. Their content is never presented as
DisputeShield-witnessed evidence.

---

## A19 — Sandbox mode, simulator and seeded demo tenant

**What.** A `test` environment per tenant with full functionality and no notifications leaving the
building; a `disputeshield simulate` CLI that generates realistic case flow — including breaches,
pauses, reopenings and a mass incident — and a one-command demo tenant.

**Why it earns its place.** Every §3.1 persona is blocked without it. Tunde cannot test an
integration whose central behaviour is a 72-hour clock. Adaeze cannot evaluate a dashboard with an
empty queue. Ibrahim cannot rehearse the §11.5 runbook without an incident to rehearse against. It
is also the fastest route to a demonstrable product — the simulator is what makes the screenshots
real.

**The hard part.** Time. A 72-hour SLA cannot be observed in a demo, so the sandbox supports a
tenant-scoped clock offset, which is a dangerous capability that must be impossible in `live`.

**Guardrail.** Clock manipulation exists only for `test` tenants, is rejected at the model layer
rather than the view layer, and is asserted impossible in `live` by a test that is a blocking CI
gate.

---

## A20 — Data residency, BYOK and crypto-shredding

**What.** Per-tenant region pinning with no cross-region replication of case content; customer-managed
KMS keys for their tenant's data keys; and erasure implemented as destruction of the per-subject
data key rather than deletion of rows.

**Why it earns its place.** §2.2 lists data residency as a procurement blocker, and §11.7 is
admirably honest that deletion in an append-only system is genuinely difficult. Crypto-shredding is
the resolution of that tension: the record and its hash chain stay intact and verifiable, while the
content becomes permanently unrecoverable. The chain still proves what happened; the personal data
is gone.

**The hard part.** Key hierarchy design, and the consequence a customer must understand before
enabling BYOK: if they destroy or revoke their key, their data is gone and we cannot recover it.
That has to be stated at the point of enablement, not in a support ticket afterwards.

**Guardrail.** Crypto-shredding is irreversible and requires two-person authorisation. The shred
event itself is an audit record — which is exactly right, because the fact that data was erased on a
lawful request is itself something that must be provable.

---

## Considered and deliberately not included

| Idea | Why not |
|---|---|
| Live chat inside the widget | Already **Won't** in §3.3. It converts an asynchronous, evidenced process into a synchronous one with staffing implications the buyer has not budgeted for. |
| Full omnichannel helpdesk | The moment DisputeShield handles "where is my card?" it competes with Zendesk on Zendesk's terms and loses the regulated-dispute focus that is the entire argument. |
| Automated refund execution | Permanently out of scope. The credibility of an evidence system depends on it having no ability to move money. |
| Cross-tenant benchmarking | Tempting commercially, genuinely hard to make safe. With few tenants per category, "anonymised" peer medians are re-identifiable. Revisit only with formal differential privacy. |
| Native mobile agent app | The dashboard as an installable PWA with push covers the actual need — a breach warning at 22:00 — at a fraction of the cost. Folded into the dashboard work, not a separate product. |
