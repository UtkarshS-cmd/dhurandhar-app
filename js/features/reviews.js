// Reviews feature: load (server-driven, cancellable), render (XSS-safe),
// submit + like + edit + delete (all double-submit guarded).
// Public GET /reviews has no auth requirement.
// All network calls go through api.js → core/api-client.js. No raw fetch().

import {
  fetchReviews, submitReview, updateReview, deleteReview, likeReviewRequest,
} from '../api.js';
import { friendlyMessage } from '../core/errors.js';
import { getSession, onSessionChange } from '../core/state.js';
import { registerActions } from '../ui/actions.js';
import { $ } from '../ui/dom.js';
import { runExclusive, isBusy } from '../ui/loading.js';
import { openModal, closeModal } from '../ui/modal.js';
import { toast } from '../ui/notifications.js';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let currentPage = 1;
const PAGE_SIZE = 10;
let currentSort = 'newest';
let hasMore = false;
let loadRequestId = 0;
let initDone = false;
let editingReviewId = null;

/** Sign-in hook: features/auth.js owns the modal; resolve it lazily. */
let requireSignIn = () => {};
export function setRequireSignIn(fn) { requireSignIn = fn; }

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function formatDate(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function getReviewMovieId() {
  const value = Number(document.body.dataset.movieId || 1);
  return Number.isFinite(value) && value > 0 ? value : 1;
}

function currentUserId() {
  const session = getSession();
  return session && session.user && session.user.id ? session.user.id : null;
}

// ---------------------------------------------------------------------------
// Server-driven loading
// ---------------------------------------------------------------------------

export async function loadReviews(page = 1, sort = currentSort) {
  const list = $('reviews-list');
  if (!list) return;

  const requestId = ++loadRequestId;
  currentPage = page;
  currentSort = sort;

  try {
    const payload = await fetchReviews({ page, limit: PAGE_SIZE, sort });
    if (requestId !== loadRequestId) return; // stale — a newer request is running

    hasMore = Boolean(payload && payload.has_more);
    const items = (payload && Array.isArray(payload.items)) ? payload.items : [];
    const total = (payload && payload.total) || 0;
    const avgRating = (payload && payload.average_rating) || 0;

    renderReviews(items, total, avgRating, page);
  } catch (err) {
    if (requestId !== loadRequestId) return;
    list.replaceChildren();
    const msg = document.createElement('div');
    msg.className = 'async-state error';
    msg.textContent = 'Reviews are temporarily unavailable.';
    list.appendChild(msg);
  }
}

// ---------------------------------------------------------------------------
// Rendering (all text set via textContent — never innerHTML)
// ---------------------------------------------------------------------------

function renderReviews(items, total, avgRating, page) {
  const list = $('reviews-list');
  if (!list) return;

  const loadMoreBtn = $('load-more-reviews');
  if (!items.length && page === 1) {
    const empty = document.createElement('div');
    empty.className = 'async-state';
    empty.textContent = 'No reviews yet — be the first!';
    list.replaceChildren(empty);
    if (loadMoreBtn) loadMoreBtn.style.display = 'none';
    return;
  }

  // Summary bar
  const summary = document.createElement('div');
  summary.className = 'review-summary';

  if (avgRating && total) {
    const avgEl = document.createElement('div');
    avgEl.className = 'rs-avg';
    avgEl.textContent = Number(avgRating).toFixed(1);

    const countEl = document.createElement('div');
    countEl.className = 'rs-count';
    countEl.textContent = `${total} USER REVIEW${total !== 1 ? 'S' : ''}`;
    summary.append(avgEl, countEl);
  }

  const uid = currentUserId();
  const cards = items.map((review) => buildReviewCard(review, uid));

  if (page === 1) {
    list.replaceChildren(summary, ...cards);
  } else {
    // Append additional page cards; update/replace summary
    const existing = list.querySelector('.review-summary');
    if (existing) list.replaceChild(summary, existing);
    cards.forEach((c) => list.appendChild(c));
  }

  if (loadMoreBtn) {
    loadMoreBtn.style.display = hasMore ? 'inline-block' : 'none';
  }
}

function buildReviewCard(review, uid) {
  const article = document.createElement('article');
  article.className = 'review-card';
  article.dataset.reviewId = String(review.id);

  // Head row
  const head = document.createElement('div');
  head.className = 'review-head';

  const who = document.createElement('span');
  who.className = 'review-who';
  const name = document.createElement('strong');
  name.textContent = review.name || 'Anonymous';
  who.appendChild(name);

  if (review.title) {
    const titleEl = document.createElement('span');
    titleEl.className = 'review-title';
    titleEl.textContent = review.title;
    who.appendChild(titleEl);
  }

  const rating = document.createElement('span');
  rating.className = 'review-rating';
  rating.textContent = String(Number(review.rating) || 0);
  const outOf = document.createElement('span');
  outOf.className = 'review-outof';
  outOf.textContent = '/10';
  rating.appendChild(outOf);

  head.append(who, rating);

  // Meta
  const meta = document.createElement('div');
  meta.className = 'review-meta';
  meta.textContent = formatDate(review.created_at);

  // Body / spoiler
  const body = document.createElement('p');
  body.className = 'review-body';

  if (Boolean(review.spoiler)) {
    const spoilerWrap = document.createElement('div');
    spoilerWrap.className = 'spoiler-wrap';

    const spoilerBadge = document.createElement('div');
    spoilerBadge.className = 'spoiler-badge';
    spoilerBadge.textContent = '⚠ SPOILER WARNING';

    const revealBtn = document.createElement('button');
    revealBtn.type = 'button';
    revealBtn.className = 'spoiler-reveal-btn';
    revealBtn.dataset.action = 'toggle-spoiler';
    revealBtn.dataset.reviewId = String(review.id);
    revealBtn.setAttribute('aria-expanded', 'false');
    revealBtn.textContent = 'Reveal spoiler';

    body.classList.add('spoiler-hidden');
    body.setAttribute('aria-hidden', 'true');
    body.textContent = review.body || '';

    spoilerWrap.append(spoilerBadge, revealBtn, body);
    article.append(head, meta, spoilerWrap);
  } else {
    body.textContent = review.body || '';
    article.append(head, meta, body);
  }

  // Actions row
  const actionsRow = document.createElement('div');
  actionsRow.className = 'review-actions';

  const likeBtn = document.createElement('button');
  likeBtn.type = 'button';
  likeBtn.className = 'review-like';
  likeBtn.dataset.action = 'like-review';
  likeBtn.dataset.reviewId = String(review.id);
  likeBtn.setAttribute('aria-label', `Like this review (${Number(review.likes) || 0} likes)`);
  likeBtn.textContent = `❤ ${Number(review.likes) || 0}`;
  if (Boolean(review.liked)) likeBtn.classList.add('liked');
  actionsRow.appendChild(likeBtn);

  // Owner controls (trust backend for authorization; show based on session user id)
  if (uid && review.user_id === uid) {
    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'review-edit-btn';
    editBtn.dataset.action = 'edit-review';
    editBtn.dataset.reviewId = String(review.id);
    editBtn.textContent = 'Edit';
    actionsRow.appendChild(editBtn);

    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'review-delete-btn';
    delBtn.dataset.action = 'delete-review';
    delBtn.dataset.reviewId = String(review.id);
    delBtn.textContent = 'Delete';
    actionsRow.appendChild(delBtn);
  }

  article.appendChild(actionsRow);
  return article;
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

function loadMoreReviews() {
  if (!hasMore) return;
  loadReviews(currentPage + 1, currentSort);
}

async function likeReview(id, button) {
  if (!getSession().token) { requireSignIn(); return; }
  if (isBusy(button)) return;
  button.setAttribute('data-busy', '');
  try {
    const data = await likeReviewRequest(Number(id));
    button.textContent = `❤ ${Number(data.likes) || 0}`;
    button.setAttribute('aria-label', `Like this review (${Number(data.likes) || 0} likes)`);
    button.classList.toggle('liked', Boolean(data.liked));
  } catch { /* silently ignore — could show toast */ } finally {
    button.removeAttribute('data-busy');
  }
}

function toggleSpoiler(id, button) {
  const card = button.closest('[data-review-id]');
  if (!card) return;
  const bodyEl = card.querySelector('.review-body');
  if (!bodyEl) return;
  const expanded = button.getAttribute('aria-expanded') === 'true';
  if (expanded) {
    bodyEl.classList.add('spoiler-hidden');
    bodyEl.setAttribute('aria-hidden', 'true');
    button.setAttribute('aria-expanded', 'false');
    button.textContent = 'Reveal spoiler';
  } else {
    bodyEl.classList.remove('spoiler-hidden');
    bodyEl.setAttribute('aria-hidden', 'false');
    button.setAttribute('aria-expanded', 'true');
    button.textContent = 'Hide spoiler';
  }
}

function openEditModal(id) {
  const card = document.querySelector(`[data-review-id="${id}"]`);
  if (!card) return;
  const titleEl = card.querySelector('.review-title');
  const ratingEl = card.querySelector('.review-rating');
  const bodyEl = card.querySelector('.review-body');

  // Populate form
  const ratingInput = $('r-rating');
  const titleInput = $('r-title');
  const bodyInput = $('r-body');
  const spoilerInput = $('r-spoiler');
  if (ratingInput) ratingInput.value = ratingEl ? ratingEl.textContent.replace('/10', '').trim() : '';
  if (titleInput) titleInput.value = titleEl ? titleEl.textContent : '';
  if (bodyInput) bodyInput.value = bodyEl ? bodyEl.textContent : '';
  if (spoilerInput) spoilerInput.checked = card.querySelector('.spoiler-badge') !== null;

  editingReviewId = id;

  // Switch submit button label if possible
  const submitBtn = document.querySelector('[data-action="submit-review"]');
  if (submitBtn) submitBtn.textContent = 'Save changes';

  // Scroll to form
  const form = document.querySelector('.review-form, #review-form');
  if (form) form.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function cancelEdit() {
  editingReviewId = null;
  const submitBtn = document.querySelector('[data-action="submit-review"]');
  if (submitBtn) submitBtn.textContent = 'Post review';
  const errBox = $('r-err');
  if (errBox) errBox.style.display = 'none';
}

async function deleteReviewAction(id, button) {
  if (!getSession().token) { requireSignIn(); return; }
  if (isBusy(button)) return;

  // Accessible confirmation using a prompt-style approach
  // Use the existing modal if available, otherwise fallback to a simple confirm
  const confirmEl = $('confirm-modal');
  let confirmed = false;
  if (confirmEl) {
    confirmed = await new Promise((resolve) => {
      const msg = confirmEl.querySelector('.confirm-msg');
      if (msg) msg.textContent = 'Delete this review? This cannot be undone.';
      const okBtn = confirmEl.querySelector('[data-action="confirm-ok"]');
      const cancelBtn = confirmEl.querySelector('[data-action="confirm-cancel"]');
      const onOk = () => { cleanup(); resolve(true); };
      const onCancel = () => { cleanup(); resolve(false); };
      const cleanup = () => {
        okBtn && okBtn.removeEventListener('click', onOk);
        cancelBtn && cancelBtn.removeEventListener('click', onCancel);
        closeModal(confirmEl);
      };
      okBtn && okBtn.addEventListener('click', onOk, { once: true });
      cancelBtn && cancelBtn.addEventListener('click', onCancel, { once: true });
      openModal(confirmEl);
    });
  } else {
    // eslint-disable-next-line no-alert
    confirmed = window.confirm('Delete this review? This cannot be undone.');
  }

  if (!confirmed) return;

  button.setAttribute('data-busy', '');
  try {
    await deleteReview(Number(id));
    // Remove card from DOM
    const card = document.querySelector(`[data-review-id="${id}"]`);
    if (card) card.remove();
    // If editing this review, cancel edit mode
    if (editingReviewId === Number(id)) cancelEdit();
    toast('Review deleted.', 'success');
    // Refresh to update aggregate stats
    await loadReviews(1, currentSort);
  } catch (err) {
    toast(friendlyMessage(err), 'error');
  } finally {
    button.removeAttribute('data-busy');
  }
}

async function submitReviewForm() {
  if (!getSession().token) { requireSignIn(); return; }
  const ratingInput = $('r-rating');
  const titleInput = $('r-title');
  const bodyInput = $('r-body');
  const spoilerInput = $('r-spoiler');
  const errBox = $('r-err');

  const rating = Number(ratingInput ? ratingInput.value : 0);
  const title = titleInput ? titleInput.value.trim() : '';
  const body = bodyInput ? bodyInput.value.trim() : '';
  const spoiler = Boolean(spoilerInput && spoilerInput.checked);

  if (rating < 1 || rating > 10 || title.length < 1 || body.length < 1) {
    if (errBox) {
      errBox.textContent = 'Please provide a rating (1–10), a title, and a review body.';
      errBox.style.display = 'block';
    }
    return;
  }

  const btn = document.querySelector('[data-action="submit-review"]');
  await runExclusive(btn, editingReviewId ? 'Saving…' : 'Posting…', async () => {
    try {
      if (editingReviewId) {
        // PATCH existing review
        await updateReview(editingReviewId, { rating, title, body, spoiler });
        cancelEdit();
        toast('Review updated.', 'success');
      } else {
        // POST new review
        await submitReview({
          movie_id: getReviewMovieId(),
          rating,
          title,
          body,
          spoiler,
        });
        toast('Review posted.', 'success');
      }
      if (errBox) errBox.style.display = 'none';
      if (ratingInput) ratingInput.value = '';
      if (titleInput) titleInput.value = '';
      if (bodyInput) bodyInput.value = '';
      if (spoilerInput) spoilerInput.checked = false;
      await loadReviews(1, currentSort);
    } catch (error) {
      if (errBox) {
        errBox.textContent = friendlyMessage(error);
        errBox.style.display = 'block';
      }
    }
  });
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

export function initReviews() {
  if (initDone) return;
  initDone = true;
  registerActions({
    'load-more-reviews': loadMoreReviews,
    'like-review': (event, target) => likeReview(target.dataset.reviewId, target),
    'toggle-spoiler': (event, target) => toggleSpoiler(target.dataset.reviewId, target),
    'edit-review': (event, target) => openEditModal(Number(target.dataset.reviewId)),
    'delete-review': (event, target) => deleteReviewAction(Number(target.dataset.reviewId), target),
    'submit-review': submitReviewForm,
    'cancel-edit-review': cancelEdit,
  });
  // Re-render when the signed-in user changes: owner controls (Edit/Delete)
  // and per-user like state depend on session identity, and this list may
  // have been fetched before sign-in (or after sign-out). The initDone guard
  // above keeps this listener registered exactly once.
  let renderedUserId = currentUserId();
  onSessionChange(() => {
    const uid = currentUserId();
    if (uid === renderedUserId) return;
    renderedUserId = uid;
    loadReviews(1, currentSort);
  });
  loadReviews(1, 'newest');
}
