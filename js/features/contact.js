// Contact feature — validated, double-submit guarded, friendly errors.

import { submitContact } from '../api.js';
import { friendlyMessage } from '../core/errors.js';
import { registerActions } from '../ui/actions.js';
import { $ } from '../ui/dom.js';
import { runExclusive } from '../ui/loading.js';

let initDone = false;

async function send() {
  const payload = {
    name: $('c-name').value.trim(),
    email: $('c-email').value.trim(),
    subject: $('c-subject').value,
    message: $('c-message').value.trim(),
  };
  const msg = $('c-msg');
  if (payload.name.length < 2 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(payload.email) || payload.message.length < 5) {
    msg.textContent = 'Please complete all contact fields.';
    msg.style.color = '#e07070';
    return;
  }
  const btn = document.querySelector('[data-action="submit-contact"]');
  await runExclusive(btn, 'Sending…', async () => {
    try {
      await submitContact(payload);
      msg.textContent = 'Message received. Thank you.';
      msg.style.color = '#6fc76f';
      $('c-message').value = '';
    } catch (error) {
      msg.textContent = friendlyMessage(error);
      msg.style.color = '#e07070';
    }
  });
}

export function initContact() {
  if (initDone) return;
  initDone = true;
  registerActions({ 'submit-contact': send });
}
