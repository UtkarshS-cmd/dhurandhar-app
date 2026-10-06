// Accessible toast notifications: aria-live region, capped at 3 visible
// (never stack infinitely), auto-dismiss, click-to-dismiss, mobile-friendly.

const MAX_VISIBLE = 3;
const DEFAULT_TTL = 4500;
const ERROR_TTL = 7000;

let root = null;
const live = new Map(); // message → node (dedupe identical toasts)

function ensureRoot() {
  if (root && document.body.contains(root)) return root;
  root = document.createElement('div');
  root.id = 'toast-root';
  root.className = 'toast-root';
  root.setAttribute('role', 'status');
  root.setAttribute('aria-live', 'polite');
  document.body.appendChild(root);
  return root;
}

function dismiss(node) {
  if (!node) return;
  live.delete(node.dataset.msg);
  node.classList.add('toast-out');
  setTimeout(() => node.remove(), 250);
}

/**
 * Show a toast. type: 'info' | 'success' | 'error'
 * Duplicate messages collapse instead of stacking.
 */
export function toast(message, type = 'info') {
  const text = String(message ?? '').trim();
  if (!text) return null;
  const container = ensureRoot();

  const existing = live.get(text);
  if (existing) { dismiss(existing); }

  while (live.size >= MAX_VISIBLE) {
    const oldest = live.values().next().value;
    dismiss(oldest);
  }

  const node = document.createElement('div');
  node.className = `toast toast-${type}`;
  node.dataset.msg = text;
  node.textContent = text; // textContent only — toasts may carry server copy
  node.addEventListener('click', () => dismiss(node));
  container.appendChild(node);
  live.set(text, node);

  const ttl = type === 'error' ? ERROR_TTL : DEFAULT_TTL;
  setTimeout(() => dismiss(node), ttl);
  return node;
}
