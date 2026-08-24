# Runbook — an emailed regulatory export did not arrive

A compliance officer queued a period for delivery and the recipient has nothing.
Unlike most delivery problems, the likeliest causes here are **deliberate
refusals**, not failures — this path is built to stop rather than to send
something wrong, and each refusal has a specific meaning.

Start by finding the row. It is in the notification outbox, one per (period,
recipients) pair:

```bash
python manage.py shell -c "from disputeshield.models import NotificationOutbox; print(*NotificationOutbox.objects.all_tenants().filter(event_type='report.regulatory').values('id','status','attempts','last_error'), sep=chr(10))"
```

Run that inside a tenant context, or it returns nothing — see the note in
`CLAUDE.md` about querysets and RLS.

## 1. `status = pending`, `last_error` contains "no longer matches"

**This is the system working.** The period changed between the request and the
send, so the bundle no longer matches the digests the requester was promised, and
`deliver()` refused rather than sending something nobody asked for (ADR-0008).

The error names which files differ.

- `cases.csv` or `history.csv` differ → a case in the period was filed, changed or
  resolved after the request. Expected on a recent or open-ended period. Cancel
  the delivery and re-request; the new request captures the current state.
- **Only `report.pdf` differs** → this should not happen, and it is a defect, not
  an operational event. The document is supposed to contain facts about the
  period, not about the system now. It regressed once already, when the cover
  page printed the tenant's live chain head and running record count. Check what
  was recently added to `_cover()` in `disputeshield/reports/pdf.py`, and confirm
  `tests/test_pdf_report.py::TestByteReproducibility` still builds either side of
  an unrelated audit write.

Deliveries retry with backoff and park after the outbox's maximum attempts. A
parked delivery for a period that is still moving will never succeed; re-request
it against a closed period instead.

## 2. `status = pending`, `last_error` names a provider error

Ordinary transport failure. It retries on the outbox's schedule. If it parks,
fix the provider and replay the row rather than requeuing — the idempotency key
is derived from (period, recipients), so a fresh request for the same period to
the same people **returns the existing row** instead of creating a second one.
That is deliberate: a retried request during an incident must not page a
regulator's inbox twice.

## 3. `status = sent`, and the recipient still has nothing

Check the mail backend first:

```bash
python manage.py disputeshield_doctor
```

`report email delivery` fails when addresses are registered and `EMAIL_BACKEND`
is the console or in-memory backend. In that state the send *succeeds*, the
delivery is audited as having happened, and the report exists only in a log file.
It is the one failure in this path that produces a false statement in the audit
trail, which is why it is a fatal doctor check rather than a warning.

Otherwise the message left this system, and the question is delivery at the far
end: attachment size limits, a quarantine, or a recipient domain that silently
drops mail with zip attachments.

## 4. The request was refused at the API with `recipient_not_allowed`

Not a delivery problem. The address is not an active entry on the tenant's
allowlist, and the error names it. Register it at `POST /v1/reports/recipients`
— compliance role, with a stated reason — and re-request.

A whole request is refused when *any* address is unknown, rather than sending to
the recognised subset. A partial send is a supervisor waiting for a report that
four of five people received, with nobody noticing for a week.

## 5. A monthly schedule has not delivered

Start with what the schedule believes:

```bash
python manage.py disputeshield_run_report_schedules --dry-run
```

It prints, per schedule, the months it considers owed. A month is owed until a
delivery for it is confirmed `sent`, so this is the real state rather than a
next-run timestamp.

- **Nothing owed, and the recipient has nothing** → the delivery went out; go to
  §3. `last_period_delivered` on `GET /v1/reports/schedules` says which month was
  the last confirmed one.
- **Months owed going back further than a day** → nothing is running the runner.
  `disputeshield_doctor` reports this as `report schedules`. Check that the
  `disputeshield.reports.run_schedules` beat task is scheduled; a deployment with
  the worker but no beat leaves schedules looking perfectly healthy.
- **One month owed and stuck** → look for its delivery rows. The schedule opens a
  new attempt per failure, up to three, then records the month in
  `failed_periods` and steps over it. Each attempt's `last_error` says why; §1
  and §2 above cover the two causes.
- **`report.schedule_blocked` in the audit trail** → every recipient on the
  schedule was deactivated, or the export exceeded the attachment limit. The
  month stays owed, so fixing the recipients and waiting for the next hourly run
  is enough — no replay needed.

A month in `failed_periods` is **not** retried automatically. That is deliberate:
three failed attempts means something about the period needs a person. Deliver it
by hand with `POST /v1/reports/regulatory/email` for that period once the cause is
fixed.

## 6. Post-incident

- Was the refusal correct? A refusal that turns out to have been right needs no
  fix — resist adding a "force send" flag, which converts every future instance
  of §1 into a silently wrong disclosure.
- If a period had to be re-requested more than once because it kept changing, the
  period was open-ended. Exports of open periods are inherently unstable; that is
  worth saying to the requester rather than engineering around.
- Anything an installation could get wrong at deploy time belongs in
  `disputeshield_doctor`.
- If a schedule abandoned a month, ask why the period was still moving three
  attempts later. A period with long-open cases is not a good fit for an early
  `day_of_month`; moving the schedule later in the month is usually the fix, and
  is better than raising the attempt limit.

---

## Related

- Why nothing is stored in the queue row, and why a mismatch refuses:
  [ADR-0008](adr/0008-emailed-exports-are-rebuilt-not-stored.md)
- The export itself, and its byte-reproducibility guarantee: specification §6.5
- Outbox retry, parking and idempotency: §8.6
