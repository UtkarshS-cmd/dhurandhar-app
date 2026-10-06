// Safe DOM helpers. Rule for the whole app:
//   user/server-controlled data → textContent or escapeHtml() before any
//   innerHTML. Never interpolate raw API data into markup strings.

export const $ = (id) => document.getElementById(id);

const HTML_ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const BOOLEAN_ATTRIBUTES = new Set([
  'autofocus', 'checked', 'disabled', 'hidden', 'multiple', 'open',
  'readonly', 'required', 'selected',
]);

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
}

/** Set an element's text safely (never parsed as HTML). */
export function setText(node, text) {
  if (node) node.textContent = text;
}

/** Replace an element's children with new nodes built by `build`. */
export function replaceChildren(parent, nodes) {
  if (!parent) return;
  parent.replaceChildren(...nodes);
}

/** Create an element with attributes/text/children. */
export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value; // only for pre-escaped strings
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (typeof value === 'boolean' && BOOLEAN_ATTRIBUTES.has(key.toLowerCase())) {
      if (value) node.setAttribute(key, '');
    } else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined) continue;
    node.append(child);
  }
  return node;
}

/** Build an <option> without string interpolation. */
export function option(value, label) {
  const opt = document.createElement('option');
  opt.value = String(value);
  opt.textContent = label;
  return opt;
}
