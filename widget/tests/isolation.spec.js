import { expect, test } from '@playwright/test';

/**
 * ADR-0001, asserted in a real browser.
 *
 * The claim is that the boundary is enforced by the browser rather than by our
 * discipline, and that a customer's engineer can verify it in devtools in ten
 * seconds. These tests are that verification, written down.
 *
 * Both directions matter and they fail differently:
 *   - the host reading the widget would expose a session token;
 *   - the widget reading the host would expose the account number a customer is
 *     typing on a page that handles money.
 */

const WIDGET_ORIGIN = 'http://127.0.0.1:8011';

test.beforeEach(async ({ page }) => {
  await page.goto('/');
  await page.waitForSelector('iframe');
});

test('the widget renders in a cross-origin iframe', async ({ page }) => {
  const frame = page.locator('iframe');
  await expect(frame).toHaveAttribute('sandbox', 'allow-scripts allow-forms allow-same-origin');
  const src = await frame.getAttribute('src');
  expect(src.startsWith(WIDGET_ORIGIN)).toBe(true);
});

test('the host page cannot read the iframe document', async ({ page }) => {
  const reachable = await page.evaluate(() => {
    const frame = document.querySelector('iframe');
    try {
      // Cross-origin: the browser returns null rather than the document.
      return {
        contentDocument: frame.contentDocument !== null,
        body: Boolean(frame.contentWindow.document?.body)
      };
    } catch (e) {
      return { contentDocument: false, body: false, threw: e.name };
    }
  });
  expect(reachable.contentDocument).toBe(false);
  expect(reachable.body).toBe(false);
});

test('the host page cannot read the widget window globals', async ({ page }) => {
  const leaked = await page.evaluate(() => {
    const frame = document.querySelector('iframe');
    const attempts = {};
    for (const name of ['__disputeshield_session__', 'React', 'fetch', 'localStorage']) {
      try {
        attempts[name] = typeof frame.contentWindow[name];
      } catch (e) {
        attempts[name] = `blocked:${e.name}`;
      }
    }
    return attempts;
  });

  // Every access is either blocked outright or returns undefined. What must
  // never happen is a defined value coming back.
  for (const [name, result] of Object.entries(leaked)) {
    expect(result === 'undefined' || String(result).startsWith('blocked:')).toBe(true);
  }
});

test('the widget cannot reach the host page globals', async ({ page }) => {
  const frame = page.frames().find((f) => f.url().startsWith(WIDGET_ORIGIN));
  expect(frame, 'the widget frame did not load').toBeTruthy();

  const result = await frame.evaluate(() => {
    const out = {};
    try {
      out.secret = window.parent.HOST_SECRET;
    } catch (e) {
      out.secret = `blocked:${e.name}`;
    }
    try {
      out.dom = window.parent.document.getElementById('account').value;
    } catch (e) {
      out.dom = `blocked:${e.name}`;
    }
    try {
      out.cookie = window.parent.document.cookie;
    } catch (e) {
      out.cookie = `blocked:${e.name}`;
    }
    return out;
  });

  expect(String(result.secret)).toMatch(/^blocked:|undefined/);
  expect(String(result.dom)).toMatch(/^blocked:/);
  expect(String(result.cookie)).toMatch(/^blocked:/);
});

test('the widget cannot read the host page cookies or storage', async ({ page }) => {
  const frame = page.frames().find((f) => f.url().startsWith(WIDGET_ORIGIN));
  const result = await frame.evaluate(() => {
    // The widget's own cookie jar and storage are its own origin's, so the
    // host's values must simply not be there.
    return {
      cookie: document.cookie,
      storage: (() => {
        try {
          return localStorage.getItem('host_token');
        } catch (e) {
          return `blocked:${e.name}`;
        }
      })()
    };
  });
  expect(result.cookie).not.toContain('super-secret-value');
  expect(String(result.storage)).not.toContain('host-storage-secret');
});

test('a postMessage from an unrelated origin does not move the widget', async ({ page }) => {
  const before = await page.evaluate(() => document.querySelector('iframe').style.width);

  await page.evaluate(() => {
    // Same page, so `event.source` will not be the widget's window — which is
    // the second half of the loader's check, and the half a naive origin-only
    // check would miss.
    window.postMessage(
      {
        source: 'disputeshield-widget',
        version: 1,
        payload: { type: 'resize', width: 999, height: 999 }
      },
      window.location.origin
    );
  });
  await page.waitForTimeout(150);

  const after = await page.evaluate(() => document.querySelector('iframe').style.width);
  expect(after).toBe(before);
});

test('the embed document carries a per-tenant frame-ancestors', async ({ request }) => {
  const response = await request.get(
    `${WIDGET_ORIGIN}/v1/embed?k=pk_test_e2e_0000000000000000000000000000`,
    { headers: { referer: 'http://localhost:4180/' } }
  );
  expect(response.status()).toBe(200);
  const csp = response.headers()['content-security-policy'];
  expect(csp).toContain('frame-ancestors http://localhost:4180');
  expect(csp).toContain("default-src 'none'");
  expect(csp).not.toContain('unsafe-inline');
});

test('an unregistered origin is refused', async ({ request }) => {
  const response = await request.get(
    `${WIDGET_ORIGIN}/v1/embed?k=pk_test_e2e_0000000000000000000000000000`,
    { headers: { referer: 'https://attacker.example/' } }
  );
  expect(response.status()).toBe(403);
  expect(await response.text()).toBe('');
});
