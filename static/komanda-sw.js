// Prestonica Komanda: servisni radnik. Čuva samo ljusku aplikacije da se otvara
// trenutno i kad je mreža loša. Podaci (/api/stat) se NIKADA ne keširaju -
// brojevi u aplikaciji su uvek sa servisa, nikad stari.

const KES = "komanda-v2";
const LJUSKA = ["/statistika.html", "/komanda.webmanifest", "/og/ikona-192.png", "/og/ikona-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(KES).then((c) => c.addAll(LJUSKA)).then(() => self.skipWaiting()).catch(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((k) => Promise.all(k.filter((x) => x !== KES).map((x) => caches.delete(x))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (u.origin !== location.origin) return;
  if (u.pathname.startsWith("/api/")) return;          // podaci idu pravo na mrežu

  // mreža prvo, keš kao mreža za nuždu: aplikacija se sama osvežava posle deploya
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        if (r && r.ok && LJUSKA.some((p) => u.pathname === p)) {
          const kopija = r.clone();
          caches.open(KES).then((c) => c.put(e.request, kopija)).catch(() => {});
        }
        return r;
      })
      .catch(() => caches.match(e.request).then((r) => r || caches.match("/statistika.html")))
  );
});
