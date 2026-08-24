import { useEffect, useState } from 'react';

import { formatWhen, humanise } from '../format.js';
import { FIELDS, describeChange, labelOf, parseThresholds, problemsWith } from './terms.js';

/**
 * SLA policies.
 *
 * **The screen is built around the fact that nothing here is edited.** A policy's
 * terms are immutable and versioned (ADR-0004): a change publishes version n+1,
 * and every case keeps the version it was filed under. So the form does not say
 * "Save", the history is not an afterthought panel, and the button states what
 * is about to happen — because an interface that reads like editing a settings
 * page teaches the officer a model of the system that is wrong in the one way
 * that matters when a supervisor asks what standard a case was judged against.
 *
 * Monochrome throughout. Colour is reserved for time, and while these numbers
 * *define* deadlines, none of them is one.
 */
export default function Policies({ policies, calendars, selected, onSelect, onPublish, busy }) {
  const policy = policies.find((p) => p.id === selected) || policies[0] || null;

  return (
    <section aria-labelledby="policies-heading">
      <h1 id="policies-heading" className="ds-h1">
        SLA policies
      </h1>
      <p className="ds-lede">
        One policy per dispute category, because a card chargeback and a failed airtime purchase
        have different regulatory windows. Terms are versioned and never edited: a change publishes
        a new version, and every case keeps the version it was filed under.
      </p>

      <div className="ds-policies">
        <nav aria-label="Policies" className="ds-policy-list">
          <ul>
            {policies.map((entry) => (
              <li key={entry.id}>
                <button
                  type="button"
                  className={`ds-policy-tab ${entry.id === policy?.id ? 'ds-policy-tab-current' : ''}`}
                  aria-current={entry.id === policy?.id ? 'true' : undefined}
                  onClick={() => onSelect(entry.id)}
                >
                  <span className="ds-policy-name">{humanise(entry.category)}</span>
                  <span className="ds-note">
                    v{entry.current?.version ?? 0} · {entry.version_count}{' '}
                    {entry.version_count === 1 ? 'version' : 'versions'}
                  </span>
                </button>
              </li>
            ))}
            {policies.length === 0 ? <li className="ds-empty">No policies configured.</li> : null}
          </ul>
        </nav>

        {policy ? (
          <div className="ds-policy-detail">
            <PolicyForm
              key={`${policy.id}:${policy.current?.version}`}
              policy={policy}
              calendars={calendars}
              busy={busy}
              onPublish={onPublish}
            />
            <History policy={policy} />
          </div>
        ) : null}
      </div>
    </section>
  );
}

function PolicyForm({ policy, calendars, busy, onPublish }) {
  const current = policy.current || {};
  const [terms, setTerms] = useState(() => ({
    acknowledgement_minutes: current.acknowledgement_minutes ?? 60,
    resolution_hours: current.resolution_hours ?? 72,
    escalate_at_percent: current.escalate_at_percent ?? 80,
    auto_close_after_hours: current.auto_close_after_hours ?? 168,
    reopen_window_hours: current.reopen_window_hours ?? 336,
    business_hours_only: current.business_hours_only ?? true,
    warning_thresholds: current.warning_thresholds ?? [50, 80, 95],
    regulatory_reference: current.regulatory_reference ?? ''
  }));
  const [thresholdText, setThresholdText] = useState(
    (current.warning_thresholds ?? [50, 80, 95]).join(', ')
  );
  const [error, setError] = useState('');

  useEffect(() => setError(''), [terms]);

  const problems = problemsWith(terms);
  const changed = FIELDS.some((field) => terms[field.name] !== current[field.name])
    || terms.business_hours_only !== current.business_hours_only
    || terms.regulatory_reference !== (current.regulatory_reference ?? '')
    || String(terms.warning_thresholds) !== String(current.warning_thresholds ?? []);

  function set(name, value) {
    setTerms((existing) => ({ ...existing, [name]: value }));
  }

  async function submit(event) {
    event.preventDefault();
    setError('');
    try {
      await onPublish(policy.id, terms);
    } catch (problem) {
      setError(problem.message);
    }
  }

  return (
    <form className="ds-form ds-policy-form" onSubmit={submit} aria-labelledby="terms-heading">
      <h2 id="terms-heading" className="ds-h2">
        {humanise(policy.category)} — version {current.version ?? 0}
      </h2>
      <p className="ds-note">
        In force since {formatWhen(current.created_at)}, published by {current.created_by || '—'}.
        Calendar: {current.calendar} ({current.calendar_timezone}).
      </p>

      <div className="ds-row ds-row-2">
        {FIELDS.map((field) => (
          <div className="ds-field" key={field.name}>
            <label htmlFor={`term-${field.name}`}>
              {field.label} <span className="ds-note-inline">({field.unit})</span>
            </label>
            <input
              id={`term-${field.name}`}
              className="ds-input ds-num"
              type="number"
              min={field.min}
              max={field.max}
              value={terms[field.name]}
              onChange={(e) => set(field.name, Number.parseInt(e.target.value, 10) || 0)}
              aria-describedby={`term-${field.name}-help`}
            />
            <p id={`term-${field.name}-help`} className="ds-help">
              {field.help}
            </p>
          </div>
        ))}

        <div className="ds-field">
          <label htmlFor="term-thresholds">
            Warning thresholds <span className="ds-note-inline">(% of the window)</span>
          </label>
          <input
            id="term-thresholds"
            className="ds-input ds-num"
            value={thresholdText}
            onChange={(e) => {
              setThresholdText(e.target.value);
              set('warning_thresholds', parseThresholds(e.target.value));
            }}
            aria-describedby="term-thresholds-help"
          />
          <p id="term-thresholds-help" className="ds-help">
            When an agent is warned. Sorted and deduplicated when published.
          </p>
        </div>

        <div className="ds-field">
          <label htmlFor="term-calendar">Business calendar</label>
          <select
            id="term-calendar"
            className="ds-input"
            value={current.calendar || ''}
            disabled
            aria-describedby="term-calendar-help"
          >
            {calendars.map((calendar) => (
              <option key={calendar.id} value={calendar.name}>
                {calendar.name} ({calendar.timezone})
              </option>
            ))}
            {calendars.length === 0 ? <option value="">None configured</option> : null}
          </select>
          <p id="term-calendar-help" className="ds-help">
            Changed on the calendar itself, not here. A calendar is shared by several policies, and
            editing it from inside one of them hides that from the others.
          </p>
        </div>
      </div>

      <label className="ds-check">
        <input
          type="checkbox"
          checked={terms.business_hours_only}
          onChange={(e) => set('business_hours_only', e.target.checked)}
        />
        Count business hours only
      </label>

      <div className="ds-field">
        <label htmlFor="term-reference">Regulatory reference</label>
        <input
          id="term-reference"
          className="ds-input"
          value={terms.regulatory_reference}
          onChange={(e) => set('regulatory_reference', e.target.value)}
          aria-describedby="term-reference-help"
        />
        <p id="term-reference-help" className="ds-help">
          What these windows are derived from. Turns a configuration value into documented
          evidence of intent, which is what a supervisor asks about.
        </p>
      </div>

      {problems.length ? (
        <ul className="ds-problems" role="alert">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : null}
      {error ? (
        <p className="ds-error" role="alert">
          {error}
        </p>
      ) : null}

      <p className="ds-help">
        Publishing creates version {(current.version ?? 0) + 1}. Cases already filed keep version{' '}
        {current.version ?? 0} — the standard they were judged against does not move.
      </p>
      <button
        type="submit"
        className="ds-button ds-button-primary"
        disabled={busy || problems.length > 0 || !changed}
      >
        Publish version {(current.version ?? 0) + 1}
      </button>
    </form>
  );
}

function History({ policy }) {
  const history = policy.history || [];
  return (
    <section className="ds-panel ds-history" aria-labelledby="history-heading">
      <h2 id="history-heading" className="ds-h3">
        Change history
      </h2>
      {/* Kept beside the terms rather than behind a tab, because a breach rate
          that improves the week after the resolution window doubled is a fact
          about the policy rather than about the operation — but that is the
          reason for the layout, not something to tell the reader. */}
      <p className="ds-help">
        Every published version, newest first, with what changed and who changed it. This is what
        a supervisor is shown when they ask which standard a case was judged against.
      </p>
      <ol className="ds-timeline">
        {history.map((entry) => (
          <li key={entry.id}>
            <p className="ds-timeline-when ds-num">
              v{entry.version} ·{' '}
              <time dateTime={entry.created_at}>{formatWhen(entry.created_at)}</time>
            </p>
            {Object.keys(entry.changed || {}).length ? (
              <ul className="ds-changes">
                {Object.entries(entry.changed).map(([field, values]) => (
                  <li key={field}>
                    <span className="ds-change-field">{labelOf(field)}</span>{' '}
                    <span className="ds-num">{describeChange(field, values)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="ds-note">The first version of this policy.</p>
            )}
            <p className="ds-note">by {entry.created_by || '—'}</p>
          </li>
        ))}
        {history.length === 0 ? <li className="ds-empty">No versions yet.</li> : null}
      </ol>
    </section>
  );
}
