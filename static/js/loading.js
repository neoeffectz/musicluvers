// Instant feedback on every tap: a thin orange progress bar at the top, and a dimmed "loading" state
// on the area being replaced (tab content / chat). Purely visual — requests are unchanged.
(() => {
  const css = `
    #ml-progress { position: fixed; top: env(safe-area-inset-top, 0px); left: 0; height: 3px; width: 0; z-index: 5000;
      background: linear-gradient(90deg, #e02f26, #f3582c); box-shadow: 0 0 8px rgba(243,88,44,0.7);
      opacity: 0; transition: width .25s ease, opacity .3s ease; pointer-events: none; }
    #ml-progress.active { opacity: 1; width: 75%; transition: width 6s cubic-bezier(.1,.6,.2,1), opacity .1s; }
    #ml-progress.done { opacity: 0; width: 100%; transition: width .15s ease, opacity .35s ease .1s; }
    .ml-loading { position: relative; }
    .ml-loading > * { opacity: .45; transition: opacity .15s; pointer-events: none; }
    .ml-loading::after { content: ""; position: absolute; left: 50%; top: min(40%, 260px); width: 34px; height: 34px;
      margin-left: -17px; border-radius: 50%; border: 3px solid rgba(243,88,44,0.25); border-top-color: #f3582c;
      animation: mlSpin .7s linear infinite; z-index: 10; }
    @keyframes mlSpin { to { transform: rotate(360deg); } }
    @media (prefers-reduced-motion: reduce) { .ml-loading::after { animation-duration: 2s; } }`;
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  let bar;
  const ensureBar = () => {
    if (!bar) { bar = document.createElement('div'); bar.id = 'ml-progress'; document.body.appendChild(bar); }
    return bar;
  };
  let pending = 0;
  const start = () => { const b = ensureBar(); pending++; b.classList.remove('done'); void b.offsetWidth; b.classList.add('active'); };
  const finish = (all) => {
    pending = all ? 0 : Math.max(0, pending - 1);
    if (pending || !bar) return;
    bar.classList.remove('active'); bar.classList.add('done');
  };

  // Background requests that shouldn't flash the bar.
  const QUIET = ['/notifications/badge/', '/search/', '/push/', '/welcome/locate/', '/dashboard/artists/'];
  const isQuiet = (path) => QUIET.some(q => (path || '').includes(q));
  const BIG_AREAS = ['tab-content', 'messages-content'];

  document.addEventListener('htmx:beforeRequest', (e) => {
    const path = e.detail.pathInfo && e.detail.pathInfo.requestPath;
    if (isQuiet(path)) return;
    e.detail.elt.__mlTracked = true;
    start();
    const target = e.detail.target;
    if (target && BIG_AREAS.includes(target.id)) target.classList.add('ml-loading');
  });
  // afterRequest reports the element that *made* the request (afterSettle reports the swapped area instead).
  document.addEventListener('htmx:afterRequest', (e) => {
    const elt = e.detail.elt;
    if (!elt || !elt.__mlTracked) return;
    elt.__mlTracked = false;
    finish();
    if (!e.detail.successful) BIG_AREAS.forEach(id => document.getElementById(id)?.classList.remove('ml-loading'));
  });
  // New content arrived in a big area: it's no longer loading.
  document.addEventListener('htmx:afterSwap', (e) => {
    if (e.detail.target && BIG_AREAS.includes(e.detail.target.id)) e.detail.target.classList.remove('ml-loading');
  });

  // Full page changes (links to other pages, normal forms): show the bar the moment you tap.
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[href]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.hasAttribute('hx-get') || a.target === '_blank' || a.hasAttribute('download')) return;
    const href = a.getAttribute('href');
    if (!href || href.startsWith('#') || href.startsWith('mailto:') || href.startsWith('tel:')) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin || (url.pathname === location.pathname && url.search === location.search)) return;
    start();
  });
  document.addEventListener('submit', (e) => {
    const f = e.target;
    if (!e.defaultPrevented && !f.hasAttribute('hx-post') && !f.hasAttribute('hx-get')) start();
  });
  // Coming back with the Back button (page restored from memory): never leave the bar stuck.
  window.addEventListener('pageshow', () => finish(true));
})();
