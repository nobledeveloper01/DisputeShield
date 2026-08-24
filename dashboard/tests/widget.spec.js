import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { WIDGET_READONLY, WIDGET_SOUND, stubWidget } from './stub-api.mjs';

/**
 * Widget configuration.
 *
 * The screen's job is the cross-check: a category offered here with no SLA
 * policy behind it lets a customer choose it and then refuses their filing, and
 * nobody on this side of the product finds out. Everything else on the screen is
 * theming, which fails visibly.
 */
test.beforeEach(async ({ page }) => {
  await stubWidget(page);
  await page.goto('/#/widget');
  await expect(page.getByRole('heading', { name: 'Widget', exact: true })).toBeVisible();
});

test('no WCAG 2.1 AA violations', async ({ page }) => {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(results.violations).toEqual([]);
});

test('a category with no policy is the first thing on the screen', async ({ page }) => {
  const warning = page.locator('.ds-broken');
  await expect(warning).toBeVisible();
  await expect(warning).toContainText('1 category cannot be filed under');
  // The stored identifier, matching the editable list the operator is sent to.
  await expect(warning).toContainText('duplicate_charge');
  await expect(warning).toContainText('told the category is unknown');
});

test('the broken-category warning carries no colour', async ({ page }) => {
  // A category with no policy is not a deadline. Stretching "colour is reserved
  // for time" a second time would leave the console with two kinds of red
  // meaning two different things.
  const colours = await page.locator('.ds-broken').evaluate((node) => {
    const style = getComputedStyle(node);
    return [style.borderLeftColor, style.color];
  });
  for (const colour of colours) {
    expect(colour).toBe('rgb(22, 22, 26)');
  }
});

test('a sound configuration shows no warning at all', async ({ page }) => {
  await stubWidget(page, WIDGET_SOUND);
  await page.reload();
  await expect(page.locator('.ds-broken')).toHaveCount(0);
});

test('a policy with no widget category is mentioned as normal, not flagged', async ({ page }) => {
  await expect(page.getByText(/Atm dispense error/)).toBeVisible();
  await expect(page.getByText(/normal if those arrive by another channel/)).toBeVisible();
});

test('the frame-ancestors header is quoted exactly as the browser receives it', async ({
  page
}) => {
  // §11.6: the most common widget support ticket by a wide margin is a domain
  // that was never registered. A screen that paraphrases the header leaves an
  // operator guessing at the line that decides it.
  await expect(
    page.getByText("'self' https://app.acme.test https://checkout.acme.test")
  ).toBeVisible();
});

test('an origin with a path is refused with what it would actually authorise', async ({ page }) => {
  await page.getByLabel('Origin', { exact: true }).fill('https://app.acme.test/checkout');
  await expect(page.getByText(/authorise the whole host/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Register' })).toBeDisabled();
});

test('a wildcard origin is refused', async ({ page }) => {
  await page.getByLabel('Origin', { exact: true }).fill('https://*.acme.test');
  await expect(page.getByText(/wildcard/i)).toBeVisible();
});

test('a trailing slash does not become a second origin', async ({ page }) => {
  await page.getByLabel('Origin', { exact: true }).fill('https://new.acme.test/');
  const [request] = await Promise.all([
    page.waitForRequest((r) => r.url().endsWith('/origins') && r.method() === 'POST'),
    page.getByRole('button', { name: 'Register' }).click()
  ]);
  expect(request.postDataJSON()).toEqual({ origin: 'https://new.acme.test' });
});

test('a colour the browser cannot parse blocks saving and says why', async ({ page }) => {
  await page.getByLabel('Primary colour').fill('cornflower');
  await expect(page.getByText(/unstyled control/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save theme' })).toBeDisabled();
});

test('the preview carries the tenant’s colour, not ours', async ({ page }) => {
  // The one place in this console where a saturated pixel is not about time. The
  // widget's whole point is that it looks like the customer's product.
  const button = page.locator('.ds-preview-button');
  await expect(button).toHaveCSS('background-color', 'rgb(11, 95, 255)');

  await page.getByLabel('Primary colour').fill('#B42318');
  await expect(button).toHaveCSS('background-color', 'rgb(180, 35, 24)');
});

test('the tenant’s colour cannot escape the preview', async ({ page }) => {
  // Scoped to the preview subtree by an inline custom property, so no part of
  // the console inherits a tenant's brand.
  const leaked = await page.evaluate(() =>
    getComputedStyle(document.body).getPropertyValue('--preview-primary').trim()
  );
  expect(leaked).toBe('');
});

test('a role that cannot change origins is told why, not just refused', async ({ page }) => {
  await stubWidget(page, WIDGET_READONLY);
  await page.reload();

  await expect(page.getByRole('button', { name: /^Remove /})).toHaveCount(0);
  await expect(page.getByText(/closer to an account setting than to a theme/)).toBeVisible();
  await expect(page.getByText(/can read this configuration but not change it/)).toBeVisible();
});

test('removing an origin names the origin in the button', async ({ page }) => {
  await expect(
    page.getByRole('button', { name: 'Remove https://app.acme.test' })
  ).toBeVisible();
});
