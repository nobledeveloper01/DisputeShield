/**
 * A schedule's health, as a deadline state.
 *
 * DESIGN.md reserves colour for time: "if a pixel is saturated, it is telling
 * you about a deadline." A monthly regulatory return that did not go out is a
 * missed deadline — arguably the most consequential one in the product — so a
 * schedule's health maps onto the same scale the queue uses, and a schedule
 * that is up to date is monochrome like a comfortable case.
 *
 * The mapping, and why each one is where it is:
 *
 *   failed      A month was abandoned after its attempts. The return did not go
 *               out and nothing will retry it. Breached: solid fill, pinned to
 *               the top of the list.
 *   overdue     A month has been owed for longer than the grace window, which
 *               means nothing is running the schedule. Saturated.
 *   owed        A month is owed and recently due. This is the normal state for a
 *               few hours after a period closes. Muted tint, like Notice.
 *   paused      Deactivated. DESIGN.md is explicit that a paused clock must
 *               never read as a comfortable one — a schedule somebody switched
 *               off, possibly for the wrong reason, is exactly that case.
 *   current     Nothing owed. No colour at all.
 *
 * This function takes the server's own derived fields rather than recomputing
 * them. The month arithmetic is subtle and lives in one place, on the side that
 * actually sends the mail.
 */

export const STATES = ['failed', 'overdue', 'owed', 'paused', 'current'];

export function healthOf(schedule) {
  if (schedule.failed_periods?.length) {
    const periods = schedule.failed_periods.map((entry) => entry.period);
    return {
      state: 'failed',
      // Never colour alone: every state carries a label and a position in the
      // sort order, because roughly one in twelve men has a colour vision
      // deficiency and §9 makes this a regulatory obligation.
      label: periods.length === 1 ? 'NOT DELIVERED' : `${periods.length} NOT DELIVERED`,
      detail: `${periods.join(', ')} — abandoned after the maximum attempts. Nothing will retry these.`
    };
  }

  if (schedule.is_overdue) {
    return {
      state: 'overdue',
      label: 'OVERDUE',
      detail: `Owed since ${schedule.periods_owed[0]}. Nothing appears to be running the schedule.`
    };
  }

  if (!schedule.is_active) {
    return {
      state: 'paused',
      label: 'PAUSED',
      detail: 'Deactivated. No further months will be delivered.'
    };
  }

  if (schedule.periods_owed?.length) {
    return {
      state: 'owed',
      label: 'DUE',
      detail: `${schedule.periods_owed.join(', ')} queued for delivery.`
    };
  }

  return {
    state: 'current',
    label: 'UP TO DATE',
    // Not "last delivered X" — the row prints that on its own line, and saying
    // it twice makes the one line that matters read as filler.
    detail: schedule.last_period_delivered
      ? 'Nothing owed. The next report goes out once the current month closes.'
      : 'Nothing due yet. The first report covers the month this schedule was created in.'
  };
}

/**
 * The sort order is the design (DESIGN.md). A compliance officer should not have
 * to sort this list to find the schedule that failed.
 */
export function bySeverity(a, b) {
  const rank = (s) => STATES.indexOf(healthOf(s).state);
  return rank(a) - rank(b) || a.name.localeCompare(b.name);
}
