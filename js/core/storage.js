// Safe localStorage access — private-mode/quota/corruption never breaks boot.

function storageAvailable() {
  try {
    const probe = '__dh_probe__';
    localStorage.setItem(probe, '1');
    localStorage.removeItem(probe);
    return true;
  } catch {
    return false;
  }
}

const OK = storageAvailable();

/** Parse JSON from storage; returns `fallback` on any failure. */
export function readJSON(key, fallback = null) {
  if (!OK) return fallback;
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback;
    return JSON.parse(raw);
  } catch {
    // Corrupted entry (e.g. hand-edited or truncated): drop it so the next
    // read starts clean instead of throwing on every boot.
    try { localStorage.removeItem(key); } catch { /* ignore */ }
    return fallback;
  }
}

export function readString(key) {
  if (!OK) return null;
  try { return localStorage.getItem(key); } catch { return null; }
}

export function writeJSON(key, value) {
  if (!OK) return;
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* quota/privacy: degrade silently */ }
}

export function remove(key) {
  if (!OK) return;
  try { localStorage.removeItem(key); } catch { /* ignore */ }
}

export function writeString(key, value) {
  if (!OK) return;
  try { localStorage.setItem(key, value); } catch { /* ignore */ }
}
