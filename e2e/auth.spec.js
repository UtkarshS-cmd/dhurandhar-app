// Phase 11 E2E: auth states — register/login/logout via the real UI dialog,
// avatar identity, unauthenticated profile defaults, and malformed-input
// rejection. Runs against the isolated E2E database with no real credentials.
import { expect, test } from '@playwright/test';
import { loginInBrowser, makeUser, registerUser, watchConsoleErrors } from './helpers.js';

test.describe('authentication flows', () => {
  // The nav button has the only stable identity: #nav-auth-btn. getByRole
  // 'Sign In' matches three elements (nav + hidden modal close/login buttons).
  const openAuthDialog = (page) => page.locator('#nav-auth-btn').click();

  test('register through the UI signs in and shows an avatar button', async ({ page, baseURL }) => {
    const errors = watchConsoleErrors(page);
    const user = makeUser('uireg');
    await page.goto('/');
    await openAuthDialog(page);
    await page.locator('#authtab-register').click();
    await page.locator('#ar-name').fill(user.full_name);
    await page.locator('#ar-email').fill(user.email);
    await page.locator('#ar-phone').fill(user.phone);
    await page.locator('#ar-pwd').fill(user.password);
    await page.locator('[data-action="auth-register"]').click();
    // Confirmation beat: success copy appears, then the dialog closes (~500ms).
    await expect(page.locator('#ar-ok')).toContainText('Account created', { timeout: 10_000 });
    // #auth-modal hides via opacity (css/booking.css), not display:none, so
    // Playwright's toBeHidden() never matches — assert the .open class contract.
    await expect(page.locator('#auth-modal')).not.toHaveClass(/\bopen\b/, { timeout: 10_000 });
    // Nav flips from "Sign In" to an initials avatar, and keys persist.
    const navBtn = page.locator('#nav-auth-btn');
    await expect(navBtn).toHaveClass(/profile-avatar/);
    await expect(navBtn).toHaveAttribute('aria-label', 'Open account menu');
    const token = await page.evaluate(() => localStorage.getItem('dhurandhar_token'));
    const stored = await page.evaluate(() => localStorage.getItem('dhurandhar_user'));
    expect(token, 'bearer token persisted').toBeTruthy();
    expect(JSON.parse(stored).email).toBe(user.email.toLowerCase());
    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([]);
  });

  test('login through the UI and sign-out returns to Sign In', async ({ page, baseURL }) => {
    const errors = watchConsoleErrors(page);
    const { user } = await registerUser(baseURL, 'uilogin');
    await page.goto('/');
    await openAuthDialog(page);
    await page.locator('#al-email').fill(user.email);
    await page.locator('#al-pwd').fill(user.password);
    await page.locator('[data-action="auth-login"]').click();
    await expect(page.locator('#auth-modal')).not.toHaveClass(/\bopen\b/, { timeout: 10_000 });
    const navBtn = page.locator('#nav-auth-btn');
    await expect(navBtn).toHaveClass(/profile-avatar/);
    // Sign out via the profile dropdown (the profile modal has its own hidden
    // logout button, so scope to the dropdown container).
    await navBtn.click();
    await page.locator('#profile-menu [data-action="logout"]').click();
    await expect(page.locator('#nav-auth-btn')).toHaveText('Sign In');
    const token = await page.evaluate(() => localStorage.getItem('dhurandhar_token'));
    expect(token, 'token cleared on logout').toBeNull();
    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([]);
  });

  test('invalid login shows an error and stays signed out', async ({ page }) => {
    const errors = watchConsoleErrors(page);
    await page.goto('/');
    await openAuthDialog(page);
    await page.locator('#al-email').fill('nobody@example.com');
    await page.locator('#al-pwd').fill('Wrong@1234');
    await page.locator('[data-action="auth-login"]').click();
    await expect(page.locator('#al-err')).not.toBeEmpty({ timeout: 10_000 });
    // Dialog stays open (.open class) and the nav still offers Sign In.
    await expect(page.locator('#auth-modal')).toHaveClass(/\bopen\b/);
    await expect(page.locator('#nav-auth-btn')).toHaveText('Sign In');
    // The deliberate 401 logs a resource error; everything else must be clean.
    const unexpected = errors.filter((e) => !e.includes('401'));
    expect(unexpected, `console errors: ${unexpected.join(' | ')}`).toEqual([]);
  });

  test('weak registration input is rejected client-side without a request', async ({ page }) => {
    const errors = watchConsoleErrors(page);
    let apiCalls = 0;
    await page.route('**/api/auth/register', async (route) => { apiCalls += 1; await route.continue(); });
    await page.goto('/');
    await openAuthDialog(page);
    await page.locator('#authtab-register').click();
    await page.locator('#ar-name').fill('Al');
    await page.locator('#ar-email').fill('not-an-email');
    await page.locator('#ar-phone').fill('123');
    await page.locator('#ar-pwd').fill('short');
    await page.locator('[data-action="auth-register"]').click();
    await expect(page.locator('#ar-err')).not.toBeEmpty({ timeout: 10_000 });
    expect(apiCalls, 'no request leaves the browser for invalid input').toBe(0);
    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([]);
  });

  test('API-seeded session renders signed-in shell on reload', async ({ page, baseURL }) => {
    const errors = watchConsoleErrors(page);
    const { token, account } = await registerUser(baseURL, 'uiseed');
    await loginInBrowser(page, { token, account });
    await expect(page.locator('#nav-auth-btn')).toHaveClass(/profile-avatar/);
    await expect(page.locator('#review-login-cta')).toBeHidden();
    await expect(page.locator('#review-write-wrap')).toBeVisible();
    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([]);
  });

  test('API rejects bad credentials with uniform 401 (no user enumeration)', async ({ request, baseURL }) => {
    const { user } = await registerUser(baseURL, 'uienum');
    const wrong = await request.post('/api/auth/login', {
      data: { email: user.email, password: 'Wrong@1234' },
    });
    const unknown = await request.post('/api/auth/login', {
      data: { email: 'unknown-enum@example.com', password: 'Test@1234' },
    });
    expect(wrong.status()).toBe(401);
    expect(unknown.status()).toBe(401);
    expect(await wrong.text()).toBe(await unknown.text());
  });
});
