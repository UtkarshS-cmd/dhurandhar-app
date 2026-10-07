// Admin dashboard feature (vanilla ES module, Phase 8).
// UX-only role gate: controls render iff /api/me says role == ADMIN.
// Every /api/admin/* endpoint re-checks the DB role server-side.
// All dynamic content uses textContent (never innerHTML).

import {
  adminAuditLogs, adminBookings, adminContacts, adminDashboard, adminDeactivateUser,
  adminActivateUser, adminDeleteReview, adminMarkContact, adminMovies, adminCreateMovie,
  adminUpdateMovie, adminPaymentAttempt, adminPayments, adminReviews, adminShows,
  adminCancelShow, adminSubscribers, adminSetUserRole, adminUsers, fetchMe,
} from '../api.js';
import { friendlyMessage } from '../core/errors.js';
import { getSession, onSessionChange } from '../core/state.js';
import { registerActions } from '../ui/actions.js';
import { $, el } from '../ui/dom.js';
import { runExclusive } from '../ui/loading.js';
import { closeModal, openModal } from '../ui/modal.js';
import { toast } from '../ui/notifications.js';

let initDone = false;
let currentTab = 'dashboard';
let isAdmin = false;
let adminChecked = false;
const PAGE_SIZE = 15;
const tabs = [
  ['dashboard', 'Dashboard'], ['users', 'Users'], ['movies', 'Movies'],
  ['shows', 'Shows'], ['bookings', 'Bookings'], ['payments', 'Payments'],
  ['reviews', 'Reviews'], ['messages', 'Messages'], ['audit', 'Audit'],
];

function modal() { return $('admin-modal'); }
function body() { return $('admin-body'); }
export function isAdminSession() { return isAdmin; }

function setStatus(message, isError = false) {
  const node = $('admin-status');
  if (node) {
    node.textContent = message || '';
    node.className = isError ? 'async-state error' : 'async-state';
  }
}

function badge(text, kind) {
  return el('span', { class: `admin-badge ${kind || ''}`.trim(), text: String(text) });
}

function pager(total, page, onPage) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const wrap = el('div', { class: 'admin-pager' }, [
    el('button', { type: 'button', class: 'modal-btn-ghost', text: '← Prev' }),
    el('span', { class: 'admin-page', text: `Page ${page} of ${pages} (${total})` }),
    el('button', { type: 'button', class: 'modal-btn-ghost', text: 'Next →' }),
  ]);
  const [prev, , next] = wrap.children;
  if (page <= 1) prev.disabled = true;
  if (page >= pages) next.disabled = true;
  prev.addEventListener('click', () => { if (page > 1) onPage(page - 1); });
  next.addEventListener('click', () => { if (page < pages) onPage(page + 1); });
  return wrap;
}

function table(headers, rows) {
  const thead = el('thead', {}, [el('tr', {}, headers.map((h) => el('th', { text: h })))]);
  const tbody = el('tbody', {}, rows.map((cells) => el('tr', {}, cells.map((c) => {
    const td = el('td', {});
    if (c && typeof c === 'object' && c.nodeType) td.append(c);
    else td.textContent = c ?? '—';
    return td;
  }))));
  return el('table', { class: 'admin-table' }, [thead, tbody]);
}

async function guardAdmin() {
  if (!getSession().token) return false;
  try {
    const me = await fetchMe();
    isAdmin = me && me.role === 'ADMIN';
  } catch { isAdmin = false; }
  adminChecked = true;
  document.querySelectorAll('[data-admin-nav]').forEach((n) => {
    n.style.display = isAdmin ? '' : 'none';
  });
  return isAdmin;
}

export function openAdmin(tab = 'dashboard') {
  if (!getSession().token) { toast('Sign in as an administrator to open the dashboard.', 'error'); return; }
  if (adminChecked && !isAdmin) { toast('You do not have permission to do that.', 'error'); return; }
  currentTab = tab;
  openModal(modal());
  render();
}

function closeAdmin() { closeModal(modal()); }
async function render() {
  const host = body();
  if (!host) return;
  host.replaceChildren(el('div', { class: 'async-state', text: 'Loading…' }));
  const ok = await guardAdmin();
  if (!ok) {
    host.replaceChildren(el('div', { class: 'async-state error', text: 'Admin access required.' }));
    closeAdmin();
    toast('You do not have permission to do that.', 'error');
    return;
  }
  const nav = el('div', { class: 'admin-tabs', role: 'tablist', 'aria-label': 'Admin sections' },
    tabs.map(([key, label]) => {
      const b = el('button', { type: 'button', class: `tab-btn${key === currentTab ? ' active' : ''}`, text: label });
      b.setAttribute('role', 'tab');
      b.setAttribute('aria-selected', String(key === currentTab));
      b.addEventListener('click', () => { currentTab = key; render(); });
      return b;
    }));
  const content = el('div', { id: 'admin-content' });
  host.replaceChildren(nav, el('div', { id: 'admin-status', class: 'async-state', role: 'status' }), content);
  try {
    if (currentTab === 'dashboard') await renderDashboard(content);
    else if (currentTab === 'users') await renderUsers(content, 1, '');
    else if (currentTab === 'movies') await renderMovies(content, 1);
    else if (currentTab === 'shows') await renderShows(content, 1);
    else if (currentTab === 'bookings') await renderBookings(content, 1, '');
    else if (currentTab === 'payments') await renderPayments(content, 1);
    else if (currentTab === 'reviews') await renderReviews(content, 1);
    else if (currentTab === 'messages') await renderMessages(content, 1);
    else await renderAudit(content, 1);
  } catch (error) { setStatus(friendlyMessage(error), true); }
}

async function renderDashboard(content) {
  const d = await adminDashboard();
  const cards = [
    ['Users', `${d.active_users}/${d.total_users}`], ['Movies', d.total_movies],
    ['Shows', `${d.upcoming_shows}/${d.total_shows}`], ['Bookings', d.total_bookings],
    ['Confirmed', d.confirmed_bookings], ['Revenue', `Rs.${Number(d.total_revenue).toFixed(2)}`],
    ['Pending payments', d.pending_payments], ['Reviews', d.total_reviews],
    ['Unread messages', d.unread_contact_messages], ['Subscribers', d.newsletter_subscribers],
  ];
  content.replaceChildren(el('div', { class: 'admin-cards' },
    cards.map(([k, v]) => el('div', { class: 'admin-card' }, [
      el('div', { class: 'admin-card-k', text: k }),
      el('div', { class: 'admin-card-v', text: String(v) }),
    ]))));
}
async function renderUsers(content, page, search) {
  const data = await adminUsers({ page, page_size: PAGE_SIZE, search });
  const searchInput = el('input', { type: 'search', class: 'admin-search', placeholder: 'Search name/email…' });
  searchInput.value = search || '';
  searchInput.setAttribute('aria-label', 'Search users');
  const go = el('button', { type: 'button', class: 'modal-btn-ghost', text: 'Search' });
  go.addEventListener('click', () => renderUsers(content, 1, searchInput.value.trim()));
  const rows = data.items.map((u) => {
    const toggle = el('button', { type: 'button', class: 'modal-btn-ghost', text: u.is_active ? 'Deactivate' : 'Activate' });
    toggle.addEventListener('click', async () => {
      await runExclusive(toggle, 'Working…', async () => {
        try {
          await (u.is_active ? adminDeactivateUser(u.id) : adminActivateUser(u.id));
          toast(u.is_active ? 'User deactivated.' : 'User activated.', 'success');
          await renderUsers(content, page, search);
        } catch (error) { toast(friendlyMessage(error), 'error'); }
      });
    });
    const roleSelect = el('select', { class: 'admin-role', 'aria-label': `Role for ${u.email}` }, [
      el('option', { value: 'USER', text: 'USER' }),
      el('option', { value: 'ADMIN', text: 'ADMIN' }),
    ]);
    roleSelect.value = u.role;
    const saveRole = el('button', { type: 'button', class: 'modal-btn-ghost', text: 'Set role' });
    saveRole.addEventListener('click', async () => {
      const next = roleSelect.value;
      if (next === u.role) { toast('Role unchanged.', 'info'); return; }
      await runExclusive(saveRole, 'Saving…', async () => {
        try {
          await adminSetUserRole(u.id, next);
          toast(`Role updated to ${next}. Existing sessions for this user are invalidated.`, 'success');
          await renderUsers(content, page, search);
        } catch (error) { toast(friendlyMessage(error), 'error'); }
      });
    });
    return ['#' + u.id, u.full_name, u.email,
      el('div', { class: 'admin-role-row' }, [roleSelect, saveRole]),
      badge(u.is_active ? 'active' : 'inactive', u.is_active ? 'green' : 'red'),
      `B:${u.booking_count} R:${u.review_count}`, toggle];
  });
  content.replaceChildren(
    el('div', { class: 'admin-toolbar' }, [searchInput, go]),
    data.items.length ? table(['ID', 'Name', 'Email', 'Role', 'Status', 'Activity', 'Action'], rows)
      : el('div', { class: 'async-state', text: 'No users found.' }),
    pager(data.total, data.page, (p) => renderUsers(content, p, search)),
  );
}
async function renderMovies(content, page) {
  const data = await adminMovies({ page, page_size: PAGE_SIZE });
  const titleInput = el('input', { type: 'text', class: 'admin-search', placeholder: 'New movie title…' });
  titleInput.setAttribute('aria-label', 'New movie title');
  const create = el('button', { type: 'button', class: 'modal-btn-ghost', text: 'Create movie' });
  create.addEventListener('click', async () => {
    const title = titleInput.value.trim();
    if (!title) { toast('Enter a movie title.', 'error'); return; }
    await runExclusive(create, 'Creating…', async () => {
      try {
        await adminCreateMovie({ title, metadata_json: '{}' });
        toast('Movie created.', 'success');
        await renderMovies(content, 1);
      } catch (error) { toast(friendlyMessage(error), 'error'); }
    });
  });
  const rows = data.items.map((m) => {
    const toggle = el('button', { type: 'button', class: 'modal-btn-ghost', text: m.is_active ? 'Unpublish' : 'Publish' });
    toggle.addEventListener('click', async () => {
      await runExclusive(toggle, 'Saving…', async () => {
        try {
          await adminUpdateMovie(m.id, { is_active: !m.is_active });
          toast('Movie updated.', 'success');
          await renderMovies(content, page);
        } catch (error) { toast(friendlyMessage(error), 'error'); }
      });
    });
    return ['#' + m.id, m.title, badge(m.is_active ? 'published' : 'hidden', m.is_active ? 'green' : 'red'), toggle];
  });
  content.replaceChildren(
    el('div', { class: 'admin-toolbar' }, [titleInput, create]),
    data.items.length ? table(['ID', 'Title', 'State', 'Action'], rows)
      : el('div', { class: 'async-state', text: 'No movies found.' }),
    pager(data.total, data.page, (p) => renderMovies(content, p)),
  );
}
async function renderShows(content, page) {
  const data = await adminShows({ page, page_size: PAGE_SIZE, upcoming: true });
  const rows = data.items.map((s) => {
    const cancel = el('button', { type: 'button', class: 'modal-btn-ghost', text: s.status === 'CANCELLED' ? 'Cancelled' : 'Cancel' });
    if (s.status !== 'CANCELLED') {
      cancel.addEventListener('click', async () => {
        await runExclusive(cancel, 'Cancelling…', async () => {
          try {
            await adminCancelShow(s.id);
            toast('Show cancelled.', 'success');
            await renderShows(content, page);
          } catch (error) { toast(friendlyMessage(error), 'error'); }
        });
      });
    } else cancel.disabled = true;
    return ['#' + s.id, s.movie_title || s.movie_id, s.theater_name || '—',
      `${s.show_date} ${String(s.show_time).slice(0, 5)}`,
      badge(s.status, s.status === 'ACTIVE' ? 'green' : 'red'),
      `H:${s.held_seats} B:${s.booked_seats}`, cancel];
  });
  content.replaceChildren(
    data.items.length ? table(['ID', 'Movie', 'Theater', 'When', 'Status', 'Seats', 'Action'], rows)
      : el('div', { class: 'async-state', text: 'No upcoming shows.' }),
    pager(data.total, data.page, (p) => renderShows(content, p)),
  );
}
async function renderBookings(content, page, search) {
  const data = await adminBookings({ page, page_size: PAGE_SIZE, search });
  const searchInput = el('input', { type: 'search', class: 'admin-search', placeholder: 'Booking reference…' });
  searchInput.value = search || '';
  searchInput.setAttribute('aria-label', 'Search bookings');
  const go = el('button', { type: 'button', class: 'modal-btn-ghost', text: 'Search' });
  go.addEventListener('click', () => renderBookings(content, 1, searchInput.value.trim()));
  const rows = data.items.map((b) => [
    b.booking_reference, b.movie_title || '—', (b.seats || []).join(', '),
    badge(b.status, b.status === 'CONFIRMED' ? 'green' : ''),
    badge(b.payment_status, b.payment_status === 'PAID' ? 'green' : ''),
    `Rs.${Number(b.total_amount).toFixed(2)}`,
  ]);
  content.replaceChildren(
    el('div', { class: 'admin-toolbar' }, [searchInput, go]),
    data.items.length ? table(['Reference', 'Movie', 'Seats', 'Booking', 'Payment', 'Total'], rows)
      : el('div', { class: 'async-state', text: 'No bookings found.' }),
    pager(data.total, data.page, (p) => renderBookings(content, p, search)),
  );
}
async function renderPayments(content, page) {
  const data = await adminPayments({ page, page_size: PAGE_SIZE });
  const rows = data.items.map((p) => {
    const view = el('button', { type: 'button', class: 'modal-btn-ghost', text: 'Detail' });
    view.addEventListener('click', async () => {
      try {
        const detail = await adminPaymentAttempt(p.id);
        const events = (detail.webhook_events || []).map((e) => `${e.event_type}:${e.status}`).join(', ') || 'none';
        toast(`Attempt #${detail.attempt_no} ${detail.status} · webhooks: ${events}`, 'info');
      } catch (error) { toast(friendlyMessage(error), 'error'); }
    });
    return ['#' + p.id, p.booking_reference || p.booking_id, p.provider,
      badge(p.status, p.status === 'PAID' ? 'green' : p.status === 'FAILED' ? 'red' : ''),
      `Rs.${Number(p.amount).toFixed(2)}`, p.failure_code || '—', view];
  });
  content.replaceChildren(
    data.items.length ? table(['ID', 'Booking', 'Provider', 'Status', 'Amount', 'Failure', ''], rows)
      : el('div', { class: 'async-state', text: 'No payment attempts.' }),
    pager(data.total, data.page, (p) => renderPayments(content, p)),
  );
}

async function renderReviews(content, page) {
  const data = await adminReviews({ page, page_size: PAGE_SIZE });
  const rows = data.items.map((r) => {
    const del = el('button', { type: 'button', class: 'modal-btn-ghost', text: 'Delete' });
    del.addEventListener('click', async () => {
      await runExclusive(del, 'Deleting…', async () => {
        try {
          await adminDeleteReview(r.id);
          toast('Review removed.', 'success');
          await renderReviews(content, page);
        } catch (error) { toast(friendlyMessage(error), 'error'); }
      });
    });
    return ['#' + r.id, r.movie_title || r.movie_id, `${r.rating}/10`, r.title,
      r.user_name || '#' + r.user_id, `♥${r.likes}`, del];
  });
  content.replaceChildren(
    data.items.length ? table(['ID', 'Movie', 'Rating', 'Title', 'Author', 'Likes', ''], rows)
      : el('div', { class: 'async-state', text: 'No reviews found.' }),
    pager(data.total, data.page, (p) => renderReviews(content, p)),
  );
}
async function renderMessages(content, page) {
  const [contacts, subs] = await Promise.all([
    adminContacts({ page, page_size: PAGE_SIZE }),
    adminSubscribers({ page: 1, page_size: 5 }),
  ]);
  const rows = contacts.items.map((m) => {
    const toggle = el('button', { type: 'button', class: 'modal-btn-ghost', text: m.is_read ? 'Mark unread' : 'Mark read' });
    toggle.addEventListener('click', async () => {
      await runExclusive(toggle, 'Saving…', async () => {
        try {
          await adminMarkContact(m.id, !m.is_read);
          await renderMessages(content, page);
        } catch (error) { toast(friendlyMessage(error), 'error'); }
      });
    });
    return ['#' + m.id, m.name, m.email, m.subject,
      badge(m.is_read ? 'read' : 'unread', m.is_read ? '' : 'gold'), toggle];
  });
  content.replaceChildren(
    el('h4', { class: 'admin-h', text: `Contact messages (${contacts.total}) · Subscribers (${subs.total})` }),
    contacts.items.length ? table(['ID', 'Name', 'Email', 'Subject', 'State', ''], rows)
      : el('div', { class: 'async-state', text: 'No messages.' }),
    pager(contacts.total, contacts.page, (p) => renderMessages(content, p)),
  );
}

async function renderAudit(content, page) {
  const data = await adminAuditLogs({ page, page_size: PAGE_SIZE });
  const rows = data.items.map((a) => [
    '#' + a.id, a.action, a.resource_type, a.resource_id || '—',
    a.admin_email || (a.admin_user_id ? '#' + a.admin_user_id : '—'),
    new Date(a.created_at).toLocaleString('en-IN'),
  ]);
  content.replaceChildren(
    data.items.length ? table(['ID', 'Action', 'Resource', 'Resource ID', 'Admin', 'At'], rows)
      : el('div', { class: 'async-state', text: 'No audit records.' }),
    pager(data.total, data.page, (p) => renderAudit(content, p)),
  );
}

export function initAdmin() {
  if (initDone) return;
  initDone = true;
  onSessionChange((session) => {
    const admin = Boolean(session.user && session.user.role === 'ADMIN');
    isAdmin = admin;
    if (!admin) adminChecked = false;
    document.querySelectorAll('[data-admin-nav]').forEach((n) => {
      n.style.display = admin ? '' : 'none';
    });
    if (!admin && modal() && modal().classList.contains('open')) closeAdmin();
  });
  registerActions({ 'open-admin': () => openAdmin('dashboard'), 'close-admin': closeAdmin });
  modal()?.addEventListener('click', (event) => { if (event.target === modal()) closeAdmin(); });
  guardAdmin();
}
