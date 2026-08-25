/**
 * The rules the settings screen has to state before somebody acts.
 *
 * All of these are enforced by the server. What lives here is the *explanation*,
 * shown before the button rather than after the refusal — because every one of
 * these actions is either irreversible or locks somebody out, and a form that
 * only explains itself when it says no has already wasted the attempt.
 */

/** Why this key cannot be revoked, or null if it can. */
export function revokeWarning(key, keys) {
  if (!key.is_active) return null;
  if (key.is_current) {
    return 'This is the key this session is using. Revoking it takes effect immediately and the next request will fail — correct if it has leaked, surprising otherwise.';
  }
  const liveKeysLeft = keys.filter(
    (other) => other.is_active && other.environment === 'live' && other.id !== key.id
  ).length;
  if (key.environment === 'live' && liveKeysLeft === 0) {
    return 'This is the only active live key. Revoking it stops every live integration until a new one is issued and deployed.';
  }
  return null;
}

/**
 * Why this member cannot be changed, or null if they can.
 *
 * Mirrors the server's two rules, which exist because neither has a recovery
 * path: a tenant with no active owner cannot mint a key, change a role or
 * register an origin, and there is no way back.
 */
export function memberBlock(member, members, { action } = { action: 'change' }) {
  if (member.is_you && action === 'role') {
    return 'You cannot change your own role. A role you can raise is not a role, and one you can lower by accident is a lockout.';
  }
  if (member.role !== 'owner' || !member.is_active) return null;

  const otherActiveOwners = members.filter(
    (other) => other.id !== member.id && other.role === 'owner' && other.is_active
  ).length;
  if (otherActiveOwners > 0) return null;

  return 'This is the only active owner. A tenant with no owner cannot mint a key, change a role or register an origin, and there is no way back from that state. Promote another owner first.';
}

/** `ds_test_a1b2…` — enough to recognise a key, never enough to use one. */
export function maskedKey(prefix) {
  return `${prefix}…`;
}

/** Live keys and test keys are counted apart, because they mean different things
 *  when one is missing. */
export function countKeys(keys) {
  const active = keys.filter((key) => key.is_active);
  return {
    live: active.filter((key) => key.environment === 'live').length,
    test: active.filter((key) => key.environment === 'test').length,
    revoked: keys.length - active.length
  };
}
