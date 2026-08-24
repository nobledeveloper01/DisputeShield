import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { stubQueue } from './stub-api.mjs';

/**
 * The case view.
 *
 * §10 guarantees structurally that an internal note cannot leak to a customer.
 * The tests here cover the half no serializer can solve: an agent believing they
 * wrote one thing when they wrote another.
 */
test.beforeEach(async ({ page }) => {
  await stubQueue(page);
  await page.goto('/#/cases/dsp_warning');
  await expect(page.getByRole('heading', { name: 'DS-2026-WARN' })).toBeVisible();
});

test('no WCAG 2.1 AA violations', async ({ page }) => {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(results.violations).toEqual([]);
});

test('an internal note carries a label saying it is not visible to the customer', async ({
  page
}) => {
  // The signal that always works: it survives a screen reader, a narrow
  // viewport and a monochrome display, none of which a background colour does.
  const note = page.locator('.ds-message-internal');
  await expect(note).toContainText('Internal note — not visible to the customer');
});

test('internal and customer messages differ in more than one way', async ({ page }) => {
  // Any single signal fails somebody, so DESIGN.md asks for four. Three are
  // checkable here; the fourth is the separate composer, tested below.
  const styles = await page.evaluate(() => {
    const read = (selector) => {
      const node = document.querySelector(selector);
      const style = getComputedStyle(node);
      return {
        background: style.backgroundColor,
        border: style.borderStyle,
        // The measured position, not the property that is supposed to produce
        // it. `margin-left: auto` on a stretched grid item resolves to 0px and
        // moves nothing, so asserting the property passed while the alignment
        // signal did nothing at all.
        left: Math.round(node.getBoundingClientRect().left),
        label: node.querySelector('.ds-message-tag').textContent
      };
    };
    return { internal: read('.ds-message-internal'), customer: read('.ds-message-customer') };
  });

  expect(styles.internal.background).not.toBe(styles.customer.background);
  expect(styles.internal.border).not.toBe(styles.customer.border);
  expect(styles.internal.left).not.toBe(styles.customer.left);
  expect(styles.internal.label).not.toBe(styles.customer.label);
});

test('there are two composers, not one with a visibility toggle', async ({ page }) => {
  // A single box with a dropdown is the design that produces the accident: the
  // agent types, the dropdown is still set from last time, and an internal note
  // goes to a customer.
  await expect(page.getByRole('heading', { name: 'Reply to the customer' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Add an internal note' })).toBeVisible();
  await expect(page.locator('select[name*="visibility" i]')).toHaveCount(0);
});

test('each composer sends its own visibility', async ({ page }) => {
  const notes = page.getByRole('form', { name: 'Add an internal note' });
  await notes.getByRole('textbox').fill('Chasing payments again.');

  const [request] = await Promise.all([
    page.waitForRequest((r) => r.url().includes('/messages/') && r.method() === 'POST'),
    notes.getByRole('button', { name: 'Save note' }).click()
  ]);

  expect(request.postDataJSON()).toEqual({
    body: 'Chasing payments again.',
    visibility: 'internal'
  });
});

test('the clock never scrolls out of view', async ({ page }) => {
  // The whole point of it is that it is always true. One an agent last saw four
  // screens ago is not.
  const position = await page
    .locator('.ds-case-side')
    .evaluate((node) => getComputedStyle(node).position);
  expect(position).toBe('sticky');
});

test('business time is shown under its own label, not as the clock', async ({ page }) => {
  // Time-to-deadline and business time remaining are different quantities. One
  // label over both is how the queue and this page end up contradicting each
  // other on a business-hours policy.
  await expect(page.locator('.ds-clock-large')).toContainText('left');
  await expect(page.getByText(/of working time, under this policy/)).toBeVisible();
});

test('pausing the clock requires a reason', async ({ page }) => {
  // A pausable clock is an abusable clock, and the reason is what makes abuse
  // visible. The button cannot be pressed without one.
  const pause = page.getByRole('button', { name: 'Pause' });
  await expect(pause).toBeDisabled();

  await page.getByLabel('Pause the clock — reason').fill('Awaiting the scheme response.');
  await expect(pause).toBeEnabled();

  const [request] = await Promise.all([
    page.waitForRequest((r) => r.url().includes('/pause/') && r.method() === 'POST'),
    pause.click()
  ]);
  expect(request.postDataJSON()).toEqual({ reason: 'Awaiting the scheme response.' });
});

test('a case from another tenant is indistinguishable from one that does not exist', async ({
  page
}) => {
  await page.route('**/v1/disputes/dsp_elsewhere/', (route) =>
    route.fulfill({ status: 404, json: { error: { type: 'not_found', message: 'No such resource.' } } })
  );
  await page.goto('/#/cases/dsp_elsewhere');
  await expect(page.getByRole('alert')).toContainText('the same answer');
});
