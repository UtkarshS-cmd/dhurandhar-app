// One delegated click dispatcher for data-action attributes across the app.
// Features register their handlers; nothing else attaches document-level
// click listeners, so re-initialization can never duplicate listeners.

const handlers = new Map(); // action name → handler(event, target)

let installed = false;

function install() {
  if (installed) return;
  installed = true;
  document.addEventListener('click', (event) => {
    const target = event.target.closest?.('[data-action]');
    if (!target) return;
    const handler = handlers.get(target.dataset.action);
    if (!handler) return;
    handler(event, target);
  });
}

/** Register handlers for data-action values. Idempotent per action name. */
export function registerActions(map) {
  install();
  for (const [name, handler] of Object.entries(map)) handlers.set(name, handler);
}

// Auth tabs use data-auth-tab (existing markup); same single-dispatcher model.
const tabHandlers = new Map();

function installTabs() {
  document.addEventListener('click', (event) => {
    const target = event.target.closest?.('[data-auth-tab]');
    if (!target) return;
    const handler = tabHandlers.get('click');
    if (handler) handler(event, target);
  });
  // Keyboard activation for the div-based tabs (they have no native button
  // semantics in the existing markup).
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const target = event.target.closest?.('[data-auth-tab]');
    if (!target) return;
    event.preventDefault();
    const handler = tabHandlers.get('click');
    if (handler) handler(event, target);
  });
}

export function registerTabHandler(fn) {
  installTabs();
  tabHandlers.set('click', fn);
}
