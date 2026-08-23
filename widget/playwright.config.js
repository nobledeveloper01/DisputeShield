import { defineConfig, devices } from '@playwright/test';

/**
 * Two origins, on purpose. The host fixture is served from
 * http://localhost:4180 and the widget from http://127.0.0.1:8011 — different
 * host *and* different port, so the browser treats them as cross-origin exactly
 * as it would treat a fintech's domain and ours.
 *
 * Running both on one origin would make every assertion here pass while testing
 * nothing, which is the failure mode this comment exists to prevent.
 */
export default defineConfig({
  testDir: 'tests',
  timeout: 30_000,
  fullyParallel: false,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: 'http://localhost:4180',
    trace: 'retain-on-failure'
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      // The widget's own origin. Started here so the browser suite is one
      // command locally and in CI, rather than a runbook step somebody forgets.
      command:
        'cd .. && .venv/bin/python manage.py migrate --no-input ' +
        '&& .venv/bin/python manage.py disputeshield_seed_e2e ' +
        '&& .venv/bin/python manage.py runserver 127.0.0.1:8011 --noreload',
      url: 'http://127.0.0.1:8011/healthz',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: 'pipe',
      stderr: 'pipe'
    },
    {
      command: 'node tests/serve-host.mjs',
      url: 'http://localhost:4180',
      reuseExistingServer: !process.env.CI,
      env: { HOST_PORT: '4180', WIDGET_ORIGIN: 'http://127.0.0.1:8011' }
    }
  ]
});
