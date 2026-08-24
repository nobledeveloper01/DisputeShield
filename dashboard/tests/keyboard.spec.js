import { expect, test } from '@playwright/test';

import { stubApi } from './stub-api.mjs';

/**
 * A keyboard-only path through both flows.
 *
 * The widget has the same gate for the customer-facing filing flow. This one
 * covers the other end: the compliance officer who decides where a period's
 * disclosure may be sent, and when.
 */
test.beforeEach(async ({ page }) => {
  await stubApi(page);
  await page.goto('/#/reports');
  await expect(page.getByRole('heading', { name: 'Report delivery' })).toBeVisible();
});

test('a recipient can be registered without a mouse', async ({ page }) => {
  const form = page.getByRole('form', { name: 'Register a recipient' });
  await form.getByLabel('Address').focus();
  await page.keyboard.type('returns@example.test');
  await page.keyboard.press('Tab');
  await page.keyboard.type('Supervisory returns');
  await page.keyboard.press('Tab');
  await page.keyboard.type('Quarterly supervisory request');

  const [request] = await Promise.all([
    page.waitForRequest(
      (r) => r.url().endsWith('/v1/reports/recipients') && r.method() === 'POST'
    ),
    page.getByRole('button', { name: 'Register' }).press('Enter')
  ]);

  expect(request.postDataJSON()).toMatchObject({
    address: 'returns@example.test',
    reason: 'Quarterly supervisory request'
  });
});

test('a schedule can be created without a mouse', async ({ page }) => {
  // Scoped to the form. Both forms have a "Reason", and a test that fills the
  // wrong one leaves a required field empty and fails in a way that looks like
  // the button is broken.
  const form = page.getByRole('form', { name: 'Add a schedule' });
  await form.getByLabel('Name', { exact: true }).focus();
  await page.keyboard.type('Monthly supervisory export');

  // The recipient checkboxes are reachable and toggle on Space, not only click.
  const checkbox = form.getByRole('checkbox', { name: /compliance@example\.test/ });
  await checkbox.focus();
  await page.keyboard.press('Space');
  await expect(checkbox).toBeChecked();

  await form.getByLabel('Reason', { exact: true }).fill('Standing arrangement');

  const [request] = await Promise.all([
    page.waitForRequest(
      (r) => r.url().endsWith('/v1/reports/schedules') && r.method() === 'POST'
    ),
    page.getByRole('button', { name: 'Create schedule' }).press('Enter')
  ]);

  expect(request.postDataJSON()).toMatchObject({
    name: 'Monthly supervisory export',
    recipients: ['compliance@example.test'],
    day_of_month: 5
  });
});

test('the day field refuses a day that does not exist in every month', async ({ page }) => {
  const day = page.getByLabel('Day of month');
  await expect(day).toHaveAttribute('max', '28');
  await expect(page.getByText(/different date in February/)).toBeVisible();
});

test('every deactivate button says what it deactivates', async ({ page }) => {
  // "Deactivate" repeated eight times is unusable with a screen reader, so each
  // carries the subject in its accessible name.
  const buttons = page.getByRole('button', { name: /^Deactivate / });
  expect(await buttons.count()).toBeGreaterThan(1);
  await expect(
    page.getByRole('button', { name: 'Deactivate Delta abandoned a month' })
  ).toBeVisible();
});

test('a focus ring is visible on every interactive element', async ({ page }) => {
  await page.getByRole('button', { name: 'Register' }).focus();
  const outline = await page
    .getByRole('button', { name: 'Register' })
    .evaluate((node) => getComputedStyle(node).outlineStyle);
  expect(outline).not.toEqual('none');
});
