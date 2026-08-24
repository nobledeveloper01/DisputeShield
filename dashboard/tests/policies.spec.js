import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { stubPolicies } from './stub-api.mjs';

/**
 * SLA policies.
 *
 * The screen's job is to make the versioning true in the interface, not only in
 * the database. An officer who leaves believing they edited a setting has the
 * wrong model of the system in the one situation that matters — a supervisor
 * asking what standard a case was judged against.
 */
test.beforeEach(async ({ page }) => {
  await stubPolicies(page);
  await page.goto('/#/policies');
  await expect(page.getByRole('heading', { name: 'SLA policies' })).toBeVisible();
});

test('no WCAG 2.1 AA violations', async ({ page }) => {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(results.violations).toEqual([]);
});

test('the button says what publishing does, and does not say "Save"', async ({ page }) => {
  // "Save" is the word that teaches the wrong model.
  await expect(page.getByRole('button', { name: 'Publish version 3' })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Save/ })).toHaveCount(0);
});

test('the screen states that filed cases keep their version', async ({ page }) => {
  await expect(page.getByText(/Cases already filed keep version 2/)).toBeVisible();
  await expect(page.getByText(/the standard they were judged against does not move/)).toBeVisible();
});

test('publishing is unavailable until something actually changes', async ({ page }) => {
  // A no-op version is a row in the change history that a reviewer has to read
  // and discard.
  const publish = page.getByRole('button', { name: 'Publish version 3' });
  await expect(publish).toBeDisabled();

  await page.getByLabel(/Resolution window/).fill('96');
  await expect(publish).toBeEnabled();
});

test('the change history is beside the terms, not behind a tab', async ({ page }) => {
  const history = page.locator('.ds-history');
  await expect(history).toBeVisible();
  await expect(history).toContainText('Resolution window');
  await expect(history).toContainText('72 → 168');
});

test('the first version reads as a first version rather than an empty diff', async ({ page }) => {
  await expect(page.locator('.ds-history')).toContainText('The first version of this policy');
});

test('terms that cannot describe a window are explained before the request', async ({ page }) => {
  await page.getByLabel(/Resolution window/).fill('0');
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('breaches every case the moment it is filed');
  await expect(page.getByRole('button', { name: /^Publish version/ })).toBeDisabled();
});

test('an escalation at 100% is refused with the reason', async ({ page }) => {
  await page.getByLabel(/Escalate at/).fill('100');
  await expect(page.getByRole('alert')).toContainText('below 100%');
});

test('a warning threshold at 100 is called out as one that never fires', async ({ page }) => {
  await page.getByLabel(/Warning thresholds/).fill('50, 100');
  await expect(page.getByRole('alert')).toContainText('never fires');
});

test('the calendar is not editable from inside a policy', async ({ page }) => {
  // A calendar is shared by several policies, and editing it from inside one of
  // them hides that change from the others.
  const calendar = page.getByLabel('Business calendar');
  await expect(calendar).toBeDisabled();
  await expect(page.getByText(/Changed on the calendar itself/)).toBeVisible();
});

test('a publish sends the terms as a PATCH', async ({ page }) => {
  await page.getByLabel(/Resolution window/).fill('96');
  const [request] = await Promise.all([
    page.waitForRequest((r) => r.url().includes('/sla-policies/') && r.method() === 'PATCH'),
    page.getByRole('button', { name: 'Publish version 3' }).click()
  ]);
  expect(request.postDataJSON()).toMatchObject({
    resolution_hours: 96,
    warning_thresholds: [50, 80, 95],
    regulatory_reference: 'CBN 2020 §3.1'
  });
});

test('switching policy is keyboard operable and marks the current one', async ({ page }) => {
  const airtime = page.getByRole('button', { name: /Failed airtime/ });
  await airtime.press('Enter');
  await expect(airtime).toHaveAttribute('aria-current', 'true');
});
