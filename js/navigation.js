
export function initNavigation() {
  const navbar = document.getElementById('navbar');
  const menu = document.getElementById('mobile-menu');
  const hamburger = document.getElementById('hamburger');
  const close = document.getElementById('menu-close');
  if (!navbar || navbar.dataset.init === '1') return; // idempotent re-init
  navbar.dataset.init = '1';

  const setMenu = (open) => {
    menu?.classList.toggle('open', open);
    hamburger?.setAttribute('aria-expanded', String(open));
    document.body.classList.toggle('menu-open', open);
  };

  hamburger?.addEventListener('click', () => setMenu(true));
  close?.addEventListener('click', () => setMenu(false));

  let lastScrolled = null;
  const updateNav = () => {
    const scrolled = window.scrollY > 70;
    if (scrolled !== lastScrolled) {
      navbar.classList.toggle('scrolled', scrolled);
      lastScrolled = scrolled;
    }
  };
  window.addEventListener('scroll', updateNav, { passive: true });
  updateNav();

  // Delegated anchor navigation: works for links added after init and can
  // never duplicate listeners on re-initialization.
  document.addEventListener('click', (event) => {
    const link = event.target.closest?.('a[href^="#"]');
    if (!link) return;
    const href = link.getAttribute('href');
    if (!href || href === '#') return;
    const target = document.querySelector(href);
    if (!target) return;
    event.preventDefault();
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    target.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth' });
    // Menu links also close the mobile menu (replaces per-link listeners).
    if (link.closest('#mobile-menu')) setMenu(false);
  });
}

