// Reviews feature: load (cancellable), render (escaped), submit + like
// (double-submit guarded). Public GET /reviews has no auth requirement.

import { fetchReviews, submitReview, likeReviewRequest } from '../api.js';
import { friendlyMessage } from '../core/errors.js';
import { getSession } from '../core/state.js';
import { registerActions } from '../ui/actions.js';
import { $, escapeHtml } from '../ui/dom.js';
import { runExclusive, isBusy } from '../ui/loading.js';
import { toast } from '../ui/notifications.js';

const REVIEWS_PER_PAGE = 5;

let allReviews = [];
let reviewPage = 0;
let loadRequestId = 0;
let initDone = false;

/** Sign-in hook: features/auth.js owns the modal; resolve it lazily. */
let requireSignIn = () => {};
export function setRequireSignIn(fn) { requireSignIn = fn; }

function formatDate(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

export async function loadReviews() {
  const list = $('reviews-list');
  if (!list) return;
  const requestId = ++loadRequestId;
  try {
    const reviews = await fetchReviews();
    if (requestId !== loadRequestId) return; // a newer load owns the UI now
    allReviews = Array.isArray(reviews) ? reviews : [];
    reviewPage = 0;
    renderReviews();
  } catch {
    if (requestId !== loadRequestId) return;
    allReviews = [];
    list.replaceChildren(); // safe text node, no interpolation of errors
    const msg = document.createElement('div');
    msg.className = 'async-state error';
    msg.textContent = 'Reviews are temporarily unavailable.';
    list.appendChild(msg);
  }
}

function renderReviews() {
  const list = $('reviews-list');
  if (!list) return;
  const loadMore = $('load-more-reviews');
  if (!allReviews.length) {
    const empty = document.createElement('div');
    empty.className = 'async-state';
    empty.textContent = 'No reviews yet — be the first!';
    list.replaceChildren(empty);
    if (loadMore) loadMore.style.display = 'none';
    return;
  }
  const avg = (allReviews.reduce((sum, r) => sum + Number(r.rating), 0) / allReviews.length).toFixed(1);
  const summary = `<div class="review-summary">
      <div class="rs-avg">${avg}</div>
      <div class="rs-count">${allReviews.length} USER REVIEW${allReviews.length !== 1 ? 'S' : ''}</div>
    </div>`;
  // Every dynamic value below is escaped or numeric — reviews are
  // user-generated content and never touch innerHTML raw.
  const cards = allReviews.slice(0, (reviewPage + 1) * REVIEWS_PER_PAGE).map((r) => `<article class="review-card">
      <div class="review-head">
        <span class="review-who"><strong>${escapeHtml(r.name)}</strong>${r.title ? `<span class="review-title">${escapeHtml(r.title)}</span>` : ''}</span>
        <span class="review-rating">${Number(r.rating) || 0}<span class="review-outof">/10</span></span>
      </div>
      <div class="review-meta">${formatDate(r.created_at)}</div>
      ${r.spoiler ? '<div class="spoiler-badge">⚠ SPOILER WARNING</div>' : ''}
      <p>${escapeHtml(r.body)}</p>
      <button type="button" class="review-like" data-action="like-review" data-review-id="${Number(r.id) || 0}" aria-label="Like this review">❤ ${Number(r.likes) || 0}</button>
    </article>`).join('');
  list.innerHTML = summary + cards;
  if (loadMore) {
    loadMore.style.display = allReviews.length > (reviewPage + 1) * REVIEWS_PER_PAGE ? 'inline-block' : 'none';
  }
}

function loadMoreReviews() { reviewPage++; renderReviews(); }

async function likeReview(id, button) {
  if (!getSession().token) { requireSignIn(); return; }
  if (isBusy(button)) return; // rapid clicks cannot double-fire the toggle
  button.setAttribute('data-busy', '');
  try {
    const data = await likeReviewRequest(Number(id));
    button.textContent = `❤ ${Number(data.likes) || 0}`;
    button.classList.toggle('liked', Boolean(data.liked));
  } catch { /* likes are non-critical; stay silent like the original */ }
  finally { button.removeAttribute('data-busy'); }
}

async function submitReviewForm() {
  if (!getSession().token) { requireSignIn(); return; }
  const rating = Number($('r-rating').value);
  const title = $('r-title').value.trim();
  const body = $('r-body').value.trim();
  const errBox = $('r-err');
  if (rating < 1 || rating > 10 || title.length < 2 || body.length < 2) {
    errBox.textContent = 'Please provide a rating, title and review.';
    errBox.style.display = 'block';
    return;
  }
  const btn = document.querySelector('[data-action="submit-review"]');
  await runExclusive(btn, 'Posting…', async () => {
    try {
      await submitReview({ rating, title, body, spoiler: $('r-spoiler').checked });
      errBox.style.display = 'none';
      $('r-title').value = '';
      $('r-body').value = '';
      $('r-rating').value = '';
      toast('Review posted.', 'success');
      await loadReviews();
    } catch (error) {
      errBox.textContent = friendlyMessage(error);
      errBox.style.display = 'block';
    }
  });
}

export function initReviews() {
  if (initDone) return;
  initDone = true;
  registerActions({
    'load-more-reviews': loadMoreReviews,
    'like-review': (event, target) => likeReview(target.dataset.reviewId, target),
    'submit-review': submitReviewForm,
  });
  loadReviews();
}
