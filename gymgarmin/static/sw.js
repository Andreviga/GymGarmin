// Service worker: a casca do app (HTML) vem da rede quando há sinal; arquivos estáticos (versionados com ?v=)
// abrem na hora do cache e são atualizados em segundo plano. Dados (/api) nunca passam por aqui.
const CACHE = "hybridlab-v6";
const SHELL = ["/", "/static/style.css?v=hl6", "/static/app.js?v=hl6", "/static/icon.svg", "/manifest.webmanifest"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", (e) => e.waitUntil(
  caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  if (url.pathname.startsWith("/static/")) {
    // estáticos: cache primeiro, atualiza por trás
    e.respondWith(caches.open(CACHE).then(async (c) => {
      const hit = await c.match(e.request);
      const net = fetch(e.request).then((r) => { if (r.ok) c.put(e.request, r.clone()); return r; }).catch(() => hit);
      return hit || net;
    }));
    return;
  }
  // HTML e manifesto: rede primeiro (pega versão nova), cache se estiver sem sinal
  e.respondWith(fetch(e.request).then((r) => { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); return r; })
    .catch(() => caches.match(e.request).then((m) => m || caches.match("/"))));
});
