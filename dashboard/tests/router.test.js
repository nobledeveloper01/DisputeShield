import assert from 'node:assert/strict';
import { test } from 'node:test';

import { parse } from '../src/router.js';

test('the queue is the default route', () => {
  assert.deepEqual(parse(''), { name: 'queue' });
  assert.deepEqual(parse('#/'), { name: 'queue' });
});

test('a case route carries its identifier', () => {
  assert.deepEqual(parse('#/cases/dsp_01ABC'), { name: 'case', id: 'dsp_01ABC' });
});

test('an identifier outside the API’s own character set does not route', () => {
  // The management API pins `lookup_value_regex` to `[A-Za-z0-9_]+`. Matching
  // that here means a crafted hash cannot put arbitrary text into a request path.
  assert.deepEqual(parse('#/cases/../../etc'), { name: 'queue' });
  assert.deepEqual(parse('#/cases/a b'), { name: 'queue' });
});

test('the compliance routes resolve', () => {
  assert.deepEqual(parse('#/reports'), { name: 'reports' });
  assert.deepEqual(parse('#/analysis'), { name: 'analysis' });
});

test('an unknown route falls back to the queue rather than blank', () => {
  assert.deepEqual(parse('#/nowhere'), { name: 'queue' });
});
