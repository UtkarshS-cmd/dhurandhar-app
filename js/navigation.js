
export function initNavigation() {
  const navbar = document.getElementById('navbar');
  const menu = document.getElementById('mobile-menu');
  const hamburger = document.getElementById('hamburger');
  const close = document.getElementById('menu-close');

  const setMenu = (open) => {
    menu?.classList.toggle('open', open);
    hamburger?.setAttribute('aria-expanded', String(open));
    document.body.classList.toggle('menu-open', open);
  };

  hamburger?.addEventListener('click', () => setMenu(true));
  close?.addEventListener('click', () => setMenu(false));
  menu?.querySelectorAll('a').forEach((a) => a.addEventListener('click', () => setMenu(false)));

  let lastScrolled = null;
  const updateNav = () => {
    const scrolled = window.scrollY > 70;
    if (scrolled !== lastScrolled) {
      navbar?.classList.toggle('scrolled', scrolled);
      lastScrolled = scrolled;
    }
  };
  window.addEventListener('scroll', updateNav, {passive:true});
  updateNav();

  document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener('click', (event) => {
      const target = document.querySelector(link.getAttribute('href'));
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
    });
  });

  // The old page referenced #reviews and #contact before those sections existed in the first viewport.
  // They now resolve to real sections and use native anchors.
}
