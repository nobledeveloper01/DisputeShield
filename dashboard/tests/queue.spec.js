import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { stubQueue } from './stub-api.mjs';

/**
 * The queue, and the sentence the whole product is built around:
 *
 *   "I could see which case breaches next without reading anything."
 *
 * Most of what follows checks that claim mechanically. A queue that looks fine
 * while failing it fails invisibly, which is exactly why it is asserted rather
 * than reviewed.
 */
test.beforeEach(async ({ page }) => {
  await stubQueue(page);
  await page.goto('/#/');
  await expect(page.getByRole('heading', { name: 'Queue' })).toBeVisible();
});

test('no WCAG 2.1 AA violations, with every clock state on screen', async ({ page }) => {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(results.violations).toEqual([]);
});

test('the breached case is first, and the client does not re-sort', async ({ page }) => {
  // The server sorts by urgency against an index, and that ordering is asserted
  // server-side. Re-sorting here would quietly replace a tested guarantee with
  // an untested one, so the rows must arrive and stay in the order they came.
  const references = await page.locator('.ds-queue-row a').allTextContents();
  expect(references[0]).toBe('DS-2026-BREACH');
  expect(references).toEqual([
    'DS-2026-BREACH',
    'DS-2026-CRIT',
    'DS-2026-WARN',
    'DS-2026-NOTE',
    'DS-2026-PAUSE',
    'DS-2026-CALM'
  ]);
});

test('a breached case reads as breached, never as a negative number', async ({ page }) => {
  const row = page.locator('.ds-queue-row', { hasText: 'DS-2026-BREACH' });
  await expect(row.locator('.ds-clock')).toContainText('BREACHED');
  await expect(row.locator('.ds-clock')).toContainText('2h 14m ago');
  await expect(row.locator('.ds-clock')).not.toContainText('-');
});

test('a paused case reads as paused, not as a comfortable one', async ({ page }) => {
  const row = page.locator('.ds-queue-row', { hasText: 'DS-2026-PAUSE' });
  await expect(row.locator('.ds-clock')).toContainText('PAUSED');
  await expect(row.locator('.ds-clock')).toHaveClass(/ds-state-paused/);
  await expect(row.locator('.ds-clock')).not.toHaveClass(/ds-state-comfortable/);
});

test('a healthy row is monochrome', async ({ page }) => {
  // A healthy queue is monochrome: the desired resting state, and what makes the
  // first spot of colour genuinely the thing to look at.
  const clock = page
    .locator('.ds-queue-row', { hasText: 'DS-2026-CALM' })
    .locator('.ds-clock');
  await expect(clock).toHaveClass(/ds-state-comfortable/);
  const colour = await clock.evaluate((node) => {
    const style = getComputedStyle(node);
    return { color: style.color, background: style.backgroundColor };
  });
  expect(colour.color).toBe('rgb(22, 22, 26)');
  expect(['rgba(0, 0, 0, 0)', 'transparent']).toContain(colour.background);
});

test('only the critical row moves', async ({ page }) => {
  // The critical pulse is the sole exception to "under 150ms or not at all".
  const animated = await page
    .locator('.ds-clock')
    .evaluateAll((nodes) =>
      nodes
        .filter((node) => getComputedStyle(node).animationName !== 'none')
        .map((node) => node.className)
    );
  expect(animated).toHaveLength(1);
  expect(animated[0]).toContain('ds-state-critical');
});

test('reduced motion removes the pulse and keeps the signal', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.reload();
  const critical = page.locator('.ds-clock.ds-state-critical').first();
  await expect(critical).toBeVisible();
  expect(await critical.evaluate((n) => getComputedStyle(n).animationName)).toBe('none');
  await expect(critical).toContainText('CRITICAL');
});

test('rows are dense enough to see twenty without scrolling', async ({ page }) => {
  const heights = await page
    .locator('.ds-queue-row')
    .evaluateAll((rows) => rows.map((row) => Math.round(row.getBoundingClientRect().height)));
  for (const height of heights) {
    expect(height).toBeGreaterThanOrEqual(40);
    expect(height).toBeLessThanOrEqual(52);
  }
});

test('the reference is the link, not the whole row', async ({ page }) => {
  // A row-wide click target makes selecting a reference to paste into a
  // regulator's email impossible.
  const row = page.locator('.ds-queue-row').first();
  await expect(row).not.toHaveAttribute('href', /./);
  await expect(row.getByRole('link', { name: 'DS-2026-BREACH' })).toBeVisible();
});

test('the queue is reachable and filterable by keyboard', async ({ page }) => {
  await page.getByLabel('Risk').selectOption('breached');
  await expect(page.getByLabel('Risk')).toHaveValue('breached');
  await page.getByRole('link', { name: 'DS-2026-BREACH' }).press('Enter');
  await expect(page.getByRole('heading', { name: 'DS-2026-WARN' })).toBeVisible();
});
