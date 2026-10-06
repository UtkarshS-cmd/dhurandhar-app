// Shared modal machinery: open/close bookkeeping, Escape (top-most only),
// Tab focus trapping, focus restore, and body scroll locking.
//
// All three dialogs (booking, auth, my-bookings) go through this stack, so
// pressing Escape closes only the dialog the user is actually looking at and
// no module needs its own document-level key handling.

const stack = [];

const FOCUSABLE = [
  'a[href]', 'button:not([disabled])', 'input:not([disabled])',
  'select:not([disabled])', 'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function isVisible(node) {
  return Boolean(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
}

function topmost() {
  return stack[stack.length - 1] || null;
}

function syncBody() {
  document.body.classList.toggle('modal-open', stack.length > 0);
}

export function openModal(el, { focus } = {}) {
  if (!el) return;
  if (!stack.some((entry) => entry.el === el)) {
    stack.push({ el, restore: document.activeElement });
  }
  el.classList.add('open');
  syncBody();
  const target = focus || el.querySelector(FOCUSABLE);
  if (target && isVisible(target)) target.focus();
}

export function closeModal(el) {
  if (!el) return;
  const index = stack.findIndex((entry) => entry.el === el);
  if (index === -1) {
    // Not tracked (e.g. opened by legacy code path) — still close safely.
    el.classList.remove('open');
    syncBody();
    return;
  }
  const [entry] = stack.splice(index, 1);
  el.classList.remove('open');
  syncBody();
  if (entry.restore?.focus && isVisible(entry.restore)) entry.restore.focus();
}

export function isModalOpen(el) {
  return Boolean(el && el.classList.contains('open'));
}

export function closeTopModal() {
  const top = topmost();
  if (top) closeModal(top.el);
}

function trapTab(event) {
  const top = topmost();
  if (!top || !top.el.classList.contains('open')) return;
  const focusables = [...top.el.querySelectorAll(FOCUSABLE)].filter(isVisible);
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  const active = document.activeElement;
  if (!top.el.contains(active)) { event.preventDefault(); first.focus(); return; }
  if (event.shiftKey && active === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && active === last) { event.preventDefault(); first.focus(); }
}

let keysInstalled = false;

export function initModalSystem() {
  if (keysInstalled) return;
  keysInstalled = true;
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && topmost()) { closeTopModal(); return; }
    if (event.key === 'Tab' && topmost()) trapTab(event);
  });
}
