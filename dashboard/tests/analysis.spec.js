import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { ANALYSIS_CLEAN, ANALYSIS_MIXED, stubAnalysis } from './stub-api.mjs';

/**
 * Breach analysis: the screen a compliance officer answers a supervisor from.
 *
 * The assertions that matter here are placement ones. Both come from decisions
 * already argued in the backend, and both fail silently if a later change moves
 * a number into its own panel or sorts one list differently.
 */
test.beforeEach(async ({ page }) => {
  await stubAnalysis(page);
  await page.goto('/#/analysis');
  await expect(page.getByRole('heading', { name: 'Breach analysis' })).toBeVisible();
});

test('no WCAG 2.1 AA violations', async ({ page }) => {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(results.violations).toEqual([]);
});

test('deflections sit beside case volume, not in their own panel', async ({ page }) => {
  // A feature that reduces recorded complaints has to be the most heavily
  // instrumented thing in the product: a drop during an outage must be visibly a
  // deflection rather than silently a suppression. Two numbers in two panels are
  // two numbers nobody puts together.
  const cells = page.locator('.ds-summary-cell');
  await expect(cells.nth(0)).toContainText('Complaints recorded');
  await expect(cells.nth(1)).toContainText('Deflected before filing');
});

test('undocumented breaches are separated from the ranked causes', async ({ page }) => {
  // Sorting causes by frequency buries "we don't know" behind whichever incident
  // happened to be biggest. Here it is 9 of 27, behind a 14-case incident.
  await expect(page.getByRole('heading', { name: /9 breaches with no recorded cause/ })).toBeVisible();
  await expect(page.locator('.ds-undocumented')).toHaveClass(/ds-state-breached/);

  const ranked = await page.locator('.ds-causes li').allTextContents();
  expect(ranked.join(' ')).not.toContain('undocumented');
  expect(ranked).toHaveLength(2);
});

test('the undocumented share is quoted as a rate, not just a count', async ({ page }) => {
  // 9 of 27 breaches. A count alone does not say whether it is a rounding error
  // or a third of the period.
  await expect(page.locator('.ds-undocumented')).toContainText('33.3%');
});

test('the worst group sorts first without anybody sorting', async ({ page }) => {
  const keys = await page.locator('.ds-queue-row td:first-child').allTextContents();
  expect(keys).toEqual(['Failed transfer', 'Duplicate charge', 'Failed airtime']);
});

test('a group with no breaches carries no colour', async ({ page }) => {
  const clean = page.locator('.ds-queue-row', { hasText: 'Failed airtime' }).locator('.ds-rate');
  await expect(clean).toHaveClass(/ds-state-comfortable/);
  const fill = await clean
    .locator('.ds-rate-bar')
    .evaluate((node) => getComputedStyle(node).backgroundColor);
  expect(fill).toBe('rgb(216, 216, 214)');
});

test('the bar is never the only encoding of a rate', async ({ page }) => {
  // A bar without its value is a shape rather than a measurement.
  const row = page.locator('.ds-queue-row', { hasText: 'Failed transfer' });
  await expect(row.locator('.ds-rate')).toContainText('11.7%');
});

test('refunds are labelled as recorded rather than paid', async ({ page }) => {
  // §3.3: recorded, never executed. Nothing in this product moves money, and the
  // screen a compliance officer quotes from must not imply otherwise.
  const cell = page.locator('.ds-summary-cell', { hasText: 'Refunds recorded' });
  await expect(cell).toContainText('moves no money');
});

test('a single-currency period shows the currency with the figure', async ({ page }) => {
  const cell = page.locator('.ds-summary-cell', { hasText: 'Refunds recorded' });
  await expect(cell).toContainText('NGN 1,845,000.00');
});

test('a mixed-currency period refuses to show one total', async ({ page }) => {
  // The sum adds kobo to cents. Rendered with a symbol in front of it, that
  // figure gets quoted to a regulator.
  await stubAnalysis(page, ANALYSIS_MIXED);
  await page.reload();
  const cell = page.locator('.ds-summary-cell', { hasText: 'Refunds recorded' });
  await expect(cell).toContainText('Mixed currencies');
  await expect(cell).toContainText('NGN, USD');
  await expect(cell).not.toContainText('1,845,000.00');
});

test('grouping by agent explains why pause time is reported per agent', async ({ page }) => {
  await page.getByLabel('Group by').selectOption('agent');
  await expect(page.getByText(/excessive pausing to be visible in the numbers/)).toBeVisible();
});

test('a period with no breaches says so rather than showing an empty alarm', async ({ page }) => {
  await stubAnalysis(page, ANALYSIS_CLEAN);
  await page.reload();
  await expect(
    page.getByRole('heading', { name: 'Every breach has a recorded cause' })
  ).toBeVisible();
  await expect(page.locator('.ds-undocumented')).not.toHaveClass(/ds-state-breached/);
});

test('the default period is a calendar month, not a rolling window', async ({ page }) => {
  // A supervisor's question is about a period they recognise. A rolling window
  // means the same question asked a day apart gets two different answers with no
  // way to tell which was quoted.
  const from = await page.getByLabel('From').inputValue();
  const to = await page.getByLabel('To').inputValue();
  expect(from).toMatch(/-01$/);
  expect(to).toMatch(/-01$/);
});
