import { useState } from 'react';

import Clock from '../queue/Clock.jsx';
import { formatDuration } from '../clock.js';
import { formatWhen, humanise } from '../format.js';
import { money } from '../queue/Queue.jsx';
import Composer from './Composer.jsx';
import Conversation from './Conversation.jsx';

/**
 * The case view. Two columns: conversation on the left, clock and context fixed
 * on the right.
 *
 * The right column is sticky because the whole point of the clock is that it is
 * always true. A clock that scrolls out of view while an agent reads a long
 * thread is a clock they last saw several minutes ago.
 */
export default function Case({ dispute, context, onBack, onSend, onPause, onResume, busy }) {
  const [pauseReason, setPauseReason] = useState('');
  const paused = dispute.sla?.state === 'paused';

  return (
    <div className="ds-case">
      <div className="ds-case-main">
        <a className="ds-link ds-back" href="#/" onClick={onBack}>
          ← Queue
        </a>
        <h1 className="ds-h1">{dispute.reference}</h1>
        <p className="ds-lede">{dispute.description}</p>

        <h2 className="ds-h2">Conversation</h2>
        <Conversation messages={dispute.messages || []} />

        <Composer visibility="customer" onSend={onSend} busy={busy} />
        <Composer visibility="internal" onSend={onSend} busy={busy} />
      </div>

      <aside className="ds-case-side" aria-label="Clock and context">
        <section className="ds-panel">
          <h2 className="ds-h3">Time remaining</h2>
          <Clock dispute={dispute} size="large" />

          {/* A second, differently-labelled figure. Business time is not the same
              quantity as time until the deadline, and giving them one label
              between them would make the queue and this page contradict each
              other on a business-hours policy. */}
          {typeof dispute.sla?.remaining_seconds === 'number' ? (
            <p className="ds-note">
              {dispute.sla.remaining_seconds >= 0
                ? `${formatDuration(dispute.sla.remaining_seconds)} of working time, under this policy's business hours.`
                : `${formatDuration(-dispute.sla.remaining_seconds)} of working time past the deadline.`}
            </p>
          ) : null}

          {dispute.sla?.paused_intervals ? (
            <p className="ds-note">
              Paused {dispute.sla.paused_intervals}{' '}
              {dispute.sla.paused_intervals === 1 ? 'time' : 'times'} so far. Every pause carries a
              recorded reason.
            </p>
          ) : null}

          {paused ? (
            <button
              type="button"
              className="ds-button"
              disabled={busy}
              onClick={() => onResume(pauseReason || 'Resumed from the case view.')}
            >
              Resume the clock
            </button>
          ) : (
            <div className="ds-field">
              <label htmlFor="pause-reason">Pause the clock — reason</label>
              <input
                id="pause-reason"
                className="ds-input"
                value={pauseReason}
                onChange={(e) => setPauseReason(e.target.value)}
                aria-describedby="pause-help"
              />
              <p id="pause-help" className="ds-help">
                Mandatory. A pausable clock is an abusable clock, and the reason is what makes
                that visible.
              </p>
              <button
                type="button"
                className="ds-button"
                disabled={busy || !pauseReason.trim()}
                onClick={() => onPause(pauseReason)}
              >
                Pause
              </button>
            </div>
          )}
        </section>

        <section className="ds-panel">
          <h2 className="ds-h3">Case</h2>
          <dl className="ds-facts">
            <dt>Status</dt>
            <dd>{humanise(dispute.status)}</dd>
            <dt>Category</dt>
            <dd>{humanise(dispute.category)}</dd>
            <dt>Amount</dt>
            <dd className="ds-num">{money(dispute)}</dd>
            <dt>Transaction</dt>
            <dd className="ds-mono">{dispute.transaction_ref || '—'}</dd>
            <dt>Assigned</dt>
            <dd>{dispute.assigned_to || 'unassigned'}</dd>
          </dl>
        </section>

        <section className="ds-panel">
          <h2 className="ds-h3">Context</h2>
          <p className="ds-help">
            Pushed by the host application, never pulled. Nothing here was fetched from the
            customer&apos;s account by us.
          </p>
          <ol className="ds-timeline">
            {context.map((entry) => (
              <li key={entry.id}>
                <p className="ds-timeline-when ds-num">
                  <time dateTime={entry.occurred_at}>{formatWhen(entry.occurred_at)}</time>
                </p>
                <p>{entry.summary}</p>
                <p className="ds-note">{humanise(entry.source)}</p>
              </li>
            ))}
            {context.length === 0 ? <li className="ds-empty">No context pushed.</li> : null}
          </ol>
        </section>
      </aside>
    </div>
  );
}
