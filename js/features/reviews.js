// Reviews feature: load (cancellable), render (escaped), submit + like
// (double-submit guarded). Public GET /reviews has no auth requirement.

import { fetchReviews, submitReview, likeReviewRequest } from '../api.js';
import { friendlyMessage } from '../core/errors.js';
import { getSession } from '../core/state.js';
import { registerActions } from '../ui/actions.js';
import { $ } from '../ui/dom.js';
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

function getReviewMovieId() {
  const value = Number(document.body.dataset.movieId || 1);
  return Number.isFinite(value) && value > 0 ? value : 1;
}

export async function loadReviews() {
  const list = $('reviews-list');
  if (!list) return;
  const requestId = ++loadRequestId;
  try {
    const payload = await fetchReviews();
    if (requestId !== loadRequestId) return;
    const items = Array.isArray(payload) ? payload : (payload && Array.isArray(payload.items) ? payload.items : []);
    allReviews = items.filter((review) => review && typeof review === 'object');
    reviewPage = 0;
    renderReviews();
  } catch {
    if (requestId !== loadRequestId) return;
    allReviews = [];
    list.replaceChildren();
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

  const totalRating = allReviews.reduce((sum, r) => sum + Number(r.rating || 0), 0);
  const avg = (totalRating / allReviews.length).toFixed(1);
  const summary = document.createElement('div');
  summary.className = 'review-summary';

  const avgValue = document.createElement('div');
  avgValue.className = 'rs-avg';
  avgValue.textContent = avg;

  const countValue = document.createElement('div');
  countValue.className = 'rs-count';
  countValue.textContent = `${allReviews.length} USER REVIEW${allReviews.length !== 1 ? 'S' : ''}`;
  summary.append(avgValue, countValue);

  const visibleReviews = allReviews.slice(0, (reviewPage + 1) * REVIEWS_PER_PAGE);
  const cards = visibleReviews.map((review) => {
    const article = document.createElement('article');
    article.className = 'review-card';

    const head = document.createElement('div');
    head.className = 'review-head';

    const who = document.createElement('span');
    who.className = 'review-who';
    const name = document.createElement('strong');
    name.textContent = review.name || 'Anonymous';
    who.appendChild(name);

    if (review.title) {
      const title = document.createElement('span');
      title.className = 'review-title';
      title.textContent = review.title;
      who.appendChild(title);
    }

    const rating = document.createElement('span');
    rating.className = 'review-rating';
    rating.textContent = `${Number(review.rating) || 0}`;
    const outOf = document.createElement('span');
    outOf.className = 'review-outof';
    outOf.textContent = '/10';
    rating.appendChild(outOf);

    head.appendChild(who);
    head.appendChild(rating);

    const meta = document.createElement('div');
    meta.className = 'review-meta';
    meta.textContent = formatDate(review.created_at);

    const body = document.createElement('p');
    body.className = 'review-body';
    if (Boolean(review.spoiler)) {
      const spoilerBadge = document.createElement('div');
      spoilerBadge.className = 'spoiler-badge';
      spoilerBadge.textContent = '⚠ SPOILER WARNING';
      article.appendChild(spoilerBadge);
      body.classList.add('spoiler');
      body.setAttribute('aria-label', 'Spoiler content hidden until revealed');
      body.setAttribute('data-spoiler', 'true');
    }
    body.textContent = review.body || '';

    const actions = document.createElement('button');
    actions.type = 'button';
    actions.className = 'review-like';
    actions.dataset.action = 'like-review';
    actions.dataset.reviewId = String(Number(review.id) || 0);
    actions.setAttribute('aria-label', 'Like this review');
    actions.textContent = `❤ ${Number(review.likes) || 0}`;
    if (Boolean(review.liked)) actions.classList.add('liked');

    article.append(head, meta, body, actions);
    return article;
  });

  list.replaceChildren(summary, ...cards);
  if (loadMore) {
    loadMore.style.display = allReviews.length > (reviewPage + 1) * REVIEWS_PER_PAGE ? 'inline-block' : 'none';
  }
}

function loadMoreReviews() { reviewPage++; renderReviews(); }

async function likeReview(id, button) {
  if (!getSession().token) { requireSignIn(); return; }
  if (isBusy(button)) return;
  button.setAttribute('data-busy', '');
  try {
    const data = await likeReviewRequest(Number(id));
    button.textContent = `❤ ${Number(data.likes) || 0}`;
    button.classList.toggle('liked', Boolean(data.liked));
  } catch {}
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
      await submitReview({
        movie_id: getReviewMovieId(),
        rating,
        title,
        body,
        spoiler: $('r-spoiler').checked,
      });
      errBox.style.display = 'none';
      $('r-title').value = '';
      $('r-body').value = '';
      $('r-rating').value = '';
      $('r-spoiler').checked = false;
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
