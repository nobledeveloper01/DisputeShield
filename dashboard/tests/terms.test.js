import assert from 'node:assert/strict';
import { test } from 'node:test';

import { describeChange, labelOf, parseThresholds, problemsWith } from '../src/policies/terms.js';

const sound = {
  acknowledgement_minutes: 60,
  resolution_hours: 72,
  warning_thresholds: [50, 80, 95],
  escalate_at_percent: 80
};

test('thresholds are parsed, deduplicated and sorted', () => {
  assert.deepEqual(parseThresholds('80, 50 95 80'), [50, 80, 95]);
});

test('typing punctuation instead of numbers yields nothing rather than NaN', () => {
  assert.deepEqual(parseThresholds(', , '), []);
  assert.deepEqual(parseThresholds(null), []);
});

test('sound terms produce no complaints', () => {
  assert.deepEqual(problemsWith(sound), []);
});

test('a zero-hour window is called out for what it does', () => {
  const [problem] = problemsWith({ ...sound, resolution_hours: 0 });
  assert.match(problem, /breaches every case the moment it is filed/);
});

test('a threshold at 100 is called out as one that never fires', () => {
  const [problem] = problemsWith({ ...sound, warning_thresholds: [50, 100] });
  assert.match(problem, /never fires/);
});

test('escalation at 100% is refused', () => {
  assert.equal(problemsWith({ ...sound, escalate_at_percent: 100 }).length, 1);
});

test('a change reads as before and after, not as a diff to decode', () => {
  assert.equal(describeChange('resolution_hours', [72, 168]), '72 → 168');
  assert.equal(describeChange('warning_thresholds', [[50, 80], [50, 80, 95]]), '50, 80 → 50, 80, 95');
  assert.equal(describeChange('business_hours_only', [true, false]), 'yes → no');
});

test('a first version reads as unset rather than as null', () => {
  assert.equal(describeChange('resolution_hours', [null, 72]), 'unset → 72');
});

test('a stored field name reads as words in the history', () => {
  assert.equal(labelOf('resolution_hours'), 'Resolution window');
  assert.equal(labelOf('warning_thresholds'), 'Warning thresholds');
});
