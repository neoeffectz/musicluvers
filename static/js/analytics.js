// Visitor analytics (PostHog), production only. Page views are automatic; app moments (signed up,
// liked, matched, message sent...) come from the server via the "ml-events" script or the
// htmx "ml:track" trigger. Chat areas carry .ph-no-capture so their text is never recorded.
(() => {
  const cfg = window.mlAnalytics;
  if (!cfg || !cfg.key) return;
  const waiting = [];
  const ready = () => window.posthog && window.posthog.__loaded;
  const track = (event, props) => { if (!event) return; ready() ? window.posthog.capture(event, props || {}) : waiting.push([event, props || {}]); };
  window.mlTrack = track;

  try { JSON.parse(document.getElementById('ml-events')?.textContent || '[]').forEach(e => track(e.event, e.props)); } catch (e) {}
  document.addEventListener('ml:track', (e) => (e.detail?.events || []).forEach(ev => track(ev.event, ev.props)));
  // In-page tab switches don't load a new page, so count them separately.
  document.addEventListener('click', (e) => {
    const tab = e.target.closest && e.target.closest('[data-tab]');
    if (tab) track('tab_opened', { tab: tab.dataset.tab });
  });
  // Logging out: forget who this phone belongs to, so the next person isn't mixed up with them.
  document.addEventListener('submit', (e) => {
    if ((e.target.getAttribute('action') || '').includes('logout') && ready()) window.posthog.reset();
  }, true);

  const s = document.createElement('script');
  s.async = true;
  s.crossOrigin = 'anonymous';
  s.src = cfg.host.replace('.i.posthog.com', '-assets.i.posthog.com') + '/static/array.js';
  s.onload = () => {
    if (!window.posthog || !window.posthog.init) return;
    window.posthog.init(cfg.key, {
      api_host: cfg.host,
      person_profiles: 'identified_only',
      capture_pageview: true,
      capture_pageleave: true,
      session_recording: { maskAllInputs: true },
      loaded: (ph) => {
        if (cfg.user) ph.identify(cfg.user);
        waiting.splice(0).forEach(([event, props]) => ph.capture(event, props));
      },
    });
  };
  document.head.appendChild(s);
})();
