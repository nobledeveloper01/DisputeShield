import assert from 'node:assert/strict';
import { test } from 'node:test';

import { brokenCategories, isColour, normaliseOrigin, originProblem } from '../src/widget/config.js';

test('a hex colour in any of its three lengths is accepted', () => {
  for (const value of ['#fff', '#0B5FFF', '#0b5fffcc']) {
    assert.equal(isColour(value), true, value);
  }
});

test('a colour name the browser would ignore is refused', () => {
  // A value the browser cannot parse leaves an unstyled control on somebody
  // else's page.
  for (const value of ['cornflower', '0B5FFF', '#12345', '', null]) {
    assert.equal(isColour(value), false, String(value));
  }
});

test('a sound origin has no problem', () => {
  assert.equal(originProblem('https://app.acme.test'), null);
  assert.equal(originProblem('http://localhost:4180'), null);
});

test('a trailing path is refused with what it would actually authorise', () => {
  const problem = originProblem('https://app.acme.test/checkout');
  assert.match(problem, /authorise the whole host/);
});

test('a wildcard is refused', () => {
  assert.match(originProblem('https://*.acme.test'), /wildcard/i);
});

test('the null origin is refused by name', () => {
  assert.match(originProblem('null'), /sandboxed/);
});

test('an origin with no scheme is refused', () => {
  assert.match(originProblem('app.acme.test'), /starts with http/);
});

test('a trailing slash is not a second origin', () => {
  assert.equal(normaliseOrigin('https://app.acme.test/'), 'https://app.acme.test');
  assert.equal(normaliseOrigin('  https://app.acme.test  '), 'https://app.acme.test');
});

test('a bare host with a trailing slash is still a valid origin', () => {
  assert.equal(originProblem('https://app.acme.test/'), null);
});

test('categories with no policy are the ones that break filing', () => {
  const broken = brokenCategories([
    { name: 'failed_transfer', has_policy: true },
    { name: 'duplicate_charge', has_policy: false }
  ]);
  assert.deepEqual(broken, ['duplicate_charge']);
});

test('a fully configured widget reports nothing broken', () => {
  assert.deepEqual(brokenCategories([{ name: 'failed_transfer', has_policy: true }]), []);
  assert.deepEqual(brokenCategories([]), []);
});
