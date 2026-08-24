/**
 * The clock. The single most important element in the product (DESIGN.md).
 *
 * It appears in the queue, on the case and in the breach analysis view, and it
 * has to read identically in all three — so the rules live here, once, as pure
 * functions, and every surface renders what this returns.
 *
 * **Which quantity it shows, and why that needed deciding.** The server computes
 * two different things. `resolution_deadline` is the instant a case is due, and
 * it is already calculated using the tenant's business hours and holidays.
 * `sla.remaining_seconds` is *business* time left, and the queue endpoint
 * deliberately does not compute it per row — doing so cost an N+1 and a calendar
 * walk per case, and put the queue over its 300ms p95 at the load target.
 *
 * If the queue showed time-to-deadline and the case showed business time under
 * the same label, the same case would read "2d 16h left" in the queue and "0h
 * left" when opened. That is the exact failure DESIGN.md's "reads identically"
 * rule exists to prevent, and it is the dangerous direction: an agent triages
 * from the queue figure and deprioritises something due at the start of Monday.
 *
 * So the clock is **time until the deadline**, everywhere. The case view also
 * shows business time remaining, under its own explicit label, because it is a
 * genuinely different quantity and a compliance conversation needs it. Two
 * figures with two labels is honest; one label over two quantities is not.
 */

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * Precision decreases with distance (DESIGN.md). False precision on a three-day
 * window is noise pretending to be information.
 */
export function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(seconds));

  if (total < HOUR) {
    return `${Math.floor(total / MINUTE)}m`;
  }
  if (total < DAY) {
    const hours = Math.floor(total / HOUR);
    const minutes = Math.floor((total % HOUR) / MINUTE);
    return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
  }
  return `${Math.floor(total / DAY)}d`;
}

/**
 * The five deadline states, plus paused.
 *
 * Thresholds are percentages of the window elapsed, measured from when the case
 * was submitted to when it is due — the same window the SLA policy defines.
 */
export function clockOf(dispute, now = Date.now()) {
  const sla = dispute.sla || {};
  const deadline = sla.resolution_deadline ? Date.parse(sla.resolution_deadline) : null;

  if (sla.breached) {
    // Negative time is never a minus sign. A minus sign is something a tired
    // reader misses.
    const ago = deadline === null ? null : (now - deadline) / 1000;
    return {
      state: 'breached',
      label: 'BREACHED',
      figure: ago === null ? '' : `${formatDuration(ago)} ago`,
      title: 'The mandated window has passed.'
    };
  }

  if (deadline === null) {
    return { state: 'none', label: 'NO CLOCK', figure: '', title: 'No resolution deadline set.' };
  }

  const remaining = (deadline - now) / 1000;

  if (sla.state === 'paused') {
    // Paused reads as paused first. A paused clock is not a safe clock — it is a
    // clock somebody stopped, possibly for the wrong reason, and rendering it as
    // calm is how pause abuse becomes invisible.
    return {
      state: 'paused',
      label: 'PAUSED',
      figure: `${formatDuration(remaining)} left`,
      title: 'The clock is stopped. Open the case for the recorded reason.'
    };
  }

  const started = dispute.submitted_at ? Date.parse(dispute.submitted_at) : null;
  const window = started === null ? null : (deadline - started) / 1000;
  const elapsed = window === null || window <= 0 ? null : 1 - remaining / window;

  const figure = `${formatDuration(remaining)} left`;
  if (elapsed !== null && elapsed >= 0.95) {
    return { state: 'critical', label: 'CRITICAL', figure, title: 'Past 95% of the window.' };
  }
  if (elapsed !== null && elapsed >= 0.8) {
    return { state: 'warning', label: 'AT RISK', figure, title: 'Past 80% of the window.' };
  }
  if (elapsed !== null && elapsed >= 0.5) {
    return { state: 'notice', label: '', figure, title: 'Past half the window.' };
  }
  // Comfortable: no colour at all, and no label competing for attention.
  return { state: 'comfortable', label: '', figure, title: '' };
}

/**
 * A recomputation every thirty seconds, not a smooth countdown.
 *
 * A countdown animating on eleven rows is motion that costs attention and buys
 * nothing, and digits that reflow as they tick are digits nobody reads at a
 * glance.
 */
export const TICK_MS = 30_000;
