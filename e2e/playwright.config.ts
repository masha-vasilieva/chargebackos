import { defineConfig, devices } from '@playwright/test';

// Tests run in the installed Google Chrome (channel "chrome"), not a downloaded browser.
// "unit" runs the frontend logic modules inside Chrome; it needs no server.
// "ui" drives the real app. By default it targets the Repl through an SSH tunnel
// (npm run tunnel → http://localhost:18080). Override with BASE_URL.
const BASE_URL = process.env.BASE_URL ?? 'http://localhost:18080';

export default defineConfig({
  testDir: '.',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    ...devices['Desktop Chrome'],
    channel: 'chrome',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'unit', testMatch: /unit\/.*\.spec\.ts/ },
    // The ui spec runs serially: runs share one owner budget on the server.
    { name: 'ui', testMatch: /ui\/.*\.spec\.ts/, fullyParallel: false, use: { baseURL: BASE_URL } },
  ],
});
