// Friendly messages when something goes wrong, instead of a tap that silently does nothing:
// - the server said no (e.g. "Messages can be up to 2000 characters.") -> show its message
// - a server error, a removed page, an expired page -> a plain-English message
// - no internet / the request never arrived / it took too long -> say so
// Background checks (notification badge, read receipts, push setup) stay quiet.
(() => {
  const css = `
    #ml-toast { position: fixed; left: 50%; top: calc(68px + env(safe-area-inset-top, 0px)); z-index: 6000;
      transform: translate(-50%, -16px); opacity: 0; pointer-events: none; width: max-content; max-width: min(92vw, 420px);
      display: flex; align-items: center; gap: 10px; padding: 12px 14px 12px 16px; border-radius: 14px;
      background: #26272d; color: #f5f5f7; font-size: 14px; line-height: 1.4; box-shadow: 0 10px 30px rgba(0,0,0,.45);
      border: 1px solid rgba(255,255,255,.08); transition: opacity .2s ease, transform .2s ease; }
    #ml-toast.show { opacity: 1; transform: translate(-50%, 0); pointer-events: auto; }
    #ml-toast i { flex-shrink: 0; font-size: 16px; }
    #ml-toast.error i { color: #ff6b5a; } #ml-toast.warn i { color: #f5b942; } #ml-toast.ok i { color: #4cd38a; }
    #ml-toast .ml-toast-text { flex: 1; }
    #ml-toast button { background: none; border: 0; color: #9a9aa2; font-size: 20px; line-height: 1; padding: 0 2px; cursor: pointer; }
    @media (min-width: 900px) { #ml-toast { top: 24px; } }
    @media (prefers-reduced-motion: reduce) { #ml-toast { transition: none; } }`;
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  const ICONS = { error: 'fa-circle-exclamation', warn: 'fa-wifi', ok: 'fa-circle-check' };
  let toast, timer;
  function show(message, kind = 'error', ms = 5000) {
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'ml-toast';
      toast.setAttribute('role', 'alert');
      toast.innerHTML = '<i class="fa-solid"></i><span class="ml-toast-text"></span><button type="button" aria-label="Close">&times;</button>';
      toast.querySelector('button').addEventListener('click', hide);
      document.body.appendChild(toast);
    }
    toast.className = kind;
    toast.querySelector('i').className = 'fa-solid ' + (ICONS[kind] || ICONS.error);
    toast.querySelector('.ml-toast-text').textContent = message;
    void toast.offsetWidth;
    toast.classList.add('show');
    clearTimeout(timer);
    if (ms) timer = setTimeout(hide, ms);
  }
  function hide() { if (toast) toast.classList.remove('show'); }
  window.mlToast = show;

  const QUIET = ['/notifications/badge/', '/messages/read/', '/push/'];
  const isQuiet = (e) => QUIET.some(q => ((e.detail.pathInfo && e.detail.pathInfo.requestPath) || '').includes(q));

  function messageFor(xhr) {
    const text = (xhr.responseText || '').trim();
    // Short plain sentences from the server are written for people — show them as they are.
    if (text && text.length <= 200 && !text.includes('<')) return text;
    switch (true) {
      case xhr.status === 403: return 'This page expired. Refresh it and try again.';
      case xhr.status === 404: return "That isn't available anymore. Refresh to see the latest.";
      case xhr.status === 413: return 'That file is too big. Pick a smaller one.';
      case xhr.status === 429: return 'Too many tries. Wait a moment, then try again.';
      case xhr.status >= 500: return 'Something went wrong on our side. Please try again in a moment.';
      default: return "That didn't work. Please try again.";
    }
  }

  document.addEventListener('htmx:responseError', (e) => { if (!isQuiet(e)) show(messageFor(e.detail.xhr)); });
  document.addEventListener('htmx:sendError', (e) => {
    if (isQuiet(e)) return;
    show(navigator.onLine === false ? "You're offline. Check your connection and try again."
                                    : "Couldn't reach Music Luvers. Check your connection and try again.", 'warn');
  });
  document.addEventListener('htmx:timeout', (e) => {
    if (!isQuiet(e)) show('This is taking too long. Check your connection and try again.', 'warn');
  });

  // A request that hangs forever leaves the screen frozen; give up after 45 s and say so.
  const setTimeoutConfig = () => { if (window.htmx && !window.htmx.config.timeout) window.htmx.config.timeout = 45000; };
  setTimeoutConfig();
  document.addEventListener('DOMContentLoaded', setTimeoutConfig);

  window.addEventListener('offline', () => show("You're offline. We'll pick up again when you're back.", 'warn', 0));
  window.addEventListener('online', () => show("You're back online.", 'ok', 2500));
})();
