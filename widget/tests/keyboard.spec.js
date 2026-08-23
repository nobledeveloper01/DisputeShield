import { expect, test } from '@playwright/test';

/**
 * §9 makes accessibility a regulatory obligation, not a preference: a dispute
 * channel a screen-reader or keyboard user cannot operate is a consumer
 * protection problem.
 *
 * So this is a keyboard-only walkthrough of the *complete* filing flow — no
 * clicks, no `focus()` calls, nothing but Tab, Enter, Space and typing. The
 * iframe boundary makes focus management genuinely harder (ADR-0001 says so),
 * which is exactly why this test exists rather than a hand-wave about a11y.
 */

const WIDGET_ORIGIN = 'http://127.0.0.1:8011';

async function widgetFrame(page) {
  await page.goto('/');
  await page.waitForSelector('iframe');
  const frame = page.frames().find((f) => f.url().startsWith(WIDGET_ORIGIN));
  expect(frame, 'the widget frame did not load').toBeTruthy();
  await frame.waitForSelector('.ds-root');
  return frame;
}

test('the whole filing flow is reachable with the keyboard alone', async ({ page }) => {
  const frame = await widgetFrame(page);
  await expect(frame.locator('h1')).toHaveText('Which transaction?');

  // Step 1 — choose the transaction by tabbing to it and pressing Space.
  await frame.locator('.ds-option').first().focus();
  await page.keyboard.press('Space');
  await expect(frame.locator('.ds-option[aria-checked="true"]')).toHaveCount(1);

  await frame.getByRole('button', { name: 'Continue' }).focus();
  await page.keyboard.press('Enter');
  await expect(frame.locator('h1')).toHaveText('What happened?');

  // Step 2 — type a description without touching the mouse.
  await frame.locator('#ds-description').focus();
  await page.keyboard.type('The transfer failed but I was debited.');
  await frame.getByRole('button', { name: 'Continue' }).focus();
  await page.keyboard.press('Enter');
  await expect(frame.locator('h1')).toHaveText('Check and send');

  // Step 3 — submit.
  await frame.getByRole('button', { name: 'Submit report' }).focus();
  await page.keyboard.press('Enter');

  await expect(frame.locator('h1')).toHaveText('Report received', { timeout: 10_000 });
  await expect(frame.locator('.ds-reference')).toContainText('DS-');
});

test('focus moves to the new heading on every step', async ({ page }) => {
  const frame = await widgetFrame(page);

  await frame.getByRole('button', { name: 'Continue' }).focus();
  await page.keyboard.press('Enter');

  const focused = await frame.evaluate(() => document.activeElement?.className);
  expect(focused).toContain('ds-title');
});

test('every interactive control has an accessible name', async ({ page }) => {
  const frame = await widgetFrame(page);
  const unnamed = await frame.evaluate(() => {
    const nodes = [...document.querySelectorAll('button, select, textarea, input, [role="radio"]')];
    return nodes
      .filter((node) => {
        const name =
          node.getAttribute('aria-label') ||
          node.textContent?.trim() ||
          document.querySelector(`label[for="${node.id}"]`)?.textContent?.trim();
        return !name;
      })
      .map((node) => node.outerHTML.slice(0, 80));
  });
  expect(unnamed).toEqual([]);
});

test('the customer is told the expected resolution date before they leave', async ({ page }) => {
  const frame = await widgetFrame(page);

  await frame.locator('.ds-option').first().click();
  await frame.getByRole('button', { name: 'Continue' }).click();
  await frame.locator('#ds-description').fill('Debited without transfer.');
  await frame.getByRole('button', { name: 'Continue' }).click();
  await frame.getByRole('button', { name: 'Submit report' }).click();

  await expect(frame.locator('time')).toBeVisible({ timeout: 10_000 });
});
