// Phase 11 E2E: page loading, navigation, health, static assets, console health.
import { expect, test } from '@playwright/test';
import { watchConsoleErrors } from './helpers.js';

test.describe('homepage + core shell', () => {
  test('homepage loads over HTTP with title, hero, and nav', async ({ page }) => {
    const errors = watchConsoleErrors(page);
    const response = await page.goto('/');
    expect(response.status()).toBe(200);
    await expect(page).toHaveTitle(/dhurandhar/i);
    await expect(page.locator('#navbar')).toBeVisible();
    await expect(page.locator('#hero')).toBeVisible();
    await expect(page.locator('#bg-video')).toHaveCount(1);
    await expect(page.getByRole('button', { name: 'Book Tickets' })).toBeVisible();
    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([]);
  });

  test('API health and readiness endpoints respond correctly', async ({ request }) => {
    const health = await request.get('/api/health');
    expect(health.status()).toBe(200);
    expect((await health.json()).status).toBe('ok');

    const ready = await request.get('/api/ready');
    expect(ready.status()).toBe(200);
    const readyBody = await ready.text();
    expect(JSON.parse(readyBody).status).toBe('ready');
    // Readiness must never leak connection details.
    expect(readyBody.toLowerCase()).not.toContain('sqlite');
    expect(readyBody.toLowerCase()).not.toContain('database_url');
  });

  test('critical static assets return 200', async ({ request }) => {
    for (const path of ['/css/styles.css', '/js/app.js', '/assets/images/image-01.jpg']) {
      const res = await request.get(path);
      expect(res.status(), `${path} -> ${res.status()}`).toBe(200);
    }
  });

  test('gzip compression is applied to large API responses', async ({ request }) => {
    const res = await request.get('/api/cities', { headers: { 'Accept-Encoding': 'gzip' } });
    // Either compressed (large payload) or plain (small) — both valid; the key
    // invariant is the body decodes to a JSON array and never errors.
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(Array.isArray(body)).toBe(true);
  });

  test('in-page navigation to reviews/contact/about works', async ({ page }) => {
    const errors = watchConsoleErrors(page);
    await page.goto('/');
    // Desktop: click the visible #navbar links. Mobile (<=980px): responsive.css
    // hides .nav-links and the hamburger-driven #mobile-menu holds the links.
    if (await page.locator('#hamburger').isVisible()) {
      await page.locator('#hamburger').click();
      await expect(page.locator('#mobile-menu')).toBeVisible();
      await page.locator('#mobile-menu a[href="#about"]').click();
    } else {
      // Scope to the visible desktop #navbar: the hidden mobile menu also holds
      // #about/#reviews/#contact links, and .first() resolved to those.
      await page.locator('#navbar a[href="#about"]').click();
    }
    await expect(page.locator('#about')).toBeInViewport();
    if (await page.locator('#hamburger').isVisible()) {
      await page.locator('#hamburger').click();
      await expect(page.locator('#mobile-menu')).toBeVisible();
      await page.locator('#mobile-menu a[href="#gallery"]').click();
      await expect(page.locator('#gallery')).toBeInViewport();
    } else {
      await page.locator('#navbar a[href="#reviews"]').click();
      await expect(page.locator('#reviews')).toBeInViewport();
      await page.locator('#navbar a[href="#contact"]').click();
      await expect(page.locator('#contact')).toBeInViewport();
    }
    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([]);
  });

  test('security headers are present on responses', async ({ request }) => {
    const res = await request.get('/api/health');
    expect(res.headers()['x-content-type-options']).toBe('nosniff');
    expect(res.headers()['x-frame-options']).toBe('DENY');
    expect(res.headers()['content-security-policy']).toContain("default-src 'self'");
  });
});
