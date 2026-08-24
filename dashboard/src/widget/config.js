/**
 * Client-side checks for the widget configuration.
 *
 * The server validates all of this and is the one that decides. These exist to
 * say *why* before the request, and each message is the server's own — a
 * client-side message that disagrees with the server's is worse than none,
 * because the operator then has two different explanations of one refusal.
 */

/** `#fff`, `#0B5FFF` or `#0B5FFFCC`. Anything else leaves an unstyled control on
 *  somebody else's page. */
export function isColour(value) {
  return /^#(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(String(value || ''));
}

/**
 * Why an origin is not an origin.
 *
 * Each message names the consequence rather than the rule. A trailing path is
 * the one worth spelling out: `frame-ancestors` ignores the path, so
 * `https://app.acme.io/checkout` silently authorises the whole of
 * `https://app.acme.io`, and whoever typed the longer form believes they
 * restricted something they did not.
 */
export function originProblem(value) {
  const origin = String(value || '').trim();
  if (!origin) return 'An origin is required.';
  if (origin === 'null') {
    return "'null' is the origin of a sandboxed or data: document, and would let any such document frame the widget.";
  }
  if (origin.includes('*')) {
    return 'A wildcard origin defeats the boundary the iframe exists to create.';
  }
  if (!/^https?:\/\//i.test(origin)) return 'An origin starts with http:// or https://';

  let parsed;
  try {
    parsed = new URL(origin);
  } catch {
    return 'That is not a URL.';
  }
  if (!parsed.hostname) return 'That origin has no host.';
  if ((parsed.pathname && parsed.pathname !== '/') || parsed.search || parsed.hash) {
    return 'An origin is scheme, host and port only. frame-ancestors ignores the path, so this would authorise the whole host.';
  }
  return null;
}

/**
 * Categories a customer can choose and then not file under.
 *
 * The widget offers whatever is configured here; filing looks up an SLA policy
 * by category and answers "Unknown category" when there is none. The customer
 * has already chosen by then.
 */
export function brokenCategories(categories = []) {
  return categories.filter((entry) => !entry.has_policy).map((entry) => entry.name);
}

/** Origins, normalised the way the server stores them, so the list does not gain
 *  a duplicate that differs only by a trailing slash. */
export function normaliseOrigin(value) {
  return String(value || '').trim().replace(/\/+$/, '');
}
