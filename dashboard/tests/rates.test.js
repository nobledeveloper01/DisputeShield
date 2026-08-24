import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  byBreachRate,
  durationOf,
  money,
  percent,
  severityOf,
  splitCauses
} from '../src/analysis/rates.js';

test('a breach rate reads to one decimal, not fifteen', () => {
  assert.equal(percent(0.0732), '7.3%');
  assert.equal(percent(0), '0.0%');
});

test('a rate that cannot be computed says so rather than showing NaN', () => {
  assert.equal(percent(undefined), '—');
  assert.equal(percent(Number.NaN), '—');
});

test('any breach at all earns colour; none is monochrome', () => {
  // There are no invented 5%/10% bands. A threshold made up here would be read
  // as authoritative by the person least able to check it.
  assert.equal(severityOf({ breached: 0, breach_rate: 0 }), 'comfortable');
  assert.equal(severityOf({ breached: 1, breach_rate: 0.001 }), 'breached');
});

test('the worst group sorts first without anybody sorting', () => {
  const rows = [
    { key: 'a', breach_rate: 0.01, breached: 1 },
    { key: 'b', breach_rate: 0.4, breached: 8 },
    { key: 'c', breach_rate: 0.1, breached: 3 }
  ];
  assert.deepEqual([...rows].sort(byBreachRate).map((r) => r.key), ['b', 'c', 'a']);
});

test('undocumented causes are separated, not ranked among the rest', () => {
  // Sorting by frequency buries "we don't know" behind whichever incident
  // happened to be biggest.
  const { undocumented, documented, undocumentedRate } = splitCauses([
    { cause: 'Beat scheduler stalled; INC-2026-0823', cases: 9 },
    { cause: 'undocumented', cases: 3 }
  ]);
  assert.equal(undocumented.cases, 3);
  assert.equal(documented.length, 1);
  assert.equal(undocumentedRate, 0.25);
});

test('no undocumented breaches is reported as a rate of zero, not as missing', () => {
  const split = splitCauses([{ cause: 'Scheme outage', cases: 4 }]);
  assert.equal(split.undocumented, null);
  assert.equal(split.undocumentedRate, 0);
  assert.equal(split.total, 4);
});

test('an empty period does not divide by zero', () => {
  const split = splitCauses([]);
  assert.equal(split.total, 0);
  assert.equal(split.undocumentedRate, 0);
});

test('pause durations read at the scale they occur on', () => {
  assert.equal(durationOf(0), 'none');
  assert.equal(durationOf(1800), '30m');
  assert.equal(durationOf(9000), '2.5h');
  assert.equal(durationOf(3 * 86400), '3.0d');
});

test('a single-currency total is rendered with its currency', () => {
  assert.equal(money(184_500_000, ['NGN']), 'NGN 1,845,000.00');
});

test('a total across two currencies is refused rather than rendered', () => {
  // The backend sums minor units without asking whether they are the same unit,
  // so this figure adds kobo to cents. With a currency symbol in front of it, it
  // would be quoted to a regulator.
  assert.equal(money(184_500_000, ['NGN', 'USD']), null);
});

test('a period with no currency recorded still renders the amount', () => {
  assert.equal(money(0, []), '0.00');
});
