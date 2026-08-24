/**
 * The arithmetic behind the breach analysis view.
 *
 * **Why there are no breach-rate bands.** The obvious design gives this screen a
 * green/amber/red scale at, say, 5% and 10%. Nothing in the regulation or the
 * specification says those numbers, and a threshold invented here would be read
 * as authoritative by the person least able to check it — a compliance officer
 * who is accountable for the regulatory relationship and does not write the
 * code. So the encoding is: **any breach at all is a missed deadline**, which is
 * what earns colour under DESIGN.md's rule, and the bar length carries the
 * magnitude. A group with no breaches is monochrome.
 */

/** `0.0732` becomes `7.3%`. Quoted to one decimal: enough to compare, not enough
 *  to imply a precision the sample size does not support. */
export function percent(rate) {
  if (!Number.isFinite(rate)) return '—';
  return `${(rate * 100).toFixed(1)}%`;
}

export function durationOf(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  if (total === 0) return 'none';
  if (total < 3600) return `${Math.round(total / 60)}m`;
  if (total < 86400) return `${(total / 3600).toFixed(1)}h`;
  return `${(total / 86400).toFixed(1)}d`;
}

/** Worst first. The sort order is the design here as much as in the queue. */
export function byBreachRate(a, b) {
  return b.breach_rate - a.breach_rate || b.breached - a.breached || a.key.localeCompare(b.key);
}

export function severityOf(row) {
  return row.breached > 0 ? 'breached' : 'comfortable';
}

/**
 * Causes, with the undocumented ones pulled out.
 *
 * §11.5 requires every breach in an incident window to be annotated with its
 * systems cause. A breach with a documented cause is defensible; an
 * undocumented one is the answer "we don't know" given to a regulator. Sorting
 * causes by frequency buries that behind whichever incident happened to be
 * biggest, so it is separated rather than ranked.
 */
export function splitCauses(causes = []) {
  const undocumented = causes.find((entry) => entry.cause === 'undocumented') || null;
  const documented = causes.filter((entry) => entry.cause !== 'undocumented');
  const total = causes.reduce((sum, entry) => sum + entry.cases, 0);
  return {
    undocumented,
    documented,
    total,
    undocumentedRate: total ? (undocumented?.cases || 0) / total : 0
  };
}

/**
 * Recorded, never executed (§3.3). The label has to carry that everywhere.
 *
 * A total across more than one currency is not a number. The backend sums minor
 * units without asking whether they are the same unit, so a period holding both
 * NGN and USD cases produces a figure that adds kobo to cents — and rendered
 * with a currency symbol in front of it, that figure would be quoted to a
 * regulator. Where the period is not single-currency, this returns null and the
 * caller says so instead.
 */
export function money(minor, currencies = []) {
  const list = Array.isArray(currencies) ? currencies : [currencies].filter(Boolean);
  if (list.length > 1) return null;

  const value = (minor || 0) / 100;
  const amount = value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
  return `${list[0] || ''} ${amount}`.trim();
}
