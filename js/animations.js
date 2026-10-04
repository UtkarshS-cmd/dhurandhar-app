
export function initAnimations() {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const reveals = document.querySelectorAll('.reveal');
  if (reduced) {
    reveals.forEach((el) => el.classList.add('visible'));
    return;
  }
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        observer.unobserve(entry.target);
      }
    }
  }, {threshold:0.08, rootMargin:'0px 0px -5% 0px'});
  reveals.forEach((el) => observer.observe(el));

  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach((p) => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(`tab-${btn.dataset.tab}`)?.classList.add('active');
    });
  });

  // Cursor is event-driven rather than a permanent RAF loop.
  if (window.matchMedia('(pointer:fine)').matches) {
    const cursor = document.getElementById('cursor');
    const trail = document.getElementById('trail');
    let trailX = 0, trailY = 0, targetX = 0, targetY = 0, raf = 0;

    const paint = () => {
      raf = 0;
      trailX += (targetX - trailX) * 0.16;
      trailY += (targetY - trailY) * 0.16;
      trail.style.transform = `translate3d(${trailX - 14}px,${trailY - 14}px,0)`;
      if (Math.abs(targetX-trailX) > 0.5 || Math.abs(targetY-trailY) > 0.5) raf = requestAnimationFrame(paint);
    };
    window.addEventListener('pointermove', (e) => {
      targetX=e.clientX; targetY=e.clientY;
      cursor.style.transform=`translate3d(${targetX-6}px,${targetY-6}px,0)`;
      if (!raf) raf=requestAnimationFrame(paint);
    }, {passive:true});
  }
}
