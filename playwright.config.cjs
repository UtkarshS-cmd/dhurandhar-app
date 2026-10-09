// Playwright configuration for the Dhurandhar Cinema E2E suite.
//
// The tests run against a *real* FastAPI process (the single server that also
// serves the static frontend), started by the global-setup webServer below.
// The app uses an isolated SQLite test database via E2E_DATABASE_URL so no
// development or production data is ever touched.
//
// Chromium only: the smallest reliable matrix for a vanilla-JS site. Payment
// flows are exercised exclusively through the MOCK provider (PAYMENT_MODE=mock),
// never a real provider or a real charge.
//
// NOTE: CommonJS (.cjs) because ESM (.mjs) config loading hangs on this
// Node + @playwright/test combo (verified by bisection).
const { defineConfig, devices } = require('@playwright/test');
const path = require('path');

const PORT = process.env.E2E_PORT || 5077;
const BASE_URL = process.env.E2E_BASE_URL || `http://127.0.0.1:${PORT}`;

module.exports = defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    actionTimeout: 15_000,
    navigationTimeout: 20_000,
  },
  projects: [
    { name: 'desktop-chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } } },
    { name: 'mobile-chromium', use: { ...devices['Pixel 7'] } },
  ],
  webServer: {
    // Isolated E2E SQLite DB, mock payments, permissive-but-explicit CORS.
    // Cross-platform Node launcher (spawns uvicorn from backend/); the old
    // .cmd wrapper only worked on Windows. E2E_PYTHON overrides the
    // interpreter for CI, where no backend/.venv exists.
    command: `node "${path.join(__dirname, 'e2e', 'e2e-server.cjs')}"`,
    cwd: '.',
    url: `${BASE_URL}/api/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    env: {
      DATABASE_URL: process.env.E2E_DATABASE_URL || 'sqlite:///./dhurandhar_e2e_test.db',
      JWT_SECRET: process.env.E2E_JWT_SECRET || 'e2e-only-secret-not-used-in-production-000000',
      APP_ENV: 'development',
      PAYMENT_MODE: 'mock',
      // Every browser test registers/logs in from 127.0.0.1; with limits on,
      // the mobile project would trip the register anti-flood counter (8/h)
      // mid-run. Backend pytest keeps limits ON (default) and covers them.
      RATE_LIMIT_ENABLED: 'false',
      CORS_ORIGINS: `http://localhost:${PORT},http://127.0.0.1:${PORT}`,
    },
  },
});
