import { useId, useState } from 'react';

import { bySeverity, healthOf } from './health.js';

/**
 * Monthly export schedules.
 *
 * This is the one screen in the reports section that gets colour, and it earns
 * it under DESIGN.md's rule rather than in spite of it: a monthly regulatory
 * return that did not go out is a missed deadline. A schedule that is up to date
 * is monochrome, the sort order puts the failures first, and every state carries
 * a text label as well as a fill.
 *
 * What the screen leads with is `last_period_delivered` — the last month
 * confirmed *delivered*, not the last time the job ran. The distinction is the
 * whole point of the scheduler's design and it would be lost by showing a
 * "last run" timestamp, which is the figure that lets a schedule delivering
 * nothing look healthy.
 */
export default function Schedules({ schedules, recipients, onAdd, onDeactivate, busy }) {
  const [name, setName] = useState('');
  const [reason, setReason] = useState('');
  const [chosen, setChosen] = useState([]);
  const [day, setDay] = useState('5');
  const [hour, setHour] = useState('6');
  const [zone, setZone] = useState('UTC');
  const [error, setError] = useState('');
  const formId = useId();

  const eligible = recipients.filter((r) => r.is_active);
  const sorted = [...schedules].sort(bySeverity);

  function toggle(address) {
    setChosen((current) =>
      current.includes(address)
        ? current.filter((a) => a !== address)
        : [...current, address]
    );
  }

  async function submit(event) {
    event.preventDefault();
    setError('');
    try {
      await onAdd({
        name,
        reason,
        recipients: chosen,
        day_of_month: Number(day),
        hour: Number(hour),
        timezone: zone
      });
      setName('');
      setReason('');
      setChosen([]);
    } catch (problem) {
      setError(problem.message);
    }
  }

  return (
    <section aria-labelledby={`${formId}-heading`}>
      <h2 id={`${formId}-heading`} className="ds-h2">
        Monthly schedules
      </h2>
      <p className="ds-lede">
        Each run exports a calendar month that has ended, in the schedule&apos;s own timezone. A month
        is not marked delivered until it was delivered, so a schedule that is queueing and never
        sending shows here as overdue rather than as healthy.
      </p>

      <ul className="ds-schedules">
        {sorted.map((schedule) => {
          const health = healthOf(schedule);
          return (
            <li key={schedule.id} className={`ds-schedule ds-state-${health.state}`}>
              <div className="ds-schedule-main">
                <h3 className="ds-h3">{schedule.name}</h3>
                <p className="ds-note">
                  {schedule.recipients.join(', ')} · day {schedule.day_of_month} at{' '}
                  <span className="ds-num">
                    {String(schedule.hour).padStart(2, '0')}:00
                  </span>{' '}
                  {schedule.timezone}
                </p>
              </div>

              <div className="ds-schedule-state">
                <p className="ds-state-label">{health.label}</p>
                <p className="ds-note">{health.detail}</p>
                <p className="ds-note">
                  Last delivered:{' '}
                  <span className="ds-num">{schedule.last_period_delivered || 'never'}</span>
                </p>
              </div>

              <div className="ds-actions">
                {schedule.is_active ? (
                  <button
                    type="button"
                    className="ds-button"
                    disabled={busy}
                    onClick={() => onDeactivate(schedule)}
                  >
                    Deactivate
                    <span className="ds-visually-hidden"> {schedule.name}</span>
                  </button>
                ) : null}
              </div>
            </li>
          );
        })}
        {schedules.length === 0 ? (
          <li className="ds-empty">
            No schedules. Exports go out only when somebody asks for one.
          </li>
        ) : null}
      </ul>

      <form className="ds-form" onSubmit={submit} aria-labelledby={`${formId}-add`}>
        <h3 id={`${formId}-add`} className="ds-h3">
          Add a schedule
        </h3>

        <div className="ds-field">
          <label htmlFor={`${formId}-name`}>Name</label>
          <input
            id={`${formId}-name`}
            className="ds-input"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>

        <fieldset className="ds-fieldset">
          <legend>Recipients</legend>
          <p className="ds-help" id={`${formId}-recipients-help`}>
            Only addresses already on the allowlist. A schedule that cannot deliver should fail
            while somebody is looking at it, not silently every month.
          </p>
          {eligible.map((recipient) => (
            <div className="ds-check" key={recipient.id}>
              <input
                type="checkbox"
                id={`${formId}-r-${recipient.id}`}
                checked={chosen.includes(recipient.address)}
                onChange={() => toggle(recipient.address)}
                aria-describedby={`${formId}-recipients-help`}
              />
              <label htmlFor={`${formId}-r-${recipient.id}`}>
                {recipient.address} <span className="ds-note">{recipient.label}</span>
              </label>
            </div>
          ))}
          {eligible.length === 0 ? (
            <p className="ds-empty">Register a recipient first.</p>
          ) : null}
        </fieldset>

        <div className="ds-row">
          <div className="ds-field">
            <label htmlFor={`${formId}-day`}>Day of month</label>
            <input
              id={`${formId}-day`}
              className="ds-input ds-num"
              type="number"
              min="1"
              max="28"
              value={day}
              onChange={(e) => setDay(e.target.value)}
              aria-describedby={`${formId}-day-help`}
            />
            <p id={`${formId}-day-help`} className="ds-help">
              Of the month after the period, and 28 at the latest. Days 29 to 31 do not exist in
              every month, and sliding to the last day would make this deadline mean a different
              date in February.
            </p>
          </div>

          <div className="ds-field">
            <label htmlFor={`${formId}-hour`}>Hour</label>
            <input
              id={`${formId}-hour`}
              className="ds-input ds-num"
              type="number"
              min="0"
              max="23"
              value={hour}
              onChange={(e) => setHour(e.target.value)}
            />
          </div>

          <div className="ds-field">
            <label htmlFor={`${formId}-zone`}>Timezone</label>
            <input
              id={`${formId}-zone`}
              className="ds-input"
              value={zone}
              onChange={(e) => setZone(e.target.value)}
              aria-describedby={`${formId}-zone-help`}
            />
            <p id={`${formId}-zone-help`} className="ds-help">
              An IANA name. Your March does not start when UTC&apos;s does.
            </p>
          </div>
        </div>

        <div className="ds-field">
          <label htmlFor={`${formId}-reason`}>Reason</label>
          <input
            id={`${formId}-reason`}
            className="ds-input"
            required
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            aria-describedby={`${formId}-reason-help`}
          />
          <p id={`${formId}-reason-help`} className="ds-help">
            A standing instruction that a period leaves this system every month. Recorded against
            your name.
          </p>
        </div>

        {error ? (
          <p className="ds-error" role="alert">
            {error}
          </p>
        ) : null}

        <button type="submit" className="ds-button ds-button-primary" disabled={busy}>
          Create schedule
        </button>
      </form>
    </section>
  );
}
