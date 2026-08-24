import { humanise } from '../format.js';
import { byBreachRate, durationOf, money, percent, severityOf, splitCauses } from './rates.js';

/**
 * Breach analysis.
 *
 * Two things on this screen are placement decisions rather than layout, and both
 * come from the backend's own reasoning:
 *
 * **Deflections sit beside case volume, never in their own panel.** A feature
 * that reduces recorded complaints has to be the most heavily instrumented thing
 * in the product (amplifier A2): a drop in complaints during an outage must be
 * visibly a deflection rather than silently a suppression. Two numbers in
 * separate panels are two numbers nobody puts together.
 *
 * **Undocumented causes are separated from the rest, not ranked among them.**
 * §11.5 requires every breach in an incident window to be annotated with its
 * systems cause. A breach with a documented cause is defensible; an undocumented
 * one is "we don't know" said to a regulator. Sorting causes by frequency buries
 * that behind whichever incident happened to be biggest.
 */
export default function Analysis({ data, period, groupBy, onPeriod, onGroupBy }) {
  const summary = data.summary || {};
  const rows = [...(data.rows || [])].sort(byBreachRate);
  const causes = splitCauses(data.causes);
  const worst = rows.length ? rows[0].breach_rate : 0;

  return (
    <section aria-labelledby="analysis-heading">
      <div className="ds-queue-head">
        <h1 id="analysis-heading" className="ds-h1">
          Breach analysis
        </h1>
        <div className="ds-filters" role="group" aria-label="Period">
          <div className="ds-field">
            <label htmlFor="period-from">From</label>
            <input
              id="period-from"
              className="ds-input ds-num"
              type="date"
              value={period.from}
              onChange={(e) => onPeriod({ ...period, from: e.target.value })}
            />
          </div>
          <div className="ds-field">
            <label htmlFor="period-to">To</label>
            <input
              id="period-to"
              className="ds-input ds-num"
              type="date"
              value={period.to}
              onChange={(e) => onPeriod({ ...period, to: e.target.value })}
            />
          </div>
        </div>
      </div>

      <dl className="ds-summary">
        {/* Volume and deflections, adjacent and deliberately so. */}
        <div className="ds-summary-cell">
          <dt>Complaints recorded</dt>
          <dd className="ds-num ds-figure">{summary.cases ?? 0}</dd>
        </div>
        <div className="ds-summary-cell">
          <dt>Deflected before filing</dt>
          <dd>
            <span className="ds-num ds-figure">{summary.deflected ?? 0}</span>
            {/* Inside the <dd>, not beside it: a <div> inside a <dl> may only
                contain <dt> and <dd>, and a stray <p> makes the whole list
                unparseable to a screen reader. */}
            {/* Beside the count above rather than in its own panel: a fall in
                recorded complaints during an outage has to be visibly a
                deflection rather than silently a suppression. */}
            <span className="ds-help">
              Customers who were shown a known incident and did not file. Read this against the
              count beside it: a fall in complaints during an outage should be explained here.
            </span>
          </dd>
        </div>
        <div className="ds-summary-cell">
          <dt>Breached</dt>
          <dd className={`ds-num ds-figure ds-state-${summary.breached ? 'breached' : 'comfortable'}`}>
            {summary.breached ?? 0}
          </dd>
        </div>
        <div className="ds-summary-cell">
          <dt>Resolved</dt>
          <dd className="ds-num ds-figure">{summary.resolved ?? 0}</dd>
        </div>
        <div className="ds-summary-cell">
          <dt>Refunds recorded</dt>
          <dd>
            {money(summary.recorded_refund_minor, summary.currencies) === null ? (
              <>
                <span className="ds-figure">Mixed currencies</span>
                <span className="ds-help">
                  This period holds cases in {(summary.currencies || []).join(', ')}. A single
                  total would be minor units of different currencies added together, which is not
                  an amount. The regulatory export reports them separately.
                </span>
              </>
            ) : (
              <>
                <span className="ds-num ds-figure">
                  {money(summary.recorded_refund_minor, summary.currencies)}
                </span>
                <span className="ds-help">
                  What was promised, not what was paid. DisputeShield records refund amounts and
                  moves no money.
                </span>
              </>
            )}
          </dd>
        </div>
        <div className="ds-summary-cell">
          <dt>Average pause</dt>
          <dd className="ds-num ds-figure">{durationOf(summary.average_pause_seconds)}</dd>
        </div>
      </dl>

      <div className="ds-queue-head">
        <h2 className="ds-h2">Breaches by {groupBy}</h2>
        <div className="ds-filters">
          <label className="ds-visually-hidden" htmlFor="group-by">
            Group by
          </label>
          <select
            id="group-by"
            className="ds-input"
            value={groupBy}
            onChange={(e) => onGroupBy(e.target.value)}
          >
            <option value="category">By category</option>
            <option value="agent">By agent</option>
          </select>
        </div>
      </div>

      {groupBy === 'agent' ? (
        <p className="ds-lede">
          Pause time is reported per agent because §4.4 requires excessive pausing to be visible
          in the numbers. A clock somebody stopped is not a clock that stopped.
        </p>
      ) : null}

      <table className="ds-queue">
        <caption className="ds-visually-hidden">
          Cases and breaches by {groupBy}, worst breach rate first.
        </caption>
        <thead>
          <tr>
            <th scope="col">{humanise(groupBy)}</th>
            <th scope="col" className="ds-right">
              Cases
            </th>
            <th scope="col" className="ds-right">
              Breached
            </th>
            <th scope="col">Breach rate</th>
            <th scope="col" className="ds-right">
              Total pause
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className="ds-queue-row">
              <td>{humanise(row.key)}</td>
              <td className="ds-num ds-right">{row.cases}</td>
              <td className="ds-num ds-right">{row.breached}</td>
              <td>
                <span className={`ds-rate ds-state-${severityOf(row)}`}>
                  {/* The bar carries magnitude; the figure beside it is the
                      number. Colour is never the only encoding, and a bar
                      without its value is a shape rather than a measurement. */}
                  <span
                    className="ds-rate-bar"
                    style={{ width: `${worst ? (row.breach_rate / worst) * 100 : 0}%` }}
                    aria-hidden="true"
                  />
                  <span className="ds-num">{percent(row.breach_rate)}</span>
                </span>
              </td>
              <td className="ds-num ds-right">{durationOf(row.total_pause_seconds)}</td>
            </tr>
          ))}
          {rows.length === 0 ? (
            <tr>
              <td colSpan={5} className="ds-empty">
                No cases in this period.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>

      <h2 className="ds-h2">Why they breached</h2>

      <div className={`ds-panel ds-undocumented ds-state-${causes.undocumented ? 'breached' : 'comfortable'}`}>
        <h3 className="ds-h3">
          {causes.undocumented
            ? `${causes.undocumented.cases} breach${causes.undocumented.cases === 1 ? '' : 'es'} with no recorded cause`
            : 'Every breach has a recorded cause'}
        </h3>
        <p className="ds-note">
          {causes.undocumented
            ? `${percent(causes.undocumentedRate)} of breaches in this period. A breach with a documented cause is defensible; an undocumented one is "we do not know", said to a regulator. Annotate these before the period is reported on.`
            : 'Nothing here needs annotating before this period is reported on.'}
        </p>
      </div>

      <ol className="ds-causes">
        {causes.documented.map((entry) => (
          <li key={entry.cause}>
            <span className="ds-num ds-causes-count">{entry.cases}</span>
            <span>{entry.cause}</span>
          </li>
        ))}
        {causes.documented.length === 0 ? (
          <li className="ds-empty">No breaches with a recorded cause in this period.</li>
        ) : null}
      </ol>
    </section>
  );
}
