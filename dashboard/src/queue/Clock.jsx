import { useEffect, useState } from 'react';

import { TICK_MS, clockOf } from '../clock.js';

/**
 * One component, every surface. The clock reads identically in the queue and on
 * the case because both render this.
 *
 * It ticks but does not animate: a recomputation on an interval, not a smooth
 * countdown. Digits that reflow as they tick are digits nobody reads at a
 * glance, which is the one thing this element exists to make possible.
 */
export default function Clock({ dispute, size = 'row' }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, []);

  const clock = clockOf(dispute, now);

  return (
    <span className={`ds-clock ds-clock-${size} ds-state-${clock.state}`} title={clock.title}>
      {clock.label ? <span className="ds-clock-label">{clock.label}</span> : null}
      {/* The separator belongs to the inline row form only. In the large size
          the label is its own line, and a leading "·" floats there orphaned. */}
      {size === 'row' && clock.label && clock.figure ? (
        <span aria-hidden="true"> · </span>
      ) : null}
      <span className="ds-clock-figure">{clock.figure}</span>
    </span>
  );
}
