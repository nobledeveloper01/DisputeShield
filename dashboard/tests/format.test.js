import assert from 'node:assert/strict';
import { test } from 'node:test';

import { formatWhen, humanise } from '../src/format.js';

const NOW = Date.parse('2026-08-24T12:00:00Z');

test('a timestamp reads as a date and a time, not as ISO', () => {
  const formatted = formatWhen('2026-08-24T16:25:42.799Z', NOW);
  assert.match(formatted, /24 Aug/);
  assert.match(formatted, /16:25/);
  assert.doesNotMatch(formatted, /T|Z|\.799/);
});

test('the year appears only when it is not the current one', () => {
  assert.doesNotMatch(formatWhen('2026-08-24T16:25:00Z', NOW), /2026/);
  assert.match(formatWhen('2025-01-04T09:00:00Z', NOW), /2025/);
});

test('a missing or unparseable timestamp renders nothing rather than "Invalid Date"', () => {
  assert.equal(formatWhen(null, NOW), '');
  assert.equal(formatWhen('not a date', NOW), '');
});

test('a stored category reads as words', () => {
  assert.equal(humanise('failed_transfer'), 'Failed transfer');
  assert.equal(humanise('awaiting_customer'), 'Awaiting customer');
  assert.equal(humanise(''), '');
});
