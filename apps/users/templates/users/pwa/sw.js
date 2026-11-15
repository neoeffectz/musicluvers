// Music Luvers service worker: phone notifications + a friendly offline screen.
const OFFLINE_HTML = '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">' +
  '<body style="margin:0;background:#101216;color:#fff;font-family:-apple-system,sans-serif;display:flex;' +
  'align-items:center;justify-content:center;height:100vh;text-align:center;padding:24px">' +
  '<div><div style="font-size:42px">🎵</div><h2>You\'re offline</h2>' +
  '<p style="color:#8e8e93">Music Luvers needs the internet. Check your connection and try again.</p>' +
  '<button onclick="location.reload()" style="margin-top:12px;padding:12px 22px;border:0;border-radius:999px;' +
  'background:#f3582c;color:#fff;font-weight:700;font-size:15px">Try again</button></div></body>';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

// Pages always come from the network (so nothing goes stale); only show the offline screen when it fails.
self.addEventListener('fetch', (event) => {
  if (event.request.mode !== 'navigate') return;
  event.respondWith(fetch(event.request).catch(() =>
    new Response(OFFLINE_HTML, { headers: { 'Content-Type': 'text/html; charset=utf-8' } })));
});

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data ? event.data.text() : '' }; }
  event.waitUntil(self.registration.showNotification(data.title || 'Music Luvers', {
    body: data.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/badge-96.png',
    tag: data.tag || undefined,
    renotify: !!data.tag,
    data: { url: data.url || '/dashboard/' },
  }));
});

// Tapping a notification opens that chat/match, reusing an open Music Luvers window if there is one.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = new URL(event.notification.data && event.notification.data.url || '/dashboard/', self.location.origin).href;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of windows) {
      if (new URL(client.url).origin === self.location.origin && 'focus' in client) {
        await client.focus();
        if ('navigate' in client) return client.navigate(url);
        return;
      }
    }
    return self.clients.openWindow(url);
  })());
});
