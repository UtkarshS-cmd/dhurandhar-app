
import { initNavigation } from './navigation.js';
import { initAnimations } from './animations.js';
import { initMusicPlayer } from './music-player.js';
import { initVideo } from './video.js';
import { initModalSystem } from './ui/modal.js';
import { initAuth, openAuth } from './features/auth.js';
import { initBooking } from './features/booking.js';
import { initReviews, setRequireSignIn } from './features/reviews.js';
import { initNewsletter } from './features/newsletter.js';
import { initContact } from './features/contact.js';
import { initAdmin } from './features/admin.js';

function initGalleryFallback() {
  // Replaces the former inline onerror attributes on gallery images so the
  // Content-Security-Policy can keep script-src 'self' (no inline handlers).
  // Error events do not bubble, hence the capture phase.
  document.addEventListener('error', (event) => {
    const target = event.target;
    if (target && target.tagName === 'IMG') {
      const item = target.closest('.g-item');
      if (item) item.style.display = 'none';
    }
  }, true);
}

document.addEventListener('DOMContentLoaded', () => {
  initModalSystem();
  initNavigation();
  initAnimations();
  initMusicPlayer();
  initVideo();
  initGalleryFallback();
  initAuth();
  setRequireSignIn(openAuth);
  initBooking();
  initReviews();
  initNewsletter();
  initContact();
  initAdmin();
});

