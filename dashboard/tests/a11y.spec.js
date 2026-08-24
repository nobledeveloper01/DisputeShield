import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { stubApi } from './stub-api.mjs';

/**
 * §9 makes accessibility a regulatory obligation rather than a preference, and
 * a compliance officer who cannot operate this screen cannot prove what the firm
 * did.
 */
test.beforeEach(async ({ page }) => {
  await stubApi(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Report delivery' })).toBeVisible();
});

test('no WCAG 2.1 AA violations, with every health state on screen', async ({ page }) => {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();

  expect(results.violations).toEqual([]);
});

test('colour is never the only encoding of a schedule’s state', async ({ page }) => {
  // Roughly one in twelve men has a colour vision deficiency, so every state a
  // fill communicates also has to be readable as text. Asserted by finding the
  // labels rather than by inspecting the CSS, which would pass if the label were
  // rendered invisibly.
  for (const label of ['UP TO DATE', 'DUE', 'OVERDUE', 'NOT DELIVERED', 'PAUSED']) {
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  }
});

test('the failed schedule is first without anybody sorting', async ({ page }) => {
  // The sort order is the design. A compliance officer should not have to sort
  // this list to find the return that did not go out.
  const first = page.locator('.ds-schedule').first();
  await expect(first).toContainText('Delta abandoned a month');
  await expect(first).toContainText('Nothing will retry these');
});

test('a deactivated schedule does not read as a comfortable one', async ({ page }) => {
  const paused = page.locator('.ds-schedule', { hasText: 'Echo deactivated schedule' });
  await expect(paused).toHaveClass(/ds-state-paused/);
  await expect(paused).not.toHaveClass(/ds-state-current/);
  await expect(paused.getByText('PAUSED')).toBeVisible();
});

test('every form control has a programmatic label', async ({ page }) => {
  const controls = page.locator('input:not([type=hidden])');
  const count = await controls.count();
  expect(count).toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    const control = controls.nth(index);
    const name = await control.evaluate((node) => {
      const explicit = node.labels?.[0]?.textContent?.trim();
      return explicit || node.getAttribute('aria-label') || '';
    });
    expect(name, `control ${index} has no label`).not.toEqual('');
  }
});

test('every cell in a table row shares its row’s bottom edge', async ({ page }) => {
  // A `display: flex` on a `<td>` stops it being a table cell, so it drops out of
  // the row's height calculation and its bottom border draws above the rest of
  // the divider. That happened here, it looked like a small alignment quirk, and
  // it survived two plausible fixes aimed at the cell's height — which was never
  // the problem. Measured rather than eyeballed, because a 3px break is exactly
  // the kind of thing a screenshot review waves through.
  const rows = await page.locator('.ds-table tbody tr').evaluateAll((trs) =>
    trs.map((tr) => [...tr.children].map((td) => Math.round(td.getBoundingClientRect().bottom)))
  );
  expect(rows.length).toBeGreaterThan(0);
  for (const bottoms of rows) {
    expect(new Set(bottoms).size, `row cells end at ${bottoms.join(', ')}`).toBe(1);
  }
});
