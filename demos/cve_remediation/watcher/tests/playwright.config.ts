import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  retries: 0,
  timeout: 600_000,
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
  ],
});
