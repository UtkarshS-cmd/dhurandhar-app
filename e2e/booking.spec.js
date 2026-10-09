// Phase 11 E2E: the ticket wizard — validation, catalog navigation, seat
// selection and a full mock-provider checkout against the isolated E2E DB.
// The catalog (cities/shows/seats) is seeded idempotently in beforeAll.
import { expect, test } from '@playwright/test';
import { makeUser, openBooking, seedCatalog, watchConsoleErrors } from './helpers.js';

test.describe('booking wizard', () => {
  test.beforeAll(async () => {
    seedCatalog();
  });

  test('opens with step 1 active and four step tabs', async ({ page }) => {
    const errors = watchConsoleErrors(page);
    await page.goto('/');
    await openBooking(page);
    await expect(page.locator('.modal-step-tab')).toHaveCount(4);
    await expect(page.locator('#stab1')).toHaveClass(/active/);
    await expect(page.locator('#mpanel1')).toHaveClass(/active/);
    await expect(page.locator('#f-name')).toBeVisible();
    // Catalog loads on init: cities and dates must be populated from seed.
    await expect(page.locator('#f-city option')).not.toHaveCount(1);
    await expect(page.locator('#f-date option')).not.toHaveCount(1);
    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([]);
  });

  test('rejects empty guest details and stays on step 1', async ({ page }) => {
    await page.goto('/');
    await openBooking(page);
    await page.locator('[data-action="booking-step1"]').click();
    await expect(page.locator('#f-name')).toHaveClass(/error/);
    await expect(page.locator('#f-email')).toHaveClass(/error/);
    await expect(page.locator('#f-phone')).toHaveClass(/error/);
    await expect(page.locator('#mpanel1')).toHaveClass(/active/);
    await expect(page.locator('#mpanel2')).not.toHaveClass(/active/);
  });

  test('books seats end-to-end through the mock provider', async ({ page }) => {
    const errors = watchConsoleErrors(page);
    const user = makeUser('bkflow');
    await page.goto('/');
    await openBooking(page);

    // Step 1 — valid guest details (also creates the account at hold time).
    await page.locator('#f-name').fill(user.full_name);
    await page.locator('#f-email').fill(user.email);
    await page.locator('#f-phone').fill(user.phone);
    await page.locator('#f-pwd').fill(user.password);
    await page.locator('#f-cpwd').fill(user.password);
    await page.locator('[data-action="booking-step1"]').click();
    await expect(page.locator('#mpanel2')).toHaveClass(/active/);

    // Step 2 — city → tomorrow (index 2: placeholder + today + tomorrow) →
    // theater card → show time.
    await page.locator('#f-city').selectOption({ index: 1 });
    await page.locator('#f-date').selectOption({ index: 2 });
    const card = page.locator('.theater-card').first();
    await expect(card).toBeVisible();
    await card.click();
    const show = page.locator('.show-time').first();
    await expect(show).toBeEnabled();
    await show.click();
    await page.locator('[data-action="booking-step2"]').click();
    await expect(page.locator('#mpanel3')).toHaveClass(/active/);

    // Step 3 — default 2 tickets / Silver: pick two bookable silver seats.
    const seats = page.locator('#silver-seats .seat:not([disabled])');
    await expect(seats.first()).toBeVisible();
    await seats.nth(0).click();
    await seats.nth(1).click();
    await expect(page.locator('#silver-seats .seat.selected-seat')).toHaveCount(2);
    await page.locator('[data-action="booking-step3"]').click();
    await expect(page.locator('#mpanel4')).toHaveClass(/active/);
    await expect(page.locator('#summaryBox .summary-row').first()).toBeVisible();
    await expect(page.locator('#summaryBox')).toContainText('SERVER TOTAL');

    // Step 4 — agree + confirm. PAYMENT_MODE=mock settles synchronously, and
    // success is only rendered because the BACKEND answered CONFIRMED.
    await page.locator('#f-agree').check();
    await page.locator('[data-action="confirm-booking"]').click();
    await expect(page.locator('#mpanel5')).toHaveClass(/active/);
    await expect(page.locator('#bookingRef')).toHaveText(/^DHR-/);
    await expect(page.locator('#mpanel5 .success-sub')).toContainText('confirmed');
    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([]);
  });
});
