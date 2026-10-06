// Authentication feature: session lifecycle, auth modal, account profile and
// "My Bookings". The 401 hook makes expired JWTs clear local state once.

import { login, register, fetchMe, updateMe, changePassword, fetchMyBookings } from '../api.js';
import { onUnauthorized } from '../core/api-client.js';
import { friendlyMessage } from '../core/errors.js';
import { getSession, setSession, clearSession, onSessionChange } from '../core/state.js';
import { registerActions, registerTabHandler } from '../ui/actions.js';
import { $, el } from '../ui/dom.js';
import { runExclusive } from '../ui/loading.js';
import { openModal, closeModal, isModalOpen } from '../ui/modal.js';
import { toast } from '../ui/notifications.js';

let initDone = false;
let bookingsRequestId = 0;
let profileRequestId = 0;
let profileReady = false;
let profileOriginal = null;

const authModal = () => $('auth-modal');
const profileModal = () => $('profile-modal');
const bookingsModal = () => $('my-bookings-modal');

function setAuthMessage(id, message, type = '') {
  const node = $(id);
  if (!node) return;
  node.textContent = message;
  node.className = type === 'error' ? 'auth-err' : 'auth-ok';
}

function updateNav(session) {
  const btn = $('nav-auth-btn');
  if (btn) {
    if (session.user) {
      btn.textContent = session.user.full_name.split(/\s+/).map((x) => x[0]).join('').slice(0, 2).toUpperCase();
      btn.classList.add('profile-avatar');
      btn.setAttribute('aria-label', 'Open account menu');
      btn.dataset.action = 'toggle-profile';
      btn.title = session.user.full_name;
    } else {
      btn.textContent = 'Sign In';
      btn.classList.remove('profile-avatar');
      btn.setAttribute('aria-label', 'Sign in');
      btn.dataset.action = 'open-auth';
      $('profile-menu')?.remove();
    }
  }
  // The review form is only available to signed-in users (reference design).
  const write = $('review-write-wrap');
  const cta = $('review-login-cta');
  if (write) write.style.display = session.user ? 'block' : 'none';
  if (cta) cta.style.display = session.user ? 'none' : 'block';
}

export function openAuth() {
  openModal(authModal(), { focus: $('al-email') });
}

function closeAuth() {
  closeModal(authModal());
  $('al-pwd').value = '';
  $('ar-pwd').value = '';
  setAuthMessage('al-err', '');
  setAuthMessage('ar-err', '');
  setAuthMessage('ar-ok', '');
}

function switchAuthTab(tab) {
  document.querySelectorAll('.auth-tab').forEach((t) => {
    const active = t.dataset.authTab === tab;
    t.classList.toggle('active', active);
    t.setAttribute('aria-selected', String(active));
  });
  document.querySelectorAll('.auth-panel').forEach((p) => p.classList.toggle('active', p.id === `auth-${tab}`));
  setAuthMessage('al-err', '');
  setAuthMessage('ar-err', '');
  setAuthMessage('ar-ok', '');
}

async function doLogin() {
  const email = $('al-email').value.trim();
  const password = $('al-pwd').value;
  if (!email || password.length < 1) {
    setAuthMessage('al-err', 'Enter your email and password', 'error');
    return;
  }
  const btn = document.querySelector('[data-action="auth-login"]');
  await runExclusive(btn, 'Signing in…', async () => {
    try {
      const result = await login({ email, password });
      setSession(result.access_token, result.user);
      closeAuth();
      toast('Signed in.', 'success');
    } catch (error) {
      // 401 from /auth/login must NOT clear a live session elsewhere: the
      // api-client only fires the unauthorized hook when a token was attached,
      // and login requests never attach one.
      setAuthMessage('al-err', friendlyMessage(error), 'error');
    }
  });
}

async function doRegister() {
  const name = $('ar-name').value.trim();
  const email = $('ar-email').value.trim();
  const phone = $('ar-phone').value.trim();
  const password = $('ar-pwd').value;
  const validPhone = /^[6-9]\d{9}$/.test(phone.replace(/\D/g, ''));
  if (name.length < 3 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || !validPhone || password.length < 8) {
    setAuthMessage('ar-err', 'Enter a valid name, email, 10-digit phone and 8+ character password', 'error');
    return;
  }
  const btn = document.querySelector('[data-action="auth-register"]');
  await runExclusive(btn, 'Creating…', async () => {
    try {
      const result = await register({ full_name: name, email, phone, password });
      setSession(result.access_token, result.user);
      setAuthMessage('ar-ok', 'Account created successfully.', 'ok');
      // Brief confirmation beat before closing (display-only, not request state).
      setTimeout(closeAuth, 500);
    } catch (error) {
      setAuthMessage('ar-err', friendlyMessage(error), 'error');
    }
  });
}

function toggleProfile(btn) {
  const existing = $('profile-menu');
  if (existing) { existing.classList.toggle('open'); return; }
  const user = getSession().user;
  if (!user) { openAuth(); return; }
  const row = (cls, text) => el('div', { class: cls, text });
  const menu = el('div', { id: 'profile-menu', class: 'profile-dropdown open' }, [
    el('div', { class: 'pd-header' }, [
      row('pd-name', user.full_name),
      row('pd-email', user.email),
    ]),
    el('button', { type: 'button', class: 'pd-item', 'data-action': 'open-profile', text: 'Profile & Password' }),
    el('button', { type: 'button', class: 'pd-item', 'data-action': 'open-my-bookings', text: 'My Bookings' }),
    el('div', { class: 'pd-sep' }),
    el('button', { type: 'button', class: 'pd-item danger', 'data-action': 'logout', text: 'Sign Out' }),
  ]);
  btn.parentElement.style.position = 'relative';
  btn.parentElement.appendChild(menu);
}

function ticketRow(label, value, valueClass = '') {
  return el('div', { class: 'bt-row' }, [
    el('span', { class: 'bt-label', text: label }),
    el('span', { class: `bt-val ${valueClass}`.trim(), text: value }),
  ]);
}

async function openMyBookings() {
  if (!getSession().token) { openAuth(); return; }
  const modal = bookingsModal();
  const list = $('my-bookings-list');
  openModal(modal);
  const requestId = ++bookingsRequestId;
  list.replaceChildren(el('div', { class: 'async-state', text: 'Loading bookings…' }));
  try {
    const bookings = await fetchMyBookings();
    if (requestId !== bookingsRequestId) return; // stale response (reopened/refreshed)
    if (!bookings.length) {
      list.replaceChildren(el('div', { class: 'async-state', text: 'No bookings yet.' }));
      return;
    }
    list.replaceChildren(...bookings.map((b) => el('div', { class: 'booking-ticket' }, [
      el('div', { class: 'bt-ref', text: b.booking_reference }),
      ticketRow('STATUS', b.status, b.status === 'CONFIRMED' ? 'bt-status-confirmed' : ''),
      ticketRow('SEATS', Array.isArray(b.seats) ? b.seats.join(', ') : ''),
      ticketRow('TOTAL', `₹${Number(b.total_amount).toFixed(2)}`),
    ])));
  } catch (error) {
    if (requestId !== bookingsRequestId) return;
    list.replaceChildren(el('div', { class: 'async-state error', text: friendlyMessage(error) }));
  }
}

function closeMyBookings() {
  bookingsRequestId++; // invalidate in-flight render
  closeModal(bookingsModal());
}

function logout() {
  closeProfile();
  closeMyBookings();
  clearSession();
  $('profile-menu')?.remove();
  toast('You have been signed out.', 'info');
}

function clearPasswordFields() {
  const current = $('profile-current-password');
  const next = $('profile-new-password');
  if (current) current.value = '';
  if (next) next.value = '';
}

function setProfileMessage(id, message, type = '') {
  const node = $(id);
  if (!node) return;
  node.textContent = message;
  node.className = type === 'error' ? 'auth-err' : 'auth-ok';
}

function setProfileLoading(loading) {
  profileReady = !loading && Boolean(profileOriginal);
  const save = document.querySelector('[data-action="save-profile"]');
  if (save) save.disabled = !profileReady;
  const status = $('profile-loading');
  if (status) status.textContent = loading ? 'Loading account details…' : '';
}

function populateProfile(user) {
  $('profile-full-name').value = user.full_name || '';
  $('profile-email').value = user.email || '';
  $('profile-phone').value = user.phone || '';
  profileOriginal = {
    full_name: user.full_name || '',
    phone: user.phone || '',
  };
}

async function openProfile() {
  if (!getSession().token || isModalOpen(profileModal())) return;
  $('profile-menu')?.remove();
  const session = getSession();
  const requestId = ++profileRequestId;
  setProfileMessage('profile-err', '');
  setProfileMessage('profile-ok', '');
  setProfileMessage('password-err', '');
  setProfileMessage('password-ok', '');
  setProfileLoading(true);
  if (session.user) populateProfile(session.user);
  openModal(profileModal(), { focus: $('profile-full-name') });

  try {
    const user = await fetchMe();
    if (requestId !== profileRequestId || getSession().token !== session.token) return;
    setSession(session.token, user);
    populateProfile(user);
  } catch (error) {
    if (requestId !== profileRequestId) return;
    setProfileMessage('profile-err', friendlyMessage(error), 'error');
  } finally {
    if (requestId === profileRequestId) setProfileLoading(false);
  }
}

function closeProfile() {
  profileRequestId++;
  profileReady = false;
  profileOriginal = null;
  clearPasswordFields();
  closeModal(profileModal());
  $('nav-auth-btn')?.focus();
}

async function saveProfile() {
  if (!profileReady || !profileOriginal) return;
  const fullName = $('profile-full-name').value.trim();
  const phoneInput = $('profile-phone').value.trim();
  const phone = phoneInput.replace(/\D/g, '').replace(/^91(?=\d{10}$)/, '');
  if (fullName.length < 3 || fullName.length > 120) {
    setProfileMessage('profile-err', 'Full name must be between 3 and 120 characters.', 'error');
    return;
  }
  if (!/^[6-9]\d{9}$/.test(phone)) {
    setProfileMessage('profile-err', 'Enter a valid 10-digit Indian mobile number.', 'error');
    return;
  }

  const payload = {};
  if (fullName !== profileOriginal.full_name) payload.full_name = fullName;
  if (phone !== profileOriginal.phone) payload.phone = phone;
  if (!Object.keys(payload).length) {
    setProfileMessage('profile-err', '');
    setProfileMessage('profile-ok', 'Your profile is already up to date.', 'ok');
    return;
  }

  const button = document.querySelector('[data-action="save-profile"]');
  await runExclusive(button, 'Saving…', async () => {
    setProfileMessage('profile-err', '');
    setProfileMessage('profile-ok', '');
    try {
      const updated = await updateMe(payload);
      const token = getSession().token;
      if (!token) return;
      setSession(token, updated);
      populateProfile(updated);
      setProfileMessage('profile-ok', 'Profile updated successfully.', 'ok');
      toast('Profile updated.', 'success');
    } catch (error) {
      setProfileMessage('profile-err', friendlyMessage(error), 'error');
    }
  });
}

async function submitPasswordChange() {
  const currentPassword = $('profile-current-password').value;
  const newPassword = $('profile-new-password').value;
  if (!currentPassword || newPassword.length < 8 || newPassword.length > 128) {
    setProfileMessage('password-err', 'Enter your current password and a new password of 8–128 characters.', 'error');
    return;
  }
  if (currentPassword === newPassword) {
    setProfileMessage('password-err', 'Your new password must differ from your current password.', 'error');
    return;
  }

  const button = document.querySelector('[data-action="change-password"]');
  await runExclusive(button, 'Changing…', async () => {
    setProfileMessage('password-err', '');
    setProfileMessage('password-ok', '');
    try {
      await changePassword({ current_password: currentPassword, new_password: newPassword });
      clearPasswordFields();
      closeProfile();
      clearSession();
      toast('Password changed. Sign in again with your new password.', 'success');
    } catch (error) {
      setProfileMessage('password-err', friendlyMessage(error), 'error');
    }
  });
}

export function initAuth() {
  if (initDone) return;
  initDone = true;

  // Session-driven UI (nav button, review form visibility).
  onSessionChange(updateNav);
  updateNav(getSession());

  // A bearer-auth challenge means the JWT is dead: clear once, notify and
  // close account UI. A wrong current password is a distinct 401 without that
  // challenge and must not sign out a still-valid session.
  onUnauthorized(() => {
    clearSession();
    toast('Your session has expired. Please sign in again.', 'error');
    if (isModalOpen(profileModal())) closeProfile();
    if (isModalOpen(bookingsModal())) closeMyBookings();
  });

  registerTabHandler((event, target) => switchAuthTab(target.dataset.authTab));

  registerActions({
    'open-auth': openAuth,
    'close-auth': closeAuth,
    'auth-login': doLogin,
    'auth-register': doRegister,
    'toggle-profile': (event, target) => toggleProfile(target),
    'open-profile': openProfile,
    'close-profile': closeProfile,
    'save-profile': saveProfile,
    'change-password': submitPasswordChange,
    'open-my-bookings': openMyBookings,
    'close-my-bookings': closeMyBookings,
    'logout': logout,
  });

  // Enter on the password field submits login (existing UX, now guarded).
  $('al-pwd')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); doLogin(); }
  });

  // Click-outside closes the profile dropdown.
  document.addEventListener('click', (event) => {
    const menu = $('profile-menu');
    if (!menu) return;
    if (menu.contains(event.target) || event.target.closest?.('[data-action="toggle-profile"]')) return;
    menu.remove();
  });

  // Backdrop click closes dialogs (the modal stack owns Escape/Tab).
  authModal()?.addEventListener('click', (event) => { if (event.target === authModal()) closeAuth(); });
  profileModal()?.addEventListener('click', (event) => { if (event.target === profileModal()) closeProfile(); });
  bookingsModal()?.addEventListener('click', (event) => { if (event.target === bookingsModal()) closeMyBookings(); });
}
