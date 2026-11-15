import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: 'parcel-flow.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 5_000 },
  reporter: [
    ['list'],
    ['json', { outputFile: 'test-results/results.json' }],
    ['html', { open: 'never' }],
  ],
  metadata: {
    appURL: process.env.APP_BASE_URL ?? 'http://127.0.0.1:5173',
    apiURL: process.env.API_BASE_URL ?? 'http://127.0.0.1:8000/api/v1',
    target: 'integrated-product',
  },
  use: {
    baseURL: process.env.APP_BASE_URL ?? 'http://127.0.0.1:5173',
    browserName: 'chromium',
    channel: process.env.PLAYWRIGHT_CHANNEL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    serviceWorkers: 'block',
  },
  projects: [
    { name: 'desktop-1440', use: { viewport: { width: 1440, height: 900 } } },
    { name: 'laptop-1280', use: { viewport: { width: 1280, height: 720 } } },
  ],
});
