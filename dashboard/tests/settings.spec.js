import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { MINTED, TEAM_TWO_OWNERS, stubSettings } from './stub-api.mjs';

/**
 * Settings.
 *
 * Every action on this screen is either irreversible or capable of locking
 * somebody out, and none of them is urgent. So the assertions are mostly about
 * whether the consequence is stated *before* the control is used, rather than
 * explained in a refusal afterwards.
 */
test.beforeEach(async ({ page }) => {
  await stubSettings(page);
  await page.goto('/#/settings');
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
});

test('no WCAG 2.1 AA violations', async ({ page }) => {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(results.violations).toEqual([]);
});

test('a key value is never shown in the list, only its prefix', async ({ page }) => {
  // Only a hash is stored, so there is nothing to show. The list must not imply
  // otherwise.
  const table = page.locator('.ds-table').first();
  await expect(table).toContainText('ds_test_41aa…');
  await expect(table).not.toContainText('EXAMPLEVALUE');
});

test('a newly issued key is shown once, and says so', async ({ page }) => {
  await page.getByLabel('Name', { exact: true }).fill('New integration');
  await page.getByRole('button', { name: 'Issue key' }).click();

  const panel = page.locator('.ds-minted');
  await expect(panel).toContainText('Copy this now. It will not be shown again.');
  await expect(panel.locator('.ds-secret')).toHaveText(MINTED.key);
  await expect(panel).toContainText('no way to retrieve it later');
});

test('the issued key is not written anywhere it could be recovered', async ({ page }) => {
  await page.getByLabel('Name', { exact: true }).fill('New integration');
  await page.getByRole('button', { name: 'Issue key' }).click();
  await expect(page.locator('.ds-secret')).toBeVisible();

  const leaked = await page.evaluate(() => ({
    url: window.location.href,
    local: JSON.stringify(window.localStorage),
    session: JSON.stringify(window.sessionStorage)
  }));

  expect(leaked.url).not.toContain('ds_test_7b1d');
  expect(leaked.local).not.toContain('ds_test_7b1d');
  expect(leaked.session).not.toContain('ds_test_7b1d');
});

test('dismissing the panel removes the key from the page', async ({ page }) => {
  await page.getByLabel('Name', { exact: true }).fill('New integration');
  await page.getByRole('button', { name: 'Issue key' }).click();
  await page.getByRole('button', { name: 'I have copied it' }).click();

  await expect(page.locator('.ds-minted')).toHaveCount(0);
  await expect(page.getByText(MINTED.key)).toHaveCount(0);
});

test('revoking the key this session is using warns before it happens', async ({ page }) => {
  // Correct if the key has leaked, surprising otherwise.
  page.on('dialog', async (dialog) => {
    expect(dialog.message()).toMatch(/next request will fail/);
    await dialog.dismiss();
  });
  await page.getByRole('button', { name: 'Revoke CI' }).click();
});

test('revoking the only live key warns what it stops', async ({ page }) => {
  const messages = [];
  page.on('dialog', async (dialog) => {
    messages.push(dialog.message());
    await dialog.dismiss();
  });
  await page.getByRole('button', { name: 'Revoke Production backend' }).click();
  expect(messages.join(' ')).toMatch(/every live integration/);
});

test('the only active owner cannot be changed, and the reason is on the control', async ({
  page
}) => {
  // A tenant with no owner cannot mint a key, change a role or register an
  // origin, and there is no way back from that state.
  const roleSelect = page.getByLabel('Role for adaeze@example.test');
  await expect(roleSelect).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Deactivate adaeze@example.test' })).toBeDisabled();
  await expect(page.getByText(/only active owner/)).toBeVisible();
});

test('a second owner unblocks the first', async ({ page }) => {
  await stubSettings(page, { team: TEAM_TWO_OWNERS });
  await page.reload();

  await expect(
    page.getByRole('button', { name: 'Deactivate second@example.test' })
  ).toBeEnabled();
});

test('you cannot change your own role even when others could', async ({ page }) => {
  await stubSettings(page, { team: TEAM_TWO_OWNERS });
  await page.reload();

  // Adaeze is `is_you` in the fixture.
  await expect(page.getByLabel('Role for adaeze@example.test')).toBeDisabled();
  await expect(page.getByText(/your own role/)).toBeVisible();
});

test('changing another member’s role sends a PATCH', async ({ page }) => {
  const [request] = await Promise.all([
    page.waitForRequest((r) => r.url().includes('/v1/agents/') && r.method() === 'PATCH'),
    page.getByLabel('Role for ngozi@example.test').selectOption('compliance')
  ]);
  expect(request.postDataJSON()).toEqual({ role: 'compliance' });
});

test('retention is reported and offers no way to change it', async ({ page }) => {
  // A tenant able to shorten its own retention below the mandated period would
  // be using a settings screen to fall out of compliance.
  const block = page.locator('.ds-settings-block', { hasText: 'Retention' });
  await expect(block).toContainText('7 years');
  await expect(block).toContainText('regulatory floor rather than a preference');
  await expect(block.locator('input, select, button')).toHaveCount(0);
});

test('a case past its window is explained rather than left looking lost', async ({ page }) => {
  const block = page.locator('.ds-settings-block', { hasText: 'Retention' });
  await expect(block).toContainText('12');
  await expect(block).toContainText('deletes only when told to');
});

test('legal holds are shown beside the window, not elsewhere', async ({ page }) => {
  // Retention and a hold point in opposite directions, and the hold wins.
  const block = page.locator('.ds-settings-block', { hasText: 'Retention' });
  await expect(block).toContainText('Active legal holds');
  await expect(block).toContainText('The hold wins');
});

test('SSO is declared unavailable rather than offered as a dead form', async ({ page }) => {
  const block = page.locator('.ds-settings-block', { hasText: 'Sign-in' });
  await expect(block).toContainText('not available in this release');
  await expect(block.locator('input, select')).toHaveCount(0);
});

test('nothing on the settings screen carries colour', async ({ page }) => {
  // Colour is reserved for time, and nothing here is a deadline — including the
  // panel that shows a credential once.
  await page.getByLabel('Name', { exact: true }).fill('New integration');
  await page.getByRole('button', { name: 'Issue key' }).click();

  const border = await page
    .locator('.ds-minted')
    .evaluate((node) => getComputedStyle(node).borderLeftColor);
  expect(border).toBe('rgb(22, 22, 26)');
});
