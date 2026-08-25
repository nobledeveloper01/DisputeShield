import assert from 'node:assert/strict';
import { test } from 'node:test';

import { countKeys, memberBlock, revokeWarning } from '../src/settings/rules.js';

const liveKey = { id: 'k1', environment: 'live', is_active: true, is_current: false };
const testKey = { id: 'k2', environment: 'test', is_active: true, is_current: false };

test('revoking the key this session is using is flagged before the click', () => {
  const warning = revokeWarning({ ...testKey, is_current: true }, [testKey]);
  assert.match(warning, /next request will fail/);
});

test('revoking the only live key is flagged as stopping every integration', () => {
  assert.match(revokeWarning(liveKey, [liveKey, testKey]), /every live integration/);
});

test('revoking one of several live keys needs no warning', () => {
  const second = { ...liveKey, id: 'k3' };
  assert.equal(revokeWarning(liveKey, [liveKey, second]), null);
});

test('an already-revoked key has nothing to warn about', () => {
  assert.equal(revokeWarning({ ...liveKey, is_active: false }, [liveKey]), null);
});

test('a test key is not treated as a live one', () => {
  assert.equal(revokeWarning(testKey, [testKey]), null);
});

const soleOwner = { id: 'a1', role: 'owner', is_active: true, is_you: false };
const agent = { id: 'a2', role: 'agent', is_active: true, is_you: false };

test('the only active owner cannot be changed', () => {
  assert.match(memberBlock(soleOwner, [soleOwner, agent]), /only active owner/);
});

test('a second owner unblocks the first', () => {
  const second = { id: 'a3', role: 'owner', is_active: true, is_you: false };
  assert.equal(memberBlock(soleOwner, [soleOwner, second]), null);
});

test('an inactive owner does not count towards the guard', () => {
  const inactive = { id: 'a3', role: 'owner', is_active: false, is_you: false };
  assert.match(memberBlock(soleOwner, [soleOwner, inactive]), /only active owner/);
});

test('nobody changes their own role, even with other owners around', () => {
  const you = { id: 'a1', role: 'compliance', is_active: true, is_you: true };
  const other = { id: 'a3', role: 'owner', is_active: true, is_you: false };
  assert.match(memberBlock(you, [you, other], { action: 'role' }), /your own role/);
});

test('you may still deactivate yourself when you are not the last owner', () => {
  const you = { id: 'a1', role: 'compliance', is_active: true, is_you: true };
  const other = { id: 'a3', role: 'owner', is_active: true, is_you: false };
  assert.equal(memberBlock(you, [you, other], { action: 'deactivate' }), null);
});

test('live and test keys are counted apart', () => {
  const revoked = { ...liveKey, id: 'k9', is_active: false };
  assert.deepEqual(countKeys([liveKey, testKey, revoked]), { live: 1, test: 1, revoked: 1 });
});
