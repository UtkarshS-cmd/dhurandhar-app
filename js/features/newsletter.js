// Newsletter feature — validated, double-submit guarded, friendly errors.

import { subscribeNewsletter } from '../api.js';
import { friendlyMessage } from '../core/errors.js';
import { registerActions } from '../ui/actions.js';
import { $ } from '../ui/dom.js';
import { runExclusive } from '../ui/loading.js';

let initDone = false;

async function subscribe() {
  const name = $('nl-name').value.trim();
  const email = $('nl-email').value.trim();
  const msg = $('nl-msg');
  if (name.length < 2 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    msg.textContent = 'Please enter a valid name and email.';
    msg.style.color = '#e07070';
    return;
  }
  const btn = document.querySelector('[data-action="submit-newsletter"]');
  await runExclusive(btn, 'Subscribing…', async () => {
    try {
      await subscribeNewsletter({ name, email });
      msg.textContent = 'You are subscribed.';
      msg.style.color = '#6fc76f';
      $('nl-name').value = '';
      $('nl-email').value = '';
    } catch (error) {
      msg.textContent = friendlyMessage(error);
      msg.style.color = '#e07070';
    }
  });
}

export function initNewsletter() {
  if (initDone) return;
  initDone = true;
  registerActions({ 'submit-newsletter': subscribe });
}
