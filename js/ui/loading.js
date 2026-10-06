// Loading + double-submit protection.
//
// `runExclusive(button, label, task)` implements a request state machine:
//   idle → submitting → success/error → idle
// While submitting the button is disabled and re-entry is impossible — no
// arbitrary timeouts, no click counters. Returns the task's result or rethrows.

const BUSY = 'data-busy';

export async function runExclusive(button, label, task) {
  if (!button) return task();
  if (button.hasAttribute(BUSY)) return undefined; // duplicate submission blocked
  const original = button.textContent;
  button.setAttribute(BUSY, '');
  button.disabled = true;
  if (label) button.textContent = label;
  try {
    return await task();
  } finally {
    button.removeAttribute(BUSY);
    button.disabled = false;
    button.textContent = original;
  }
}

/** True while an exclusive run is in flight on this button. */
export function isBusy(button) {
  return Boolean(button?.hasAttribute(BUSY));
}
