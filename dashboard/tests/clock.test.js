import assert from 'node:assert/strict';
import { test } from 'node:test';

import { clockOf, formatDuration } from '../src/clock.js';

const NOW = Date.parse('2026-05-01T12:00:00Z');
const at = (offsetSeconds) => new Date(NOW + offsetSeconds * 1000).toISOString();

const caseAt = (remainingSeconds, windowSeconds, extra = {}) => ({
  submitted_at: at(remainingSeconds - windowSeconds),
  sla: { resolution_deadline: at(remainingSeconds), state: 'running', breached: false, ...extra }
});

test('under an hour reads in minutes', () => {
  assert.equal(formatDuration(42 * 60), '42m');
  assert.equal(formatDuration(59 * 60 + 59), '59m');
});

test('under a day reads in hours and minutes', () => {
  assert.equal(formatDuration(4 * 3600 + 12 * 60), '4h 12m');
  assert.equal(formatDuration(3600), '1h');
});

test('beyond a day reads in days — false precision is noise', () => {
  assert.equal(formatDuration(3 * 86400 + 7 * 3600), '3d');
});

test('a breached case never renders a minus sign', () => {
  const clock = clockOf(
    { submitted_at: at(-86400), sla: { resolution_deadline: at(-8040), breached: true } },
    NOW
  );
  assert.equal(clock.label, 'BREACHED');
  assert.equal(clock.figure, '2h 14m ago');
  assert.doesNotMatch(clock.figure, /-/);
});

test('a paused case reads as paused first, never as a bare figure', () => {
  const clock = clockOf(caseAt(22 * 3600, 72 * 3600, { state: 'paused' }), NOW);
  assert.equal(clock.label, 'PAUSED');
  assert.equal(clock.figure, '22h left');
});

test('a paused case is never rendered as a comfortable one', () => {
  // DESIGN.md: a paused clock is not a safe clock. Rendering it as calm is how
  // pause abuse becomes invisible.
  const paused = clockOf(caseAt(60 * 3600, 72 * 3600, { state: 'paused' }), NOW);
  assert.notEqual(paused.state, 'comfortable');
});

test('a comfortable case carries no colour and no competing label', () => {
  const clock = clockOf(caseAt(60 * 3600, 72 * 3600), NOW);
  assert.equal(clock.state, 'comfortable');
  assert.equal(clock.label, '');
});

test('the thresholds are percentages of the window, not fixed durations', () => {
  // The same 6h remaining means different things on different windows. On an
  // 8-hour acknowledgement window it is most of the time left; on a 5-day
  // resolution window it is the last 5%. A fixed-hours threshold would shout on
  // the first and stay silent on the second.
  assert.equal(clockOf(caseAt(6 * 3600, 8 * 3600), NOW).state, 'comfortable');
  assert.equal(clockOf(caseAt(6 * 3600, 30 * 3600), NOW).state, 'warning');
  assert.equal(clockOf(caseAt(6 * 3600, 120 * 3600), NOW).state, 'critical');
});

test('each threshold lands where DESIGN.md puts it', () => {
  const window = 100 * 3600;
  assert.equal(clockOf(caseAt(0.51 * window, window), NOW).state, 'comfortable');
  assert.equal(clockOf(caseAt(0.49 * window, window), NOW).state, 'notice');
  assert.equal(clockOf(caseAt(0.19 * window, window), NOW).state, 'warning');
  assert.equal(clockOf(caseAt(0.04 * window, window), NOW).state, 'critical');
});

test('a case with no deadline says so rather than showing a plausible figure', () => {
  const clock = clockOf({ submitted_at: at(-3600), sla: {} }, NOW);
  assert.equal(clock.state, 'none');
  assert.equal(clock.figure, '');
});

test('breached outranks paused — a stopped clock does not un-breach a case', () => {
  const clock = clockOf(
    { submitted_at: at(-86400), sla: { resolution_deadline: at(-3600), state: 'paused', breached: true } },
    NOW
  );
  assert.equal(clock.state, 'breached');
});
