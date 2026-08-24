# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The PyPI package, the npm packages and the widget bundle are versioned together
and released from one tag.

## [Unreleased]

### Added

- **Scheduled monthly delivery** — `POST /v1/reports/schedules`, compliance-only,
  with an hourly runner (`disputeshield.reports.run_schedules`) and
  `disputeshield_run_report_schedules` for catching up by hand.
- **The runner asks what is owed, not what is due now.** A month stops being owed
  only when a delivery for it is confirmed `sent` — not when one is queued. Three
  properties fall out of that single decision: catch-up is free (a runner down for
  two months finds two months owed, because nothing recorded them as done), a
  schedule that queues twelve exports a year and delivers none cannot look
  healthy, and firing the runner twice cannot mail a period twice.
- **A closed month, in the schedule's own timezone.** The current month is never
  exported: a period still accepting cases produces a different document every
  time it is built, which would make the delivery's digest check refuse and the
  artefact worthless as a record. A firm's March does not start when UTC's does,
  so the month boundary is the schedule's, as a half-open interval — a closed one
  either double-counts the boundary instant or drops it, and for a monthly return
  that is a case reported twice or not at all.
- **`day_of_month` is capped at 28.** Days 29 to 31 do not exist in every month,
  and the usual workaround — sliding silently to the last day — makes a reporting
  deadline mean a different date in February. Refusing is clearer than guessing.
- Missed months go out **one at a time, in order**. Queueing two at once would
  deliver them out of order, and the second would read as a correction of the
  first.
- A month that cannot be delivered after three attempts is **recorded in
  `failed_periods`, audited, and stepped over**. Stepping over is deliberate —
  blocking every future month behind one stuck month turns a single bad period
  into a total, silent outage — and recording it is what keeps that from being a
  quiet skip. The API surfaces `failed_periods` rather than burying it.
- Deactivating every recipient **blocks rather than skips**: the month stays owed,
  a `report.schedule_blocked` audit record says why, and the runner logs an error.
  A schedule that looks active and delivers nothing is the failure this whole
  feature exists to prevent.
- `idempotency_key` gained an `attempt`, defaulting to 0 for every request a
  person makes. When a delivery parks because the period moved between request
  and send, the promise it carried is spent — it can never be satisfied, because
  the bundle it described no longer exists. The scheduler opens a *new* delivery
  with a fresh promise rather than editing the old one's, so the trail reads
  "attempt 0 refused because the period changed, attempt 1 delivered" instead of
  one row whose recorded promise quietly became something else.
- `disputeshield_doctor` gained a `report schedules` check: an active schedule
  overdue by more than two days means nothing is calling the runner. A deployment
  with the worker but no beat is a configuration a monthly report can hide in for
  a long time, and the first person to notice is a supervisor asking where the
  return is.
- **Email delivery of the regulatory export** — `POST /v1/reports/regulatory/email`,
  compliance-only. Producing a supervisory return by hand from a download was the
  manual step §6.5 exists to remove.
- **Recipients come from an allowlist, never from the request.** An endpoint that
  emails a whole period's disclosure to an address supplied by the caller is an
  exfiltration route with an OpenAPI entry. Addresses are registered first at
  `/v1/reports/recipients` — compliance-only, a stated reason required, audited
  against the acting agent — so the send has nothing interesting left to
  authorise. That was the point of separating them.
- One unknown address **refuses the whole request**, naming what it rejected. A
  partial send is a supervisor waiting for a report that four of five people
  received, and nobody noticing for a week.
- **Nothing is attached to the queue row** (ADR-0008). The payload holds the
  period and the digests the export had when it was requested; the dispatcher
  rebuilds the bundle and sends only if they still match. Case content never
  waits in a queue table, and a period that changed in between **fails loudly**
  into the outbox's existing retry and parking rather than delivering a bundle
  nobody promised. This is the first thing in the system that *depends* on
  byte-reproducibility rather than merely asserting it.
- Delivery is idempotent on (period, recipients), so a retried request during an
  incident cannot page a regulator's inbox twice.
- The email body restates each file's SHA-256 and the manifest signature, and
  tells the reader to check them against `GET /v1/reports/regulatory` — a
  recipient who trusts an attachment because the email said to has verified
  nothing.
- The audit trail records the request and the delivery **separately**: who asked
  for a period to be sent outside has an answer even when the send later fails,
  and `report.delivered` is written only after the send succeeded, so the trail
  never contains a false statement about a disclosure.
- Recipients are **deactivated, never deleted**. "Who could receive our disputes
  data in March" is a question a supervisor is entitled to ask.
- `disputeshield_seed_report_recipients` seeds sample recipients on a non-live
  tenant. Every address is under `example.test` or `.invalid` — domains RFC 2606
  and RFC 6761 reserve so they can never resolve — so a bug in delivery cannot
  reach a real inbox at the DNS layer rather than merely at the review layer. The
  command refuses a `live` tenant outright.
- `disputeshield_doctor` gained a **fatal** `report email delivery` check: an
  installation with registered recipients and a console or in-memory mail backend
  reports every send as successful and audits it as a disclosure that happened.
  It is the one failure on this path that puts a false statement in the audit
  trail, which is why it fails the preflight rather than warning.
- [`docs/runbook-report-delivery.md`](docs/runbook-report-delivery.md) for a
  queued export that did not arrive. Most of it is about telling a deliberate
  refusal apart from a failure, including the one refusal that means a defect
  rather than an operational event.
- `EMAIL_BACKEND` defaults to the console backend, and Django swaps it for the
  in-memory one under test. No address in this repository is ever written to.

- **`format=pdf` on the regulatory export (§7.3).** The specification has always
  listed `csv|pdf`; only the CSVs shipped. The PDF is the document a supervisor
  reads — cover attestation, period summary, the complaints table, then per-case
  history — while the CSVs stay what their systems ingest. It is built from the
  export that was already assembled rather than from a second query, so the
  readable document and the machine-readable one cannot describe two different
  reads of the period.
- The PDF carries the **same byte-reproducibility gate** as the rest of the
  bundle. PDFs make that harder than CSVs: a creation date, a modification date
  and a document ID are all regenerated per build. `invariant=1` pins the three,
  and page streams are left **uncompressed** on purpose — zlib's output is
  deterministic for a given build of zlib but not guaranteed across versions, so
  compressing would make reproducibility depend on which machine rendered the
  file. It also leaves the document greppable, which for an artefact a supervisor
  may examine is a feature.
- `report.pdf` is listed in the signed manifest alongside the CSVs, so the
  document is covered by the same signature and a tampered copy is detectable.
- The cover page states what the integrity attestation **does not** prove, and a
  failed chain appears on page one rather than being quietly omitted. A
  regulator-ready artefact that overclaims is worse than one that says nothing.
- There is deliberately **no way to build the bundle without the PDF**, tempting
  as that is for `format=json`, which never reads it. A period has exactly one
  manifest; omitting a file would sign a different list, and a supervisor
  comparing the manifest they fetched as JSON against the one inside the zip
  would find two signatures for the same period and no way to tell which is
  authoritative. Rendering a document nobody reads is the cheaper mistake.
- Long periods are **abridged, and say so**: per-case history is rendered for the
  first 200 cases, with an explicit statement that nothing was discarded and that
  the complete history is in `history.csv`.

### Fixed

- **A closed period did not export identically twice, once the PDF existed.** The
  cover page printed the tenant's live audit-chain head and its running record
  count — statements about the system *now*, rendered into digest-covered bytes —
  so re-exporting a finished period produced a different document whenever
  anything at all was written anywhere in the tenant. Every emailed report would
  have refused to send, including on the audit record that requesting the
  delivery writes for itself. The document now carries facts about the *period*
  (case count, the history records covering it, imported-case count); the live
  figures are published in `manifest.json` and at `GET /v1/audit/verify`, and the
  page says so rather than silently omitting them.
- **The reproducibility gate could not have caught that.** It built twice in a
  row, which is exactly the case where nothing has changed. It now builds either
  side of an unrelated audit write, and a second test asserts that a period which
  genuinely gains a case *does* produce a different document — so the gate cannot
  be satisfied by ignoring changes.
- **An unconfigured `report_email` channel would have silently no-opped.** The
  dispatcher falls back to the console channel for anything not configured, which
  for an SLA warning in development is a convenience; here it would mark a report
  `sent` and write an audit record saying a period left the building when nothing
  did. The report channel now resolves to a real implementation by default, and
  whether a message leaves the machine is `EMAIL_BACKEND`'s decision.
- **`?format=pdf` and `?format=csv` answered 404 before the view ran.** DRF
  reserves the `format` query parameter to select a renderer by name and raises
  `NotFound` for one it does not recognise, so two of the four formats §7.3
  documents were unreachable. `?format=json` appeared to work only because DRF
  happens to have a renderer by that name — the endpoint looked correct from the
  one format anybody had tried. `URL_FORMAT_OVERRIDE` is now disabled and the
  parameter belongs to the views that document it.
- An unrecognised `format` now answers **400** instead of falling through to the
  zip. A typo used to return a whole period's disclosure in a shape the caller
  never asked for, with nothing to indicate it.
- **The grounding gate could be satisfied by a coincidence.** An amount claim was
  compared by stripping every non-digit from the joined sources into one long
  string and searching it for the claim's digits, so a match could span two
  facts that have nothing to do with each other or with money. A draft promising
  ₦9,000 was judged supported because a case reference ran into a deadline's
  microseconds. Amounts are now compared as values, number by number. This is the
  gate that stops a reply inventing a refund, so a coincidence passing it is the
  worst way for it to fail.
- **An amount ending a sentence was read as a truncated prefix.** The claim
  pattern's trailing guard rejected a number followed by a full stop, so a weaker
  alternative matched and `₦9,000.` was extracted as the claim `₦9` — which
  almost any text appears to support. The guard now rejects a number that
  *continues* rather than a sentence that *ends*. Found because the strict
  value comparison above stopped hiding it.

## [2.0.0] — every amplifier delivered

Phases 0–12 of `docs/ROADMAP.md`. The specification shipped at v1.0; the twenty
capabilities in `docs/AMPLIFIERS.md` shipped across v1.1–v2.0, each with the
guardrail that made it shippable.

### Added — phase 12, enterprise and adoption (v2.0)

Amplifiers **A20** residency, BYOK and crypto-shredding, **A18** migration
tooling, **A19** sandbox and simulator.

- **Crypto-shredding** resolves the tension §11.7 is honest about: seven-year
  retention and a tested deletion procedure point in opposite directions, and an
  append-only system cannot delete. Content is sealed under a per-subject key; a
  shred destroys the key and **changes no row**, so the hash chain still verifies
  while the content is permanently unrecoverable. Both halves are asserted
  together, because either alone is worthless.
- Sealing is **opt-in per tenant**, and the consequence is stated rather than
  hidden: a tenant with sealing off can only be offered §11.7's refusal.
- A shred requires two different people, is refused outright while a legal hold
  covers the subject, and writes an audit record marked irreversible — the fact
  that data was erased on a lawful request is itself something that must be
  provable.
- BYOK: a revoked master key renders exactly that tenant's content unreadable and
  nobody else's, and we say so plainly rather than pretending to recover it.
- Per-tenant `region` pinning, and subject keys that never cross a tenant.
- CSV/Zendesk-shaped import with original timestamps. **Imported history stays
  distinguishable from native history, forever**: clocks are stopped on creation
  so a case closed in 2021 never acquires a deadline in 2026, every audit record
  carries `disputeshield_witnessed: false`, and the regulatory export gains an
  `origin` column plus an integrity note saying which rows we witnessed.
- `disputeshield_simulate` builds a sandbox tenant containing a **breach, a
  pause, a reopening and a mass incident** in one command — in 0.5s against the
  roadmap's 60-second gate. A demo of the happy path demonstrates a ticketing
  system.
- The sandbox clock offset is **refused at the model layer** for a live tenant,
  on `save()` as well as `clean()`, because the admin, a fixture, a data
  migration and a management command all reach the model without a form in
  between.

### Tests — 578 fast, 11 wall-clock gates, 92% coverage

### Added — phase 11, operating the operation (v1.5)

Amplifiers **A9** SLA simulator, **A15** QA sampling, **A14** outbound webhooks.

- The policy simulator replays a proposed window over real cases using each
  case's **own** immutable `SLAPolicyVersion`, its calendar as that version
  referenced it, and its recorded pause intervals read from `SLAEvent` — the
  evidence, not the clock's materialised view of it. ADR-0004 is what made the
  historical calendar recoverable at all, so this could not have been built
  before it.
- Its **self-check** is the gate: replaying an *unchanged* policy must reproduce
  the breach count that actually occurred. Anything else means the replay is not
  using history, and a confident wrong number is worse than no number.
- QA sampling: uniformly random selection using `secrets` (a sample an agent
  could predict is one they could prepare for), forced-review criteria as a
  **module constant** rather than configuration, an agent cannot review their own
  case, and an agent can respond to any score about their own work — a scorecard
  nobody may contest is a scorecard nobody trusts.
- A QA score is a record about the **review**, not the case. Filing an opinion
  into a case's own history would put an opinion where a regulator reads facts.
- Outbound webhooks: the §8.2 HMAC scheme with the timestamp inside the signed
  material, ordered per case so a `dispute.resolved` cannot overtake its
  `dispute.acknowledged`, at-least-once with a deterministic idempotency key, and
  **parked rather than dropped** after eight attempts across a day of backoff. A
  replay keeps its key, so a consumer that already processed it ignores it.
- The webhook payload serializer is **declared in the widget serializer module**,
  so the existing field-graph leakage gate walks it — the same test, not a
  parallel one. A second implementation of one guarantee is a second thing to get
  wrong.

### Found during phase 11

- **A read replica is a different connection, so row level security has no
  context there.** A context established on the primary is simply absent on the
  replica, and a query returns zero rows with nothing raised — a simulation would
  have reported "0 cases examined", which reads as reassurance rather than as
  failure. `set_tenant_context` and `db_tenant_context` now take a `using`
  argument, and `replica_reads()` is the only supported way to read from it.
- **`analytics.py` claimed it was "routed to the replica" and was not.** The
  docstring had said so since phase 6 while every query ran on the primary. Now
  it genuinely does, which cost the analytics tests their non-transactional
  speed — the alternative was a comment that survives review and means nothing.
- The `as_tenant` fixture borrowed an ambient transaction that transactional
  tests do not have, so it worked everywhere except the tests that exercise a
  second connection. It opens its own now.
- `DATABASES["replica"]["TEST"] = {"MIRROR": "default"}` — without it the test
  runner builds a second database for the alias and then forbids queries to it,
  making a correct read path untestable and creating pressure to stop using it.

### Tests — 552 fast, 9 wall-clock gates, 92% coverage

### Added — phase 10, intelligence that proposes and never disposes (v1.4)

Amplifiers **A11** triage, **A12** copilot, **A10** root-cause clustering,
**A13** repeat-claimant signals.

Every deliverable in this phase proposes. None disposes. The gates exist mostly
to keep it that way, because the commercial pull toward "just let it auto-resolve
the easy ones" arrives in this phase and the answer has to have been decided
before the pull does.

- `Suggestion` is a **separate model with no path to case fields**. §3.3 lists
  priority prediction under *Won't*, and a model writing a case field is how that
  exclusion gets quietly reversed. What the human chose is recorded beside what
  was proposed — and that difference only exists because the suggestion never
  became the case's own field.
- Every suggestion, acceptance and override is an audit record naming the model
  and its version. Without that, the accuracy metric is a number about nothing
  and a behaviour shift is unattributable.
- Accuracy is exported per tenant, and is **`None` rather than 1.0** before any
  decision: an untested model is not a perfect one.
- Triage proposes a category with the words that matched, and proposes **nothing**
  when the description carries no signal — a wrong category starts the wrong
  regulatory clock, so silence beats a confident guess.
- The copilot **blocks** an ungrounded draft rather than flagging it. A warning
  beside a draft is one an agent under queue pressure clicks past; a block is a
  thing they have to resolve. Retrieval reaches the case's own content, the
  tenant's templates and its resolved history — never an internal note, never
  another tenant, and never the model's own prior output.
- Clustering is a lens: inspectable membership, its evidence attached, and it
  discards a term appearing in almost every case because that term is describing
  the product rather than a cause.
- Risk signals are context with their evidence. They cannot reach an SLA policy,
  a priority, a channel gate or an outcome — and the service layer does not read
  them at all, so a rejection cannot cite one as its reason.

### Gates added — permanent

- **No model output writes to a case.** AST-walked across every intelligence
  module: no write to a case model, no call to a disposition (`transition`,
  `resolve`, `assign`, `pause`…), and no import of a service that could act.
- **No autonomous send on any channel**, asserted against the send path itself —
  so no configuration can enable one, because there is nothing to configure.
- **A signal cannot decide.** `signals.py` assigns no case attribute, `RiskSignal`
  has no field a future author could point at an outcome, and `service.py`
  contains no reference to signals at all.
- **Clustering writes only its own snapshot.**

### On the commitment boundary

The grounding check targets the three things a customer holds a firm to: *when*,
*how much*, and *that you will*. "We are looking into this" is grounded; "we will
keep you updated" is not, because "we will" is quotable back at the firm even when
what follows is mild. That line is stated in the test rather than left implicit,
since it is the judgement most likely to be revisited.

### Tests — 524 fast, 9 wall-clock gates, 91% coverage

The adversarial grounding suite is eight individual sentences somebody could
plausibly write, each asserted to be caught: an invented date, an invented
amount, "rest assured", "we guarantee", and a promise made true only by a related
word appearing nearby.

### Added — phase 9, the money side (v1.3)

Amplifiers **A5** representment packs, **A4** provider connectors, **A16**
financial exposure.

- **Two clocks, structurally independent.** The scheme's representment window is
  wall-clock, computed from the chargeback date, and marked `pausable=False` — so
  pausing the regulatory clock never moves it. A card scheme does not care that
  the firm is waiting on the customer, and it does not observe the firm's
  business hours. They breach and alert separately, asserted with a case where
  one is comfortable and the other is not.
- Reason codes as **data**, with a per-code evidence checklist. Schemes revise
  their requirements on their own schedule, and a mapping compiled into the
  application goes stale between releases — at which point a representment is
  refused for a missing element nobody knew had been added.
- A pack refuses to export while the checklist is unsatisfied. The expensive
  failure is an acquirer rejecting it *after* the window has closed.
- **DisputeShield builds and exports the pack; it does not submit it.**
  `submitted_by_disputeshield: false` is written into the audit record, not just
  the documentation, and the field that records a submission is named for the
  fintech that made it.
- Provider connectors: read-only by construction, opt-in per tenant,
  credentials envelope-encrypted with a per-tenant key. Every outbound call is
  recorded with the exact request made — a customer's security team asking "what
  did you ask our provider about me?" gets an answer from the record. A provider
  outage degrades the case to "context unavailable" and never blocks filing.
- `disputeshield/finance/` — value under dispute by category, age bands, expected
  loss from the tenant's **own measured uphold rate** (absent rather than guessed
  when there is no history), and reconciliation against settlement confirmations.
  The unreconciled delta is reported and signed: more paid than promised is its
  own finding, and netting it away would hide it.

### Gates added — both permanent

- **The connector interface exposes no write method.** Asserted by introspecting
  the abstract base class and every subclass, against a list of write-shaped
  verbs — so a connector written next year is covered the day it is written.
- **No code path from any module to money movement.** Walked from the AST: the
  finance package imports nothing that leaves the process, reaches no connector,
  and makes no money-shaped call; and no function anywhere in the package is
  named for moving money. §3.3 puts this under a permanent *Won't*, and the
  credibility of an evidence system depends on it having no ability to act on the
  thing it holds evidence about.

### Fixed — during phase 9

- **`_settle` in the intake router was renamed `_record_disposition`.** It meant
  "record what we decided about this message", and in a payments product that
  word means something else entirely. The money-movement gate flagged it, which
  is the gate doing its job on a naming problem rather than a behavioural one.

### Tests — 455 fast, 9 wall-clock gates, 91% coverage

### Added — phase 8, evidence that survives a lawyer (v1.2)

Amplifiers **A7** legal hold, **A8** chain anchoring, **A6** external escalation,
**A17** regulatory returns.

- `LegalHold` over a case, a customer, a category or a period. It suspends every
  retention and deletion process touching what it covers, including the
  data-subject erasure path — §11.7 promises seven-year retention *and* a tested
  deletion procedure, and the moment a case is in litigation those promises point
  in opposite directions.
- **Releasing a hold needs a second approver, and it must be a different person.**
  A two-person rule one person can satisfy twice is a one-person rule with extra
  steps.
- `ErasureRequest`: a refusal is a recorded outcome with the words the requester
  was given, stored verbatim in the audit trail. Refusing silently is its own
  violation, and a refusal a supervisor cannot read back is one we cannot defend.
- The retention sweep is **dry-run by default** and records every case it skipped
  on hold — a case still present after its window needs a reason in the record
  rather than looking like a sweep that missed it.
- RFC 3161 anchoring of chain checkpoints, with a pluggable authority.
  Unreachable means *pending*, not failed: writes continue, the backlog is
  exported as `unanchored_total`, and recovery anchors it in order.
- `GET /v1/audit/verify` now reports **three independent facts** — the chain is
  consistent, we computed that, and somebody outside this system attests the
  chain existed when we say it did. The development authority declares itself
  non-external, so a local install can never let the API claim an attestation it
  has not got.
- `ExternalEscalation` with its own reference, the body's own clock, and its
  correspondence kept on the case. **An open external track blocks closure in the
  state machine** — internal case closed while the external one is live is
  precisely what produces "the firm was unresponsive" in a supervisory finding.
- A determination that contradicts the internal outcome is **surfaced, not
  reconciled**. Rewriting ours to agree would destroy the most interesting
  evidence in the case.
- Versioned, data-driven `ReturnTemplate` with a **closed source registry**: a
  template specifies what to count and cannot reach anything nobody decided to
  publish. Returns are maker-checker, and the approved artefact's digest is
  hashed into the chain — what is provable later is not that a return was
  produced but that *this* one was approved.

### Found during phase 8

- **A foreign key into an append-only table cannot work.** Postgres enforces one
  by taking a `FOR KEY SHARE` lock on the referenced row, and a row lock requires
  UPDATE or DELETE privilege — exactly what migration 0014 revoked to make
  checkpoints append-only. Every anchor insert failed with "permission denied".
  `CheckpointAnchor` now holds a plain `checkpoint_id`; the integrity a foreign
  key would buy is already a property of a parent that is immutable and never
  deleted.

### Tests — 421 fast, 9 wall-clock gates

- `test_legal_hold.py` — every hold scope, the two-person release, the erasure
  refusal for both reasons (hold and retention), and that releasing a hold
  re-enters the case into the retention schedule.
- `test_anchoring_and_escalation.py` — an unreachable authority does not block
  filing, the backlog is a metric, recovery anchors it, and a local authority
  never claims an external attestation. Plus: a return regenerates
  byte-identically under a newer template revision, and generation has no path to
  an approved status.

### Added — phase 7, every complaint lands in the clock (v1.1)

Amplifiers **A1** omnichannel intake, **A2** deflection, **A3** mass-incident mode.

- Six inbound channels — email, WhatsApp, USSD, phone call logs, social DMs and
  web forms — each with its own adapter and one shape afterwards. §2.1's actual
  problem is that complaints arrive through six channels and end up in a shared
  inbox; the widget solved one of them.
- `DisputeContact`: the identity a case may receive messages from, per channel.
  **Channel identity never grants case access on its own** — a message from
  anyone else is quarantined for a human to attribute, never appended and never
  echoed back into the thread.
- Thread matching on the email thread root rather than the subject line, on the
  envelope `From` rather than the display name, and on a normalised phone number
  so `+234 801 234 5678` and `2348012345678` are one customer.
- Auto-replies and bounces are ignored rather than appended. Treating an
  out-of-office as a customer's response would resume a paused clock on the
  strength of a mail server's holiday message.
- `router.attribute` — a human resolving a quarantine also verifies the sender,
  so the next message in that thread lands without a human. A review queue should
  shrink as it is worked.
- Deflection: declared incidents, narrow matchers, and a "notify me"
  subscription. `file_anyway` is a **module constant**, not a setting, not a
  column and not a serializer field — a boolean a tenant can set to False during
  an outage is one that will be set to False during an outage, and complaint
  suppression is the worst accusation a regulator can make about a complaints
  system.
- `deflections_total` from the audit trail rather than a counter, and rendered
  beside case volume in the analytics summary — a feature that reduces recorded
  complaints has to be the most instrumented thing in the product.
- `MassEvent` with per-case membership: one investigation, one finding, and a
  fan-out that writes each case individually. Membership is closed rather than
  deleted, because that a case was once grouped with four thousand others is part
  of how it was handled.
- `POST /v1/intake/{channel}` and `POST /v1/widget/deflection`.

### Fixed — during phase 7

- **A case's clock-start event was attributed to a subject that never existed.**
  `file_dispute` created the clock with a placeholder `subject_id` and corrected
  it a moment later, so `sla.started` pointed at `"pending"` — leaving every
  case's history missing the event that began its clock, and the regulatory
  export short one row per case. The identifier is now generated before the
  clock, so the first audit record names the case. Caught by the parameterised
  per-channel suite asserting an identical audit shape.
- **The 5,000-case gate was inspecting only the last 9,000 statements.** Django
  caps the query log and warns; the deque is sized at connection setup, so
  raising `queries_limit` alone does nothing. A bulk `UPDATE` in the first batch
  would have passed a gate written to forbid it.

### Tests — 384 Python

- `test_intake.py` — the per-channel suite is parameterised over every inbound
  channel and asserts an identical clock, an identical audit shape, identical
  isolation and a hashed identity. The quarantine cases are individual: a
  different address, a **spoofed display name**, a **rewritten `Reply-To`**, and
  a quoted case reference — which appears in every email we send and must not act
  like a secret.
- `test_deflection_and_mass_events.py` — the file-anyway guardrail is asserted
  against the *shape of the code*: not a settings key, not a model field, a
  module constant.
- `test_mass_event_at_scale.py` — 5,000 cases resolve as 5,000 individual audit
  records with zero bulk `UPDATE`s, asserted by reading the SQL, and the chain
  still verifies afterwards.

## [1.0.0] — the specification, delivered

Phases 0–6 of `docs/ROADMAP.md`. Everything §13 claims survives follow-up
questions is built and gated: the sandboxed iframe, the server-minted
customer-scoped session token, the pausable-clock-with-mandatory-reason, the
dead-man's switch, and `disputeshield_doctor` verifying the immutability trigger
actually installed.

### Added — phase 6, analytics, export and packaging

- Signed audit checkpoints. A failed verification still produces a checkpoint
  marked unverified — silence after a failed check is indistinguishable from the
  job not having run, and §11.4 pages on exactly that condition.
- `GET /v1/audit/verify`, published so a customer's auditor can check the claim
  independently. A proof only we can run is a promise, not a proof.
- The regulatory export (§6.5): a zip of `cases.csv`, `history.csv` and a signed
  `manifest.json`. **Byte-reproducible** — total ordering, fixed line
  terminators, pinned archive timestamps, integer minor units and no float
  anywhere. A supervisor who asks for the same period twice and gets two
  different files has a reason to doubt the rest of the bundle.
- The export reports a broken chain rather than hiding it. Producing a
  clean-looking bundle from a tampered history is the worst thing this feature
  could do, so there is a test for it.
- Breach analysis by category, agent and **cause**, with pause duration reported
  beside breaches rather than on its own screen — §4.4's argument is that a
  pausable clock is abusable, and the abuse is only visible in the comparison.
  Undocumented breaches surface as their own group, which is §11.5 step 5's
  annotation earning its keep.
- `disputeshield_init` now seeds real categories, a business calendar and five
  SLA policies with their regulatory references.
- Deployment artefacts: production compose, a Helm chart, a Terraform module.
  The chart **hard-codes one beat replica** and uses a `Recreate` strategy — a
  chart that exposes the number is one where somebody raises it during an
  incident to clear a backlog, producing the duplicate breach pages they were
  trying to avoid. Terraform pins the audit bucket to object lock in COMPLIANCE
  mode, because GOVERNANCE mode can be overridden by exactly the principal an
  evidence store has to survive.
- `docs/openapi.yaml` complete: 23 paths, three authentication schemes, and the
  404-not-403 and mandatory-idempotency rules stated once at the top.

### Fixed — during phase 6

- **The sweep missed §11.9's budget: 75.8s for 10,000 due deadlines against 60s.**
  Claiming and firing were separate transactions, which made `SKIP LOCKED`
  almost decorative — the claim's locks were released at its own commit, so every
  row was re-locked a moment later — and each firing took its own
  advisory-locked audit append. Now one transaction per tranche, bulk writes, and
  `audit.append_batch` paying for the chain lock once instead of ten thousand
  times. ADR-0003 anticipated this batching; the load gate is what forced it.
- **`SET LOCAL` outside a transaction is discarded, silently.** Postgres warns and
  continues, so the variable is never set, RLS matches nothing, and every query
  returns zero rows with nothing raised. This was the *third* appearance of that
  shape — the SLA sweep, the attachment download, and the packaged-install smoke
  test — so `set_tenant_context` now raises `NoTransaction` instead of leaving it
  to each caller to remember. `for_each_tenant` opens one transaction per tenant,
  so a failure on the eleventh does not roll back the ten before it.
- **A tenant-bearing model shipped with an unscoped default manager.**
  `AuditCheckpoint` was written as a plain model on the reasoning that a platform
  job reads it; the registry-walk test in `tests/test_tenant_isolation.py` failed
  it immediately, which is what that test is for.
- **Permissions were evaluated before the acting agent was resolved.** A role
  admitting a bare API key passed the first check and was corrected by a second;
  a compliance permission raised on the first and never reached the correction —
  so a compliance officer was judged as the key they authenticated with. Now
  resolved in `perform_authentication`, and the double-check is gone.

### Gates added

- **The packaging gate.** Builds a wheel, installs it into a bare Django project
  in its own virtualenv, asserts the import came from `site-packages` rather than
  the working tree, then runs `migrate` → `disputeshield_init` →
  `disputeshield_doctor` → files a dispute → verifies the chain. §6.2 is a
  distribution promise and this is what makes it testable as one.
- The sweep load gate, and a second assertion that an *empty* sweep over 10,000
  open clocks is still cheap — which is ADR-0007's actual claim: cost tracks
  events due, not clocks open.

### Added — phase 5, attachments, templates and context

- Content inspection (`disputeshield/attachments/inspection.py`): a magic-byte
  allowlist of PDF, PNG, JPEG and GIF, a 10 MB cap, and rejection of polyglots,
  PDFs carrying JavaScript or an automatic action, and archive bombs. The
  filename is never consulted — `statement.pdf` is a claim made by whoever
  uploaded it.
- Private storage with content-addressed keys that contain nothing the uploader
  supplied, so the path is neither guessable nor a traversal surface.
- **Nothing is retrievable until it is clean** — including by whoever uploaded
  it. An uploader who can fetch their own file back before the scan finishes has
  a working file-hosting endpoint on a fintech's domain, and the malware never
  needs to reach an agent to be useful.
- Signed, expiring download URLs. The signature covers the attachment, its
  tenant and the expiry, so a link that leaks into a chat log goes stale and one
  tenant's link cannot be replayed against another's.
- Downloads are served as a fixed `application/octet-stream` with
  `Content-Disposition: attachment`, `nosniff` and a deny-everything CSP — never
  as their own content type.
- A pluggable AV scanner. The default is `NullScanner`, which marks files
  `failed` rather than `clean`: an installation that never configured a scanner
  gets invisible attachments, not unscanned ones served to agents.
- Response templates with a **substitution engine, not a template language**. A
  compliance officer edits these in a dashboard, and in a real engine
  `{{ dispute.tenant.api_keys.first.key_hash }}` renders. A fixed variable
  allowlist, no attribute access, no filters, and an unknown name renders its
  placeholder rather than an empty string — an empty string is how a customer
  receives "Dear ,".
- `POST /v1/disputes/{id}/context` (§7.3). Pushed by the host application, never
  pulled: an endpoint that reached back for data would retire §7.1's claim that
  DisputeShield holds no standing access to the customer's database.
- The outbox dispatcher: at-least-once with backoff, parking after six attempts
  rather than dropping. A breach alert that vanishes is one nobody received and
  nobody can prove was owed.
- `disputeshield/tenancy/platform.py` — `for_each_tenant`, the only supported way
  to write work that spans tenants.

### Fixed — during phase 5

- **The SLA sweep would have fired nothing in production.** It queried across
  tenants directly, and row level security is FORCEd — so a query with no tenant
  context returns *zero rows*, not every row. Every test passed because the
  fixtures held a tenant context open around their `yield`; Celery has none to
  inherit. The heartbeat would have stayed fresh and §11.5's runbook would never
  have triggered, because the scheduler was healthy and the queries were empty.
  The notification dispatcher and the deadline reconciler had the same defect —
  the reconciler would have reported a clean bill of health for a database it
  never read. All three now iterate tenants explicitly, and
  `tests/test_platform_scope.py` asserts the context is `None` before each test
  begins.
- **The attachment download could not read its own row.** Same cause, found the
  same day: the view is unauthenticated by design, so there was no tenant context
  for RLS. The tenant now travels in the URL *and* in the signature, so it can be
  established from a value we signed rather than one the caller chose.
- The queue's p95 gate now measures twice and fails only if both attempts exceed
  the budget. A wall-clock budget on a shared machine picks up whatever else is
  running, and a gate that cries wolf is one people re-run until it passes.

### Tests — 281 Python, 19 loader, 13 browser

- `test_attachment_inspection.py` — the GIF/HTML polyglot, an SVG hidden in a
  PNG, a case-varied `<ScRiPt>`, a PDF with `/JavaScript`, a PDF with
  `/OpenAction`, an ELF renamed `.pdf`, and a zip bomb.
- `test_attachments.py` — the scan gate asserted for pending, infected and
  unconfigured-scanner states; signature tampering, expiry, cross-attachment and
  cross-tenant replay; and that a pending file is byte-identical to a missing one
  in the response, because "not scanned yet" tells an uploader when to retry.
- `test_templates_and_context.py` — the template context is asserted to expose
  nothing internal, marked as a leakage gate because it protects the same
  guarantee as the widget serializer.
- `test_platform_scope.py` — the regression guard described above, including a
  grep that fails any future background module querying across tenants without
  iterating them.

### Added — phase 4, the widget and its boundary

- `loader/` — **1,035 bytes gzipped**, a quarter of ADR-0001's 4 KB budget. It
  creates the sandboxed cross-origin iframe and does nothing else. No fetch, no
  cookie access, no DOM queries into the host page — asserted by a test that
  greps its own source, because the budget protects reviewability and the grep
  protects the claim.
- The `postMessage` protocol, symmetric and validated on both sides: fixed
  envelope, protocol version, an allowlist per direction, origin **and**
  `event.source` checked, and never `'*'` as a target origin.
- Session tokens (§4.3, ADR-0002): opaque, Redis-backed, hashed at rest, scoped
  to exactly one customer, revocable one session at a time, per customer, or per
  minting key. The last is the response to a leaked secret key and is available
  immediately rather than after a rotation completes.
- **The token is handed to the widget over `postMessage`, never in the iframe
  URL** (§10). It is sent only after the widget announces it is listening, only
  to the widget's own origin, and only once.
- `POST /v1/sessions`, the widget API of §7.2, and the transaction picker fed
  from the list the fintech supplied at mint time — so a customer can only
  dispute their own transactions, enforced rather than assumed.
- Publishable keys as a distinct kind with a distinct `pk_` prefix and a distinct
  principal class, so one cannot satisfy a permission written for the other by
  accident.
- `AllowedOrigin` with validation that refuses a path, a wildcard or `null` — a
  path is the dangerous one, because `frame-ancestors` ignores it and the tenant
  believes they restricted a page when they authorised a host.
- `GET /v1/embed` (D9): dynamic, per-tenant CSP, privately cached for a minute,
  referencing bundles that are static and cached for a year. A load from an
  unregistered origin is refused **and recorded**, because §11.6 says that is the
  most common widget support ticket by a wide margin.
- The React widget: one decision per screen, the expected resolution date shown
  before submission, and focus moved to each step's heading so a screen reader
  announces it.
- `/healthz` and `/readyz`. Readiness includes the audit immutability trigger —
  a deployment that can accept writes but cannot make them immutable should not
  be taking traffic.

### Fixed — during phase 4

- **`role="radiogroup"` on a `<ul>` stripped its list semantics**, orphaning the
  `<li>` children. Found by axe-core, not by review.
- **A customer's own message could not be audited.** `add_message` passed
  `customer` as an actor type and the audit trail accepted only
  system/user/api_key. Recording a customer's words as `api_key` would attribute
  them to the fintech's integration, so `customer` is now a first-class actor
  identified by the pseudonymous hash the case already carries.
- **The embed document answered an unknown key with a JSON 401.** That body is
  what would render inside a customer-facing page on a fintech's site. It now
  fails closed *and quietly* — empty 403, deny-everything CSP — via an
  authenticator used on that one surface and nowhere else.
- **The widget's inbound and outbound message allowlists were the same set**, so
  a type added for one direction became valid in the other.
- **The CSP's `connect-src` was built from the request's `Host` header.** Django
  narrows `Host` via `ALLOWED_HOSTS`, so it was not exploitable — but it made a
  security header depend on an attacker-supplied one, and the next person to
  relax `ALLOWED_HOSTS` for a health check would not connect the two. It is now
  configuration, falling back to the request only under `DEBUG` and to `'none'`
  in production. Found by semgrep, via a Flask rule that does not apply to Django
  and pointed at something real anyway.
- **The direct-write grep gate flagged reads.** `Dispute.objects.filter` in a
  view is a scoped queryset, not a bypassed audit trail; the matcher now targets
  write methods only.

### Tests — 213 Python, 19 loader, 13 browser

- `tests/isolation.spec.js` — Playwright, two real origins, a deliberately
  hostile host page holding a fake account number in a form field, on `window`,
  in a cookie and in `localStorage`. Asserted in both directions: the host cannot
  read the iframe's document or globals; the widget cannot reach
  `window.parent.HOST_SECRET`, the host's DOM, its cookies or its storage.
- `tests/keyboard.spec.js` — the **complete** filing flow driven by Tab, Enter,
  Space and typing only, ending at a filed case with a reference. §9 makes this a
  regulatory obligation, and the iframe boundary is what makes it hard.
- `tests/a11y.spec.js` — axe-core at **every** step, not just the first. The
  screens a customer reaches after a decision are the ones nobody looks at.
- `tests/test_widget_api.py` — the publishable key is asserted to reach no data
  route, parameterised over every route rather than sampling one; a session token
  cannot cross customers or tenants; a transaction reference outside the session
  is refused.
- `tests/test_widget_embed.py` — per-tenant `frame-ancestors`, no
  `unsafe-inline`, never publicly cacheable, and the document contains no inline
  script its own CSP would block.

### Added — phase 3, the management API and the case lifecycle

- `Dispute` and `DisputeMessage`. Messages are immutable with no edit path — a
  correction is a new message, for the same reason the audit trail appends: a
  conversation that can be edited afterwards is not evidence of what was said.
- §3.4's state machine as a **table** (`disputeshield/disputes/states.py`), so the
  tests enumerate it. A transition added in a later phase is automatically covered
  by the assertion that every transition records actor, reason and the SLA clock
  state at that instant, rather than only if somebody wrote a test beside it.
- `disputeshield.disputes.service` — the only supported way to write a case. The
  API calls it, the admin will call it (D10), commands call it. That is what lets
  the audit trail be complete without qualification.
- Management API (§7.3): cursor-paginated queue sorted by urgency with breached
  cases pinned, filters by status/category/assignee/amount/risk, and actions for
  transition, pause, resume, resolve, assign, messages and SLA.
- Two unrelated serializer families. Widget and management share **no base class**
  — inheritance is how a field added for agents silently appears in a customer's
  response.
- API key authentication (Argon2id), the §6.5 role model, and an acting-agent
  header so the audit trail names the person rather than the key.
- D8's 404-not-403 exception handler, project-wide rather than per-view.
- Idempotency on every write, stored rather than cached, with a request
  fingerprint so reusing a key with a different body is a 409 instead of silently
  returning the first response and hiding a client bug.

### Fixed — during phase 3

- **The role permission classes did not require authentication.** Setting
  `permission_classes` on a view *replaces* `IsAuthenticated` rather than adding
  to it, so an anonymous request passed the permission check and reached the
  queryset. Nothing leaked — the scoped manager raised — but the last layer was
  doing the first layer's job, and the caller got a 500 instead of a 401.
- **The tenant contextvar outlived its request.** Authentication set it and
  nothing reset it; worker threads are reused, so the next request began with the
  previous request's tenant in scope, and an anonymous request never overwrote it.
  `TenantContextMiddleware` now owns the lifetime and resets in a `finally`.
  Found by test-ordering pollution — which is exactly how it would have been found
  in production.
- **RLS on the API key table made authentication impossible.** The lookup happens
  before a tenant context exists, so the blanket policy from migration 0003
  returned zero rows and every request answered 401. The model docstring already
  said the lookup could not be tenant-scoped; the migration contradicted it. The
  policy is now split by command: SELECT unscoped (the row holds a prefix and an
  Argon2id hash), writes still tenant-scoped.
- **The queue serializer computed business-time remaining per row.** That walk
  needs the calendar, the pause intervals and a deadline row for every case, so a
  page of fifty cost fifty calendar walks plus an N+1. It passed in isolation and
  failed in a full run — the worst way for a performance defect to behave. The
  list now reports the denormalised deadline and breach flags, which is what the
  urgency sort and the breach pinning actually read; business-time remaining moved
  to the detail view, where it is one case rather than fifty. The whole suite went
  from 169s to 37s as a side effect.
- **Idempotency records could not store their own response.** The SLA block began
  returning datetimes, which a `JSONField` cannot hold — so the write failed on
  the *original* request, for a feature that exists only to make retries safe.
  Responses are now stored as rendered JSON, which is also the only way a replay
  can return what the client actually received.
- **The queue performance gate was measuring one row.** The load fixture reused
  the seed case's clock, which is a OneToOne, so every bulk insert violated the
  constraint and `ignore_conflicts=True` swallowed all 9,999 of them. The fixture
  now builds a clock per case and asserts the row count before measuring — a
  performance gate that silently measures nothing is worse than no gate, because
  it reports success.

### Tests — 172 passing, 91% coverage

- `test_serializer_leakage.py` — walks the widget serializers' **full field
  graph**, checks `source` aliases, asserts the widget and management serializers
  share no base class, and enumerates the module so a new widget serializer added
  without coverage fails the build.
- `test_no_mutation_routes.py` — walks the **resolved URLconf** asserting no route
  binds PUT/PATCH/DELETE and no `ModelViewSet` exists, then greps the API package
  for direct ORM writes to auditable models.
- `test_dispute_transitions.py` — drives every entry in the transition table.
- `test_management_api.py` — authentication (including that an unknown prefix and
  a wrong secret are byte-identical responses), the queue's urgency ordering and
  breach pinning, cursor pagination, idempotent replay, 409 on key reuse, and
  role separation.
- `test_queue_performance.py` — p95 under 300 ms at 10,000 open cases, plus a
  query-plan assertion so the budget is not met by a sequential scan that stops
  being fast at the next order of magnitude.
- Cross-tenant and cross-customer isolation extended to the HTTP layer: 404, never
  403, on both reads and writes.

### Added — phase 2, the SLA engine

- `compute_deadline` and `business_time_between` (`disputeshield/sla/deadlines.py`)
  — pure, Django-free, and written as each other's check rather than sharing an
  implementation, so their agreement is evidence instead of tautology.
- `BusinessCalendar` as a plain value object: weekday windows, local-date
  holidays, continuous (24/7) mode, and a search horizon that raises
  `ImpossibleCalendar` rather than looping forever on a calendar that is never
  open — a hung sweep is a stopped compliance clock for every tenant on the worker.
- `elapsed_fraction` — warning thresholds are percentages of *business* time. A
  case filed Friday afternoon is not 60% consumed by Sunday.
- Models: `BusinessCalendar`, `BusinessHoursWindow`, `Holiday`, `SLAPolicy`,
  `SLAPolicyVersion` (immutable, ADR-0004), `SLAClock`, `SLAEvent`, `SLADeadline`,
  `NotificationOutbox`, `SweepHeartbeat`. RLS enabled and **FORCEd** on all of them.
- Clock lifecycle: `start`, `pause`, `resume`, `stop`. Pause and resume require a
  reason at the service layer **and** at the database layer — a `CheckConstraint`
  makes a reasonless pause event unrepresentable, because a reason enforced only
  in a service is one refactor from optional.
- Every clock event records `clock_remaining_seconds` at the moment it happened.
  That field is what makes a breach explainable six months later.
- The watermark-driven sweep (ADR-0007): claims due deadlines with `SKIP LOCKED`,
  writes the outbox row in the same transaction that marks the deadline fired,
  under an idempotency key derived from *what* the notification is about rather
  than *when* it was generated. That is what makes §11.5's catch-up step safe to
  run during an incident instead of causing a second one.
- `SweepHeartbeat`, written even on a quiet sweep — "nothing was due" and "the
  scheduler is dead" look identical from outside, and only one is an incident.
- `disputeshield_sweep` management command with `--catch-up`, `--to` and
  `--dry-run`, so §11.5 step 4 is an executed procedure rather than prose.
- Nightly reconciliation of materialised deadlines against the pure function.
  Divergence is **reported, never repaired** — a mismatch may be a bug or an owed
  backfill, and rewriting the row destroys the evidence needed to tell which.
- `disputeshield_doctor` gained a heartbeat check. Non-fatal by design: refusing
  to serve because the scheduler stalled would turn a silent compliance outage
  into a loud availability one, and take down the dashboard showing which cases
  are affected.

### Fixed — during phase 2

- **An open pause interval was measured to `now()` rather than to the instant
  being evaluated.** A clock evaluated at the moment it was paused counted its own
  pause as already elapsed and reported zero time remaining — wrong, and exactly
  backwards. `paused_intervals_of` now takes the instant under evaluation.
- **`disputeshield.sla.sweep` was both a module and an exported function**, so the
  package export shadowed the module and `from disputeshield.sla import sweep`
  yielded whichever was imported last. The module is now `sweeper`.

### Tests — 121 passing, 93% coverage

- `test_sla_deadlines.py` — the matrix: weekends, holidays as *local* dates, DST
  in both directions, windows starting before opening and after closing, windows
  shorter than a business day, multiple pauses, a pause spanning a holiday and one
  spanning a weekend, overlapping pauses merged rather than double-subtracted.
  Runs a second time under `TZ=Pacific/Kiritimati`.
- Two Hypothesis properties, run at **10,000 examples** in CI with no falsifying
  case: the round trip (a computed deadline contains exactly the business time
  requested) and monotonicity (a longer window never yields an earlier deadline).
- `test_sla_clock.py` — pause discipline asserted four ways, including by
  introspecting the service signature so a future overload with a defaulted
  `reason` fails the build.
- `test_sla_sweep.py` — firing, idempotency, catch-up, late-detection recording,
  the heartbeat's 3-minute alert budget, reconciliation, and the runbook's shell
  command run twice to prove it pages nobody twice.

### Added — phase 1, tenancy and the immutable audit trail

- `Tenant`, `Agent`, `APIKey` and `AuditRecord`, with `on_delete=PROTECT`
  throughout (ADR-0006) and random prefixed identifiers rather than sequential
  keys, so §10's answer to ID enumeration holds before the 404 is reached.
- `TenantScopedManager`: the default manager on every tenant-scoped model
  **raises** without a tenant context. `all_tenants()` is the only unscoped path
  and is named so that using it is a decision rather than an inherited default.
- Postgres row level security on every tenant-scoped table, **FORCEd** so that it
  applies to the table owner too — plain `ENABLE` exempts the owner, and in every
  self-hosted compose install the application role *is* the owner, which would
  leave the third isolation layer looking installed and doing nothing.
- Audit immutability enforced by the database: `BEFORE UPDATE OR DELETE` trigger,
  a separate statement-level `BEFORE TRUNCATE` trigger, and `UPDATE`/`DELETE`/
  `TRUNCATE` revoked from the application role.
- Hash-chained audit records appended synchronously under a per-tenant advisory
  lock (ADR-0003), inside the same transaction as the domain write.
- `audit.correct()` — the only correction mechanism. Appends a compensating
  record; the original stays.
- `audit.verify_tenant()` — walks a tenant's chain and reports content
  alteration, broken links and sequence gaps.
- `db_tenant_context` — restores the previous RLS scope on exit rather than
  leaving the last tenant's scope in place for the rest of the transaction.
- `disputeshield_doctor` now passes and fails for real, verified by a test that
  reverts the immutability migration and asserts the command refuses to proceed.

### Fixed — during phase 1

- **Chain verification did not deliver §8.3's claim.** Recomputing each record
  against its own stored `prev_hash` flagged only an edited record, leaving the
  rest of the chain apparently healthy. Verification now recomputes against the
  expected predecessor and carries it forward, so an edit invalidates every
  record after it. Amendment recorded in ADR-0003.
- **`SET LOCAL` is scoped to the transaction, not to the block that set it.** A
  transaction touching more than one tenant kept the last one's scope for
  everything that followed. Amendment recorded in ADR-0005.

### Tests — 53, all passing

- `test_tenant_isolation.py` — each of the three isolation layers asserted
  independently, including a registry walk that fails any future model given an
  unscoped default manager, and a demonstration that `all_tenants()` escapes
  layer 2 but not layer 3.
- `test_immutability.py` — every assertion bypasses the ORM. `UPDATE`, `DELETE`
  and `TRUNCATE` are each shown to raise in Postgres.
- `test_audit_chain.py` — tampering invalidates the edited record **and every
  record after it**; a deleted record shows as a sequence gap.
- `test_audit_concurrency.py` — eight concurrent writers, forty appends, one
  unbroken chain with contiguous sequences (ADR-0003).
- `test_doctor_detects_missing_trigger.py` — the doctor is shown to fail, because
  a preflight nobody has watched fail is a preflight nobody should trust.
- `test_foundations.py` — identifiers, key lifecycle, context restoration,
  middleware, and the §10.2 startup invariants.

### Changed

- CI now asserts what exists at the current phase. The frontend and image jobs are
  gated on their artefacts existing and light up in phases 4 and 6 — a job that
  asserts something the code cannot yet do is a red build that teaches people to
  ignore red builds, and one that passes vacuously is worse because it looks like
  coverage.
- Container image vulnerability gating split in two: HIGH and CRITICAL findings
  **with an available fix** block the build, while the full report including
  unfixed findings is published as an artefact. A gate that fails on
  vulnerabilities with no patch anywhere can never go green, and the only thing
  it reliably produces is a team that has learned to bypass it.
- The image applies `apt-get upgrade` at build time, so a Debian package fixed
  upstream cannot sit unapplied while §8.5's published remediation SLAs run down.
- `ruff format` no longer rewrites Python code blocks inside Markdown. The
  specification, the ADRs and the README are source documents whose examples are
  written for a reader.
- Local host ports moved to a dedicated block (Postgres 55433, PgBouncer 56432,
  Redis 56380/56381, Mailpit 8026) so DisputeShield coexists with the sibling
  projects on one machine.

### Added — phase 0, foundations

- Repository scaffold: the installable Django app (`disputeshield/`), the
  standalone server project (`server/`), and placeholders for the widget, loader,
  dashboard, SDKs and deployment artefacts.
- `pyproject.toml` targeting Python 3.12 with ruff, pytest, coverage at 85%,
  `bandit`, `pip-audit` and `gitleaks`.
- `Makefile` whose targets are exactly the ones CI runs.
- `compose.yaml` with Postgres 16, **two** Redis instances (§11.1), PgBouncer in
  transaction-pooling mode for the isolation suite (ADR-0005), and Mailpit.
- `.github/workflows/ci.yml` — five jobs, with the blocking gates split into
  their own job so a failure is unmissable rather than buried in a test run.
- `disputeshield_doctor` with its first four checks: audit immutability trigger,
  audit table grants, RLS applicability of the connecting role, and clock skew.
- Production invariant assertions in `disputeshield/conf.py`, enforced from
  `AppConfig.ready()` — the app refuses to start with `DEBUG=True`,
  `ALLOWED_HOSTS=['*']`, insecure cookies or `ATOMIC_REQUESTS` disabled.
- `TenantContextMiddleware`, establishing RLS context with `SET LOCAL`.
- `scripts/no-dangerous-html.sh` and `scripts/check-loader-size.sh` as CI gates.
- `scripts/hello-world.sh` with a CI-asserted step count.

### Documentation

- `docs/product-specification.md` — the complete product specification.
- `docs/plan-architecture.md` — twelve decisions the specification left open,
  each with its consequence, plus four deferred questions with the trigger that
  reopens them.
- `docs/ROADMAP.md` — thirteen phases with machine-checkable exit gates.
- `docs/AMPLIFIERS.md` — twenty capabilities beyond v1.0, each with a guardrail.
- `docs/runbook-sla-sweep.md` — the most important runbook in the product.
- ADR-0001 sandboxed iframe · ADR-0002 opaque session tokens · ADR-0003
  synchronous audit chain · ADR-0004 versioned SLA policies · ADR-0005 RLS under
  transaction pooling · ADR-0006 `PROTECT` not `CASCADE` · ADR-0007 materialised
  deadlines.
