// Phase 11 E2E: admin RBAC surface — the nav entry is hidden for regular
// users, the dashboard renders for role=ADMIN, and the API enforces RBAC
// server-side regardless of what the UI shows. Promotion happens only in the
// isolated E2E SQLite database.
import { expect, request, test } from '@playwright/test';
import { createAdminSession, loginInBrowser, registerUser, watchConsoleErrors } from './helpers.js';

test.describe('admin dashboard', () => {
  test('hides the Admin entry point for regular users', async ({ page, baseURL }) => {
    const errors = watchConsoleErrors(page);
    const { token, account } = await registerUser(baseURL, 'adm-plain');
    await loginInBrowser(page, { token, account });
    await page.goto('/');
    await expect(page.locator('#nav-auth-btn')).toHaveClass(/profile-avatar/); // signed in
    await expect(page.locator('#nav-admin-btn')).toBeHidden();
    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([]);
  });

  test('admin API refuses non-admin and anonymous callers', async ({ baseURL }) => {
    const { token, api } = await registerUser(baseURL, 'adm-api');
    const asUser = await api.get('/api/admin/dashboard', {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(asUser.status(), await asUser.text()).toBe(403);
    const anonApi = await request.newContext({ baseURL });
    const asAnonymous = await anonApi.get('/api/admin/dashboard');
    expect(asAnonymous.status()).toBe(401);
  });

  test('admin session sees the entry point and dashboard stats', async ({ page, baseURL }) => {
    const errors = watchConsoleErrors(page);
    const { token, account } = await createAdminSession(baseURL, 'adm-ui');
    await loginInBrowser(page, { token, account });
    await page.goto('/');
    const navBtn = page.locator('#nav-admin-btn');
    await expect(navBtn).toBeVisible();
    await navBtn.click();
    await expect(page.locator('#admin-modal')).toHaveClass(/\bopen\b/);
    // Dashboard renders ten stat cards once /api/admin/dashboard responds.
    await expect(page.locator('#admin-content .admin-card').first()).toBeVisible();
    await expect(page.locator('#admin-content')).toContainText('Users');
    await expect(page.locator('#admin-content')).toContainText('Revenue');
    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([]);
  });
});
