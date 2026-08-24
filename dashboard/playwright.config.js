import { defineConfig, devices } from '@playwright/test';

/**
 * The dashboard's API is stubbed here, and that is a deliberate scope line.
 *
 * These tests exist to assert two things about the *surface*: that a keyboard-
 * only compliance officer can operate it, and that it has no accessibility
 * violations. Neither needs a real database, and standing one up would make the
 * suite slow enough that people learn to skip it — which is how an accessibility
 * gate stops being a gate.
 *
 * The API contract underneath is covered by the Python suite, which drives the
 * same endpoints through real authentication, real roles and real RLS.
 */
export default defineConfig({
  testDir: 'tests',
  testMatch: '*.spec.js',
  timeout: 30_000,
  fullyParallel: false,
  reporter: process.env.CI ? 'github' : 'list',
  use: { baseURL: 'http://localhost:4190', trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npm run preview',
    url: 'http://localhost:4190',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000
  }
});
