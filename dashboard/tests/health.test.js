import assert from 'node:assert/strict';
import { test } from 'node:test';

import { bySeverity, healthOf } from '../src/reports/health.js';

const base = {
  name: 'Monthly supervisory export',
  is_active: true,
  is_overdue: false,
  periods_owed: [],
  failed_periods: [],
  last_period_delivered: '2026-04-01'
};

test('a schedule that is up to date carries no colour', () => {
  assert.equal(healthOf(base).state, 'current');
});

test('a month owed and recently due is the normal state, not an alarm', () => {
  const health = healthOf({ ...base, periods_owed: ['2026-05-01'] });
  assert.equal(health.state, 'owed');
  assert.match(health.detail, /2026-05-01/);
});

test('owed for longer than the grace window means nothing is running it', () => {
  const health = healthOf({ ...base, periods_owed: ['2026-03-01'], is_overdue: true });
  assert.equal(health.state, 'overdue');
});

test('an abandoned month outranks everything else', () => {
  const schedule = {
    ...base,
    is_overdue: true,
    periods_owed: ['2026-03-01'],
    failed_periods: [{ period: '2026-02-01', attempts: 3, last_error: 'x' }]
  };
  const health = healthOf(schedule);
  assert.equal(health.state, 'failed');
  assert.match(health.detail, /Nothing will retry/);
});

test('a deactivated schedule never reads as a comfortable one', () => {
  // DESIGN.md: a paused clock is not a safe clock.
  const health = healthOf({ ...base, is_active: false });
  assert.equal(health.state, 'paused');
  assert.notEqual(health.state, 'current');
});

test('every state carries a text label, so colour is never the only encoding', () => {
  const cases = [
    base,
    { ...base, periods_owed: ['2026-05-01'] },
    { ...base, is_overdue: true, periods_owed: ['2026-03-01'] },
    { ...base, is_active: false },
    { ...base, failed_periods: [{ period: '2026-02-01' }] }
  ];
  for (const schedule of cases) {
    const { label } = healthOf(schedule);
    assert.ok(label && label.trim().length > 0);
  }
});

test('the sort order puts the problem first without anybody sorting', () => {
  const healthy = { ...base, name: 'A healthy one' };
  const failed = { ...base, name: 'Z broken one', failed_periods: [{ period: '2026-02-01' }] };
  const sorted = [healthy, failed].sort(bySeverity);
  assert.equal(sorted[0].name, 'Z broken one');
});
