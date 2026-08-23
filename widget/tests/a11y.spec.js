import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

/**
 * axe-core against the widget's own document, at every step of the flow.
 *
 * Scanning only the first screen is the common mistake: the steps a customer
 * reaches after a decision are the ones nobody looks at, and they are where a
 * missing label or a broken contrast ratio survives.
 */

const WIDGET_ORIGIN = 'http://127.0.0.1:8011';

test('the filing flow is free of axe violations at every step', async ({ page }) => {
  await page.goto('/');
  await page.waitForSelector('iframe');
  const frame = page.frames().find((f) => f.url().startsWith(WIDGET_ORIGIN));
  await frame.waitForSelector('.ds-root');

  const steps = [
    { name: 'transaction' },
    { name: 'detail', before: async () => frame.getByRole('button', { name: 'Continue' }).click() },
    {
      name: 'review',
      before: async () => {
        await frame.locator('#ds-description').fill('Debited without transfer.');
        await frame.getByRole('button', { name: 'Continue' }).click();
      }
    }
  ];

  for (const step of steps) {
    if (step.before) await step.before();
    const results = await new AxeBuilder({ page })
      .include('iframe')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();

    const violations = results.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`);
    expect(violations, `axe violations on the ${step.name} step`).toEqual([]);
  }
});
