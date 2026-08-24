import { humanise } from '../format.js';
import Clock from './Clock.jsx';

const RISK_FILTERS = [
  { value: '', label: 'Everything' },
  { value: 'breached', label: 'Breached' },
  { value: 'at_risk', label: 'Due within 24h' }
];

/**
 * The queue.
 *
 * **The sort order is the design.** Rows arrive in the server's urgency order —
 * breached pinned, then by deadline — and this component does not re-sort them.
 * An agent who has to sort a queue to find urgent work is using a table, and the
 * ordering is asserted server-side against an index, so re-sorting here would
 * quietly replace a tested guarantee with an untested one.
 *
 * **No skeleton screens.** A queue that renders progressively invites action on
 * a partial sort order, which is worse than a blank moment.
 */
export default function Queue({ disputes, filters, onFilter, onOpen, busy }) {
  return (
    <section aria-labelledby="queue-heading">
      <div className="ds-queue-head">
        <h1 id="queue-heading" className="ds-h1">
          Queue
        </h1>
        <div className="ds-filters" role="group" aria-label="Filter the queue">
          <label className="ds-visually-hidden" htmlFor="queue-risk">
            Risk
          </label>
          <select
            id="queue-risk"
            className="ds-input"
            value={filters.sla_risk}
            onChange={(e) => onFilter({ ...filters, sla_risk: e.target.value })}
          >
            {RISK_FILTERS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>

          <label className="ds-check">
            <input
              type="checkbox"
              checked={filters.open === 'true'}
              onChange={(e) => onFilter({ ...filters, open: e.target.checked ? 'true' : '' })}
            />
            Open only
          </label>
        </div>
      </div>

      <table className="ds-queue">
        <caption className="ds-visually-hidden">
          Cases, most at risk first. Breached cases are pinned to the top.
        </caption>
        <thead>
          <tr>
            <th scope="col">Time remaining</th>
            <th scope="col">Reference</th>
            <th scope="col">Category</th>
            <th scope="col">Customer</th>
            <th scope="col" className="ds-right">
              Amount
            </th>
            <th scope="col">Status</th>
            <th scope="col">Assigned</th>
          </tr>
        </thead>
        <tbody>
          {disputes.map((dispute) => (
            <tr key={dispute.id} className="ds-queue-row">
              <td className="ds-queue-clock">
                <Clock dispute={dispute} />
              </td>
              <td>
                {/* The whole row is not a link: a row-wide click target makes
                    selecting a reference to paste into a regulator's email
                    impossible. The reference is the link, and it is the thing an
                    agent is looking for anyway. */}
                <a className="ds-link" href={`#/cases/${dispute.id}`} onClick={onOpen}>
                  {dispute.reference}
                </a>
              </td>
              <td>{humanise(dispute.category)}</td>
              <td>{dispute.customer_display_name || dispute.customer_ref_hash?.slice(0, 12)}</td>
              <td className="ds-num ds-right">{money(dispute)}</td>
              <td>{humanise(dispute.status)}</td>
              <td>{dispute.assigned_to || <span className="ds-note">unassigned</span>}</td>
            </tr>
          ))}
          {disputes.length === 0 && !busy ? (
            <tr>
              <td colSpan={7} className="ds-empty">
                Nothing matches this filter.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </section>
  );
}

export function money(dispute) {
  if (dispute.amount_minor === null || dispute.amount_minor === undefined) return '—';
  return `${dispute.currency || ''} ${(dispute.amount_minor / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })}`.trim();
}
