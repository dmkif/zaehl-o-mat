import { defineConfig } from '@playwright/test'
import { rig } from './lib/rig'

// One worker: the fake IdP is a single global stand-in whose "claims" mode picks the user of the next sign-in,
// and the tests share one rig database. The whole suite runs in about a minute, parallelism is not needed.
export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  outputDir: 'test-results',
  use: {
    baseURL: rig().proxyUrl,
    ignoreHTTPSErrors: true, // throw-away self-signed certificate of rig/browser_proxy.py
    locale: 'de-DE',
    timezoneId: 'Europe/Berlin',
    viewport: { width: 1280, height: 800 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
})
