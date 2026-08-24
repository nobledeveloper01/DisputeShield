/**
 * Turning stored values into things a person reads at a glance.
 *
 * A raw `2026-08-24T16:25:42.799Z` in a conversation is a timestamp an agent has
 * to decode, and `failed_transfer` in a category column is a database value that
 * escaped. Neither is wrong, exactly — both are just the storage format shown to
 * somebody who does not work in it.
 */

/** `24 Aug, 16:25`. The year appears only when it is not the current one. */
export function formatWhen(iso, now = Date.now()) {
  if (!iso) return '';
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return '';

  const sameYear = when.getUTCFullYear() === new Date(now).getUTCFullYear();
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    year: sameYear ? undefined : 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC'
  }).format(when);
}

/** `failed_transfer` becomes `Failed transfer`. */
export function humanise(value) {
  if (!value) return '';
  const words = String(value).replace(/_/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
