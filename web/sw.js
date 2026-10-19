const CACHE = "run-v4";
const SHELL = ["./", "index.html", "app.js", "charts.js", "style.css", "manifest.webmanifest", "icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// 画面部品はネットワーク優先・オフライン時はキャッシュ。API と地図 (別サイト) はキャッシュしない
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});

self.addEventListener("push", (e) => {
  const data = e.data ? e.data.json() : { title: "ランログ", body: "" };
  e.waitUntil(self.registration.showNotification(data.title, {
    body: data.body,
    icon: "icon-192.png",
    badge: "icon-192.png",
    tag: data.tag || "run",
    data: { url: data.url || "./" },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = new URL(e.notification.data?.url || "./", self.registration.scope).href;
  e.waitUntil(
    self.clients.matchAll({ type: "window" }).then((list) => {
      if (list.length) return list[0].navigate(url).then((c) => c && c.focus());
      return self.clients.openWindow(url);
    })
  );
});
