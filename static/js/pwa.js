// Installable app + phone notifications, shared by every page.
(() => {
  const meta = (name) => document.querySelector(`meta[name="${name}"]`)?.content || '';
  const VAPID = meta('ml-vapid-key');
  const csrf = () => { const m = document.cookie.match(/(^|;)\s*csrftoken\s*=\s*([^;]+)/); return m ? m.pop() : ''; };
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const isStandalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const pushSupported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window && !!VAPID;

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(() => syncSubscription()).catch(() => {});
  }

  function keyBytes(base64) {
    const pad = '='.repeat((4 - base64.length % 4) % 4);
    const raw = atob((base64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
  }
  async function post(url, body) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, body: JSON.stringify(body || {}) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Server error');
  }
  // Always wait for an *active* worker: subscribing fails while it's still installing (first visit).
  async function ready() { return navigator.serviceWorker.ready; }

  // Permission already given: make sure this phone is subscribed and the server knows it
  // (covers phones that rotate their subscription, and permission granted before a reinstall).
  async function syncSubscription() {
    if (!pushSupported || Notification.permission !== 'granted' || !meta('ml-signed-in')) return;
    try {
      const reg = await ready();
      const sub = (await reg.pushManager.getSubscription()) ||
        await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(VAPID) });
      await post('/push/subscribe/', sub.toJSON());
    } catch (e) { console.warn('Notification sync failed', e); }
  }

  // What the "Notifications" controls should show.
  window.mlPushState = function () {
    if (isIOS && !isStandalone) return 'ios-install-first';
    if (!pushSupported) return 'unsupported';
    if (Notification.permission === 'denied') return 'blocked';
    if (Notification.permission === 'granted') return 'on';
    return 'off';
  };

  window.mlEnablePush = async function () {
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') return window.mlPushState();
    const reg = await ready();
    const sub = (await reg.pushManager.getSubscription()) ||
      await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(VAPID) });
    await post('/push/subscribe/', sub.toJSON());
    document.dispatchEvent(new Event('ml-push-on'));
    return 'on';
  };

  window.mlDisablePush = async function () {
    const reg = await ready();
    const sub = await reg.pushManager.getSubscription();
    if (sub) { await post('/push/unsubscribe/', { endpoint: sub.endpoint }); await sub.unsubscribe(); }
    return 'off';
  };

  // Android/desktop Chrome: our own "Install app" button instead of the browser's mini-bar.
  let installPrompt = null;
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    installPrompt = e;
    document.dispatchEvent(new Event('ml-can-install'));
  });
  window.mlCanInstall = () => !!installPrompt;
  // Returns 'accepted', 'dismissed', or 'unavailable' (caller then shows manual steps).
  window.mlInstall = async () => {
    if (!installPrompt) return 'unavailable';
    try {
      installPrompt.prompt();
      const { outcome } = await installPrompt.userChoice;
      installPrompt = null;
      return outcome;
    } catch (e) {
      console.warn('Install prompt not available', e);
      return 'unavailable';
    }
  };
  window.mlIsIOS = isIOS;
  window.mlIsStandalone = isStandalone;
})();
