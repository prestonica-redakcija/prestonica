// Prestonica: interna statistika i analitika (Cloudflare Worker + D1 baza).
// Ruta: prestonica.rs/api/stat*        Veza sa bazom: D1, ime promenljive DB
//
// ULAZ (sa sajta, bez ključa — šalje ga brojač u templates/base.html):
//   POST /api/stat/v   {id,p,r,n,s,w,u, c?,dn?,dp?,sn?}   pregled strane
//   POST /api/stat/x   {id,sek,dub}                        izlaz: vreme na strani i dubina čitanja
//
// IZLAZ (za aplikaciju, traži zaglavlje X-Kljuc = tajna STAT_KLJUC):
//   GET  /api/stat/izvestaj?od=<ms>&do=<ms>   pun izveštaj o portalu
//   GET  /api/stat/uzivo                      ko je na sajtu sada
//   GET  /api/stat/google?od=&do=             Google Analytics 4 + Search Console
//   GET  /api/stat/youtube                    kanal i poslednji videi
//   GET  /api/stat/x-mreza                    nalog @Prestonicars i poslednje objave
//   GET  /api/stat/produkcija                 koliko portal objavljuje (šalje GitHub Actions)
//   POST /api/stat/produkcija                 upis iz GitHub Actions (zaglavlje X-Upis = STAT_UPIS)
//
// Privatnost: IP adresa se nikada ne čuva. Posetilac se u toku jednog dana prepoznaje
// preko jednosmernog otiska (hash) adrese, pregledača i dana, uz tajnu so (STAT_SO).
// Povratnici se broje samo za čitaoce koji su prihvatili kolačić (pr_ok=1).

const TZ = "Europe/Belgrade";
const CUVAJ_DANA = 400;          // koliko dugo se čuvaju pojedinačni pregledi
const ZIVO_MIN = 5;              // "na sajtu sada" = pregled u poslednjih 5 minuta
const KES_MIN = { google: 10, youtube: 30, x: 180 };  // keširanje stranih API-ja, u minutima;
// X se naplaćuje po pozivu, pa se čita ređe: 8 puta dnevno umesto 48

const ORIGINS = ["https://prestonica.rs", "https://www.prestonica.rs"];
let spremno = false;
let tokenGoogle = null;          // {t: istek u ms, v: access token}

/* ---------------------------------------------------------------- pomoćno */

function json(data, status, origin, keš) {
  return new Response(JSON.stringify(data), {
    status: status || 200,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Access-Control-Allow-Origin": ORIGINS.includes(origin) ? origin : ORIGINS[0],
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, X-Kljuc, X-Upis",
      "Vary": "Origin",
      "Cache-Control": keš || "no-store",
    },
  });
}

async function hash(text) {
  const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(b)).map((x) => x.toString(16).padStart(2, "0")).join("");
}

// poređenje ključa koje ne otkriva dužinu ni sadržaj kroz vreme izvršavanja
function istiKljuc(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

// beogradski datum, sat i dan nedelje za dati trenutak
const DELOVI = new Intl.DateTimeFormat("en-GB", {
  timeZone: TZ, hour12: false, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", weekday: "short",
});
const DOW = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
function vreme(ms) {
  const o = {};
  for (const p of DELOVI.formatToParts(new Date(ms))) o[p.type] = p.value;
  return {
    dan: o.year + "-" + o.month + "-" + o.day,
    sat: (+o.hour) % 24,
    dow: DOW[o.weekday] != null ? DOW[o.weekday] : 0,
  };
}

function broj(v, pod) { const n = parseInt(v, 10); return Number.isFinite(n) ? n : pod; }
function tekst(v, max) { return v == null ? "" : String(v).slice(0, max || 300); }

/* ------------------------------------------------- prepoznavanje izvora */

const MREZE = [
  [/(^|\.)(x|twitter)\.com$/i, "X"], [/(^|\.)t\.co$/i, "X"],
  [/(^|\.)facebook\.com$|(^|\.)fb\.(com|me)$/i, "Facebook"],
  [/(^|\.)instagram\.com$/i, "Instagram"],
  [/(^|\.)t\.me$|(^|\.)telegram\./i, "Telegram"],
  [/viber/i, "Viber"], [/whatsapp/i, "WhatsApp"],
  [/(^|\.)(tiktok|youtube|youtu\.be|linkedin|reddit|threads)\./i, "Ostale mreže"],
];
const PRETRAGE = [
  [/news\.google\./i, "Google vesti"],
  [/(^|\.)google\./i, "Google"],
  [/(^|\.)(bing|duckduckgo|yahoo|yandex|ecosia|brave|startpage)\./i, "Ostali pretraživači"],
];

function izvor(ref, utm) {
  if (utm) {
    const u = utm.toLowerCase();
    if (/twitter|^x$/.test(u)) return "X";
    if (/facebook/.test(u)) return "Facebook";
    if (/instagram/.test(u)) return "Instagram";
    if (/google/.test(u)) return "Google";
  }
  if (!ref) return "Direktno";
  let h = "";
  try { h = new URL(ref).hostname.replace(/^www\./, ""); } catch (e) { return "Drugi sajtovi"; }
  if (/(^|\.)prestonica\.rs$/i.test(h)) return "Interno";
  for (const [re, ime] of PRETRAGE) if (re.test(h)) return ime;
  for (const [re, ime] of MREZE) if (re.test(h)) return ime;
  return "Drugi sajtovi";
}

// kolona u tabeli najčitanijih: x / g / f / d / i (ostalo se računa kao razlika)
function kolona(iz) {
  if (iz === "X") return "x";
  if (iz === "Google" || iz === "Google vesti") return "g";
  if (iz === "Facebook") return "f";
  if (iz === "Direktno") return "d";
  if (iz === "Interno") return "i";
  return "";
}

function refDomen(ref) {
  if (!ref) return "";
  try {
    const u = new URL(ref);
    const h = u.hostname.replace(/^www\./, "");
    if (/(^|\.)prestonica\.rs$/i.test(h)) return "";
    return h;
  } catch (e) { return ""; }
}

/* ---------------------------------------------- prepoznavanje uređaja */

function uredjaj(ua, w) {
  if (/\b(ipad|tablet)\b|android(?!.*mobile)/i.test(ua)) return "Tablet";
  if (/mobile|iphone|ipod|android|windows phone/i.test(ua)) return "Mobilni";
  if (w && w <= 820) return "Mobilni";
  return "Računar";
}
function sistem(ua) {
  if (/iphone|ipad|ipod/i.test(ua)) return "iOS";
  if (/android/i.test(ua)) return "Android";
  if (/windows nt/i.test(ua)) return "Windows";
  if (/mac os x/i.test(ua)) return "macOS";
  if (/cros/i.test(ua)) return "ChromeOS";
  if (/linux/i.test(ua)) return "Linux";
  return "Ostalo";
}
function pregledac(ua) {
  if (/edg\//i.test(ua)) return "Edge";
  if (/opr\/|opera/i.test(ua)) return "Opera";
  if (/samsungbrowser/i.test(ua)) return "Samsung Internet";
  if (/firefox|fxios/i.test(ua)) return "Firefox";
  if (/chrome|crios/i.test(ua)) return "Chrome";
  if (/safari/i.test(ua)) return "Safari";
  return "Ostalo";
}
// grubo odbijanje automatskih posetilaca
function robot(ua) {
  return !ua || /bot|crawler|spider|crawl|slurp|headless|preview|monitor|curl|wget|python-requests|facebookexternalhit|bingpreview|lighthouse|pingdom|uptime/i.test(ua);
}

/* ---------------------------------------------------------- baza: šeme */

async function pripremi(env) {
  if (spremno) return;
  await env.DB.exec("CREATE TABLE IF NOT EXISTS pregledi (id TEXT PRIMARY KEY, t INTEGER NOT NULL, dan TEXT NOT NULL, sat INTEGER NOT NULL, dow INTEGER NOT NULL, put TEXT NOT NULL, naslov TEXT, rubrika TEXT, izvor TEXT, ref TEXT, uredjaj TEXT, sistem TEXT, pregledac TEXT, zemlja TEXT, grad TEXT, sesija TEXT, ck TEXT, dn INTEGER, dp INTEGER, sn INTEGER, sek INTEGER, dub INTEGER)");
  await env.DB.exec("CREATE INDEX IF NOT EXISTS i_pregledi_t ON pregledi (t)");
  await env.DB.exec("CREATE INDEX IF NOT EXISTS i_pregledi_dan ON pregledi (dan)");
  await env.DB.exec("CREATE TABLE IF NOT EXISTS kes (kljuc TEXT PRIMARY KEY, t INTEGER NOT NULL, telo TEXT NOT NULL)");
  await env.DB.exec("CREATE TABLE IF NOT EXISTS produkcija (dan TEXT PRIMARY KEY, t INTEGER NOT NULL, telo TEXT NOT NULL)");
  spremno = true;
}

/* ---------------------------------------------------------- ULAZ: /v /x */

async function upisPregled(req, env, telo) {
  const ua = req.headers.get("User-Agent") || "";
  if (robot(ua)) return;                               // roboti se ne broje
  const id = tekst(telo.id, 24);
  if (!/^[a-z0-9]{8,24}$/i.test(id)) return;

  const t = Date.now(), v = vreme(t), cf = req.cf || {};
  const ip = req.headers.get("CF-Connecting-IP") || "";
  const so = env.STAT_SO || "prestonica";
  // otisak posetioca važi samo za jedan dan: isti čovek sutra = novi posetilac
  const sesija = (await hash(ip + "|" + ua + "|" + v.dan + "|" + so)).slice(0, 32);

  const ref = tekst(telo.r, 400);
  const utm = tekst(telo.u, 60);
  const iz = izvor(ref, utm);
  const w = broj(telo.w, 0);

  await env.DB.prepare(
    "INSERT OR IGNORE INTO pregledi (id,t,dan,sat,dow,put,naslov,rubrika,izvor,ref,uredjaj,sistem,pregledac,zemlja,grad,sesija,ck,dn,dp,sn,sek,dub) " +
    "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17,?18,?19,?20,NULL,NULL)"
  ).bind(
    id, t, v.dan, v.sat, v.dow,
    tekst(telo.p, 300) || "/", tekst(telo.n, 300), tekst(telo.s, 60),
    iz, refDomen(ref),
    uredjaj(ua, w), sistem(ua), pregledac(ua),
    tekst(cf.country, 4), tekst(cf.city, 80),
    sesija,
    /^[a-z0-9]{12,24}$/.test(tekst(telo.c, 24)) ? telo.c : null,
    telo.dn != null ? broj(telo.dn, null) : null,
    telo.dp != null ? broj(telo.dp, null) : null,
    telo.sn != null ? broj(telo.sn, null) : null
  ).run();
}

async function upisIzlaz(env, telo) {
  const id = tekst(telo.id, 24);
  if (!/^[a-z0-9]{8,24}$/i.test(id)) return;
  const sek = Math.min(broj(telo.sek, 0), 3600);       // više od sata se ne računa
  const dub = Math.max(0, Math.min(broj(telo.dub, 0), 100));
  if (sek <= 0) return;
  await env.DB.prepare("UPDATE pregledi SET sek=?2, dub=?3 WHERE id=?1 AND sek IS NULL").bind(id, sek, dub).run();
}

/* ------------------------------------------------------- IZLAZ: izveštaj */

const UK = "SELECT COUNT(*) v, COUNT(DISTINCT sesija) u, " +
  "AVG(CASE WHEN sek IS NOT NULL THEN sek END) sek, " +
  "SUM(CASE WHEN sek IS NOT NULL THEN 1 ELSE 0 END) merljivo, " +
  "SUM(CASE WHEN dub >= 85 THEN 1 ELSE 0 END) kraj, " +
  "SUM(CASE WHEN sek IS NOT NULL AND sek <= 10 THEN 1 ELSE 0 END) brzo " +
  "FROM pregledi WHERE t >= ?1 AND t < ?2";

async function izvestaj(env, od, dok) {
  const trajanje = dok - od;
  const posat = trajanje <= 50 * 36e5;                 // do ~2 dana: grafik po satu
  const kljucNiza = posat ? "dan || ' ' || printf('%02d', sat)" : "dan";
  const rOd = od - trajanje, rDo = od;                 // prethodni period iste dužine

  const red = (sql, ...a) => env.DB.prepare(sql).bind(...a).all().then((r) => r.results || []);
  const jedan = (sql, ...a) => env.DB.prepare(sql).bind(...a).first();

  const [ukupno, ranije, niz, nizRanije, top, sati, dani, izvori, refovi, rubrike, uredjaji, sistemi, zemlje, gradovi, pregledaci, povZbir, povDani, povRazmaci] = await Promise.all([
    jedan(UK, od, dok),
    jedan(UK, rOd, rDo),
    red("SELECT " + kljucNiza + " k, COUNT(*) v, COUNT(DISTINCT sesija) u FROM pregledi WHERE t>=?1 AND t<?2 GROUP BY k", od, dok),
    red("SELECT " + kljucNiza + " k, COUNT(*) v FROM pregledi WHERE t>=?1 AND t<?2 GROUP BY k", rOd, rDo),
    red(
      "SELECT put, MAX(naslov) naslov, MAX(rubrika) rubrika, COUNT(*) v, COUNT(DISTINCT sesija) u, " +
      "AVG(CASE WHEN sek IS NOT NULL THEN sek END) sek, AVG(CASE WHEN dub IS NOT NULL THEN dub END) dub, " +
      "SUM(CASE WHEN uredjaj='Mobilni' THEN 1 ELSE 0 END) mob, " +
      "SUM(CASE WHEN izvor='X' THEN 1 ELSE 0 END) x, " +
      "SUM(CASE WHEN izvor IN ('Google','Google vesti') THEN 1 ELSE 0 END) g, " +
      "SUM(CASE WHEN izvor='Facebook' THEN 1 ELSE 0 END) f, " +
      "SUM(CASE WHEN izvor='Direktno' THEN 1 ELSE 0 END) d, " +
      "SUM(CASE WHEN izvor='Interno' THEN 1 ELSE 0 END) i " +
      "FROM pregledi WHERE t>=?1 AND t<?2 GROUP BY put ORDER BY v DESC LIMIT 400", od, dok),
    red("SELECT sat k, COUNT(*) v FROM pregledi WHERE t>=?1 AND t<?2 GROUP BY sat", od, dok),
    red("SELECT dow k, COUNT(*) v FROM pregledi WHERE t>=?1 AND t<?2 GROUP BY dow", od, dok),
    red("SELECT izvor k, COUNT(*) v, COUNT(DISTINCT sesija) u FROM pregledi WHERE t>=?1 AND t<?2 GROUP BY izvor ORDER BY v DESC", od, dok),
    red("SELECT ref k, COUNT(*) v FROM pregledi WHERE t>=?1 AND t<?2 AND ref<>'' AND ref IS NOT NULL GROUP BY ref ORDER BY v DESC LIMIT 20", od, dok),
    red("SELECT rubrika k, COUNT(*) v, AVG(CASE WHEN sek IS NOT NULL THEN sek END) sek FROM pregledi WHERE t>=?1 AND t<?2 GROUP BY rubrika ORDER BY v DESC", od, dok),
    red("SELECT uredjaj k, COUNT(*) v FROM pregledi WHERE t>=?1 AND t<?2 GROUP BY uredjaj ORDER BY v DESC", od, dok),
    red("SELECT sistem k, COUNT(*) v FROM pregledi WHERE t>=?1 AND t<?2 GROUP BY sistem ORDER BY v DESC", od, dok),
    red("SELECT zemlja k, COUNT(*) v FROM pregledi WHERE t>=?1 AND t<?2 GROUP BY zemlja ORDER BY v DESC LIMIT 25", od, dok),
    red("SELECT grad k, MAX(zemlja) z, COUNT(*) v FROM pregledi WHERE t>=?1 AND t<?2 AND grad<>'' AND grad IS NOT NULL GROUP BY grad, zemlja ORDER BY v DESC LIMIT 25", od, dok),
    red("SELECT pregledac k, COUNT(*) v FROM pregledi WHERE t>=?1 AND t<?2 GROUP BY pregledac ORDER BY v DESC", od, dok),
    jedan("SELECT COUNT(DISTINCT ck) sa, COUNT(DISTINCT CASE WHEN dn<=1 THEN ck END) novi, COUNT(DISTINCT CASE WHEN dn>1 THEN ck END) stari, AVG(sn) sn, AVG(CASE WHEN dp>0 THEN dp END) dp FROM pregledi WHERE t>=?1 AND t<?2 AND ck IS NOT NULL", od, dok),
    red("SELECT CASE WHEN dn=1 THEN '1. dan' WHEN dn<=3 THEN '2-3 dana' WHEN dn<=7 THEN '4-7 dana' WHEN dn<=30 THEN '8-30 dana' ELSE 'preko 30 dana' END k, COUNT(DISTINCT ck) v FROM pregledi WHERE t>=?1 AND t<?2 AND ck IS NOT NULL AND dn IS NOT NULL GROUP BY k ORDER BY v DESC", od, dok),
    red("SELECT CASE WHEN dp=1 THEN 'sutra opet' WHEN dp<=3 THEN 'posle 2-3 dana' WHEN dp<=7 THEN 'posle nedelje' ELSE 'posle duže pauze' END k, COUNT(DISTINCT ck) v FROM pregledi WHERE t>=?1 AND t<?2 AND ck IS NOT NULL AND dp>0 GROUP BY k ORDER BY v DESC", od, dok),
  ]);

  return {
    od, do: dok, hourly: posat, generisano: Date.now(),
    ukupno: ukupno || {}, ranije: ranije || {},
    niz, nizRanije, top, sati, dani,
    izvori, refovi, rubrike, uredjaji, sistemi, zemlje, gradovi, pregledaci,
    povratnici: { zbir: povZbir || {}, sn: (povZbir || {}).sn || 1, dp: (povZbir || {}).dp || 0, dani: povDani, razmaci: povRazmaci },
  };
}

async function uzivo(env) {
  const sada = Date.now(), granica = sada - ZIVO_MIN * 6e4, pola = sada - 30 * 6e4;
  const red = (sql, ...a) => env.DB.prepare(sql).bind(...a).all().then((r) => r.results || []);
  const [a, minuti, izvori, stranice] = await Promise.all([
    env.DB.prepare("SELECT COUNT(DISTINCT sesija) n FROM pregledi WHERE t>=?1").bind(granica).first(),
    red("SELECT t/60000 m, COUNT(*) v FROM pregledi WHERE t>=?1 GROUP BY m", pola),
    red("SELECT izvor k, COUNT(*) v FROM pregledi WHERE t>=?1 GROUP BY izvor ORDER BY v DESC", pola),
    red("SELECT put, MAX(naslov) naslov, COUNT(*) v FROM pregledi WHERE t>=?1 GROUP BY put ORDER BY v DESC LIMIT 10", granica),
  ]);
  return { aktivni: (a || {}).n || 0, sada, minuti, izvori, stranice };
}

/* ------------------------------------------------------------ keširanje */

async function saKesom(env, kljuc, minuta, posao) {
  const r = await env.DB.prepare("SELECT t, telo FROM kes WHERE kljuc=?1").bind(kljuc).first();
  const svez = r && Date.now() - r.t < minuta * 6e4;
  if (svez) { const o = JSON.parse(r.telo); o._kes = Math.round((Date.now() - r.t) / 1000); return o; }
  try {
    const o = await posao();
    o._kes = 0;
    await env.DB.prepare("INSERT INTO kes (kljuc,t,telo) VALUES (?1,?2,?3) ON CONFLICT(kljuc) DO UPDATE SET t=?2, telo=?3")
      .bind(kljuc, Date.now(), JSON.stringify(o)).run();
    return o;
  } catch (e) {
    // ako strani API padne, radije se pokaže zadnji poznat podatak nego ništa
    if (r) { const o = JSON.parse(r.telo); o._kes = Math.round((Date.now() - r.t) / 1000); o._greska = String(e.message || e); return o; }
    throw e;
  }
}

/* ------------------------------- podaci koje portal sam šalje (YouTube, X) */
// Ključevi za YouTube i X već postoje u GitHub Actions, pa se ne dupliraju ovde:
// portal pri svakom osvežavanju pošalje stanje svojih naloga, a servis ga čuva.

async function primiIzvore(env, telo) {
  const t = Date.now();
  for (const kljuc of ["youtube", "x"]) {
    if (!telo[kljuc] || typeof telo[kljuc] !== "object") continue;
    await env.DB.prepare("INSERT INTO kes (kljuc,t,telo) VALUES (?1,?2,?3) ON CONFLICT(kljuc) DO UPDATE SET t=?2, telo=?3")
      .bind("push:" + kljuc, t, JSON.stringify(telo[kljuc])).run();
  }
}

async function poslato(env, kljuc) {
  const r = await env.DB.prepare("SELECT t, telo FROM kes WHERE kljuc=?1").bind("push:" + kljuc).first();
  if (!r) return null;
  const o = JSON.parse(r.telo);
  o._kes = Math.round((Date.now() - r.t) / 1000);
  o._izvor = "portal";
  return o;
}

/* ------------------------------------------------- Google: pristupni token */

function pemUBajtove(pem) {
  // Ključ se prihvata i kada je prekopiran iz JSON fajla, gde su prelomi redova
  // zapisani kao dva znaka \n, i kada ima prave prelome.
  const b64 = String(pem).replace(/\\n/g, "\n").replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
  const bin = atob(b64), out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
function b64url(buf) {
  const bin = typeof buf === "string" ? buf : String.fromCharCode(...new Uint8Array(buf));
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function googleToken(env) {
  if (tokenGoogle && tokenGoogle.t > Date.now() + 6e4) return tokenGoogle.v;
  const email = env.GOOGLE_SA_EMAIL, kljuc = env.GOOGLE_SA_KEY;
  if (!email || !kljuc) throw new Error("Google servisni nalog nije podešen (GOOGLE_SA_EMAIL, GOOGLE_SA_KEY).");
  const sada = Math.floor(Date.now() / 1000);
  const zahtev = {
    iss: email, aud: "https://oauth2.googleapis.com/token", iat: sada, exp: sada + 3600,
    scope: "https://www.googleapis.com/auth/analytics.readonly https://www.googleapis.com/auth/webmasters.readonly https://www.googleapis.com/auth/yt-analytics.readonly",
  };
  const glava = b64url(JSON.stringify({ alg: "RS256", typ: "JWT" })) + "." + b64url(JSON.stringify(zahtev));
  const ck = await crypto.subtle.importKey("pkcs8", pemUBajtove(kljuc),
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"]);
  const potpis = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", ck, new TextEncoder().encode(glava));
  const jwt = glava + "." + b64url(potpis);

  const r = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: "grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion=" + encodeURIComponent(jwt),
  });
  const j = await r.json();
  if (!r.ok || !j.access_token) throw new Error("Google ne daje token: " + (j.error_description || j.error || r.status));
  tokenGoogle = { t: Date.now() + (j.expires_in || 3600) * 1000, v: j.access_token };
  return tokenGoogle.v;
}

/* --------------------------------------- Google Analytics 4 + Search Console */

function danKljuc(ms) { return vreme(ms).dan; }

async function ga4(token, imanje, od, dok) {
  const telo = (zahtev) => fetch("https://analyticsdata.googleapis.com/v1beta/properties/" + imanje + ":runReport", {
    method: "POST", headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
    body: JSON.stringify(Object.assign({ dateRanges: [{ startDate: danKljuc(od), endDate: danKljuc(dok) }] }, zahtev)),
  }).then(async (r) => { const j = await r.json(); if (!r.ok) throw new Error("GA4: " + ((j.error || {}).message || r.status)); return j; });

  const redovi = (j) => (j.rows || []).map((r) => ({
    k: (r.dimensionValues || []).map((d) => d.value).join(" · "),
    v: +(((r.metricValues || [])[0] || {}).value || 0),
    v2: +(((r.metricValues || [])[1] || {}).value || 0),
  }));

  const [zbir, poDanu, kanali, strane, zemlje] = await Promise.all([
    telo({ metrics: [{ name: "screenPageViews" }, { name: "totalUsers" }, { name: "sessions" }, { name: "averageSessionDuration" }, { name: "engagementRate" }, { name: "newUsers" }] }),
    telo({ dimensions: [{ name: "date" }], metrics: [{ name: "screenPageViews" }, { name: "totalUsers" }], orderBys: [{ dimension: { dimensionName: "date" } }], limit: 400 }),
    telo({ dimensions: [{ name: "sessionDefaultChannelGroup" }], metrics: [{ name: "sessions" }, { name: "totalUsers" }], limit: 20 }),
    telo({ dimensions: [{ name: "pagePath" }, { name: "pageTitle" }], metrics: [{ name: "screenPageViews" }, { name: "totalUsers" }], orderBys: [{ metric: { metricName: "screenPageViews" }, desc: true }], limit: 50 }),
    telo({ dimensions: [{ name: "country" }], metrics: [{ name: "totalUsers" }], orderBys: [{ metric: { metricName: "totalUsers" }, desc: true }], limit: 20 }),
  ]);

  const m = (((zbir.rows || [])[0] || {}).metricValues || []).map((x) => +x.value || 0);
  return {
    zbir: { pregledi: m[0] || 0, korisnici: m[1] || 0, sesije: m[2] || 0, trajanje: m[3] || 0, angazovanje: (m[4] || 0) * 100, novi: m[5] || 0 },
    poDanu: redovi(poDanu), kanali: redovi(kanali), strane: redovi(strane), zemlje: redovi(zemlje),
  };
}

async function gsc(token, sajt, od, dok) {
  const pitaj = (zahtev) => fetch("https://searchconsole.googleapis.com/webmasters/v3/sites/" + encodeURIComponent(sajt) + "/searchAnalytics/query", {
    method: "POST", headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
    body: JSON.stringify(Object.assign({ startDate: danKljuc(od), endDate: danKljuc(dok) }, zahtev)),
  }).then(async (r) => { const j = await r.json(); if (!r.ok) throw new Error("Search Console: " + ((j.error || {}).message || r.status)); return j; });

  const redovi = (j) => (j.rows || []).map((r) => ({
    k: (r.keys || []).join(" · "), klik: r.clicks || 0, imp: r.impressions || 0,
    ctr: (r.ctr || 0) * 100, poz: r.position || 0,
  }));

  const [zbir, poDanu, upiti, strane, zemlje, uredjaji] = await Promise.all([
    pitaj({ dimensions: [] }),
    pitaj({ dimensions: ["date"], rowLimit: 400 }),
    pitaj({ dimensions: ["query"], rowLimit: 100 }),
    pitaj({ dimensions: ["page"], rowLimit: 50 }),
    pitaj({ dimensions: ["country"], rowLimit: 15 }),
    pitaj({ dimensions: ["device"], rowLimit: 5 }),
  ]);
  const z = (zbir.rows || [])[0] || {};
  return {
    zbir: { klik: z.clicks || 0, imp: z.impressions || 0, ctr: (z.ctr || 0) * 100, poz: z.position || 0 },
    poDanu: redovi(poDanu), upiti: redovi(upiti), strane: redovi(strane), zemlje: redovi(zemlje), uredjaji: redovi(uredjaji),
  };
}

async function google(env, od, dok) {
  const imanje = env.GA4_PROPERTY_ID, sajt = env.GSC_SITE_URL || "sc-domain:prestonica.rs";
  if (!imanje && !env.GOOGLE_SA_EMAIL) throw new Error("Google još nije podešen: dodaj tajne GOOGLE_SA_EMAIL, GOOGLE_SA_KEY i GA4_PROPERTY_ID.");
  const token = await googleToken(env);
  const [a, s] = await Promise.all([
    imanje ? ga4(token, imanje, od, dok).catch((e) => ({ greska: String(e.message || e) }))
           : { iskljucen: true, greska: "Google Analytics se namerno ne koristi: sopstveno merenje na kartici Portal je tačnije jer ga blokatori reklama ne seku." },
    gsc(token, sajt, od, dok).catch((e) => ({ greska: String(e.message || e) })),
  ]);
  // Search Console kasni oko 2 dana; to se mora reći, inače izgleda kao pad saobraćaja
  return { ga4: a, gsc: s, sajt, napomena: "Search Console podaci kasne 1-3 dana.", generisano: Date.now() };
}

/* ------------------------------------------------------------- YouTube */

async function youtube(env) {
  const kljuc = env.YT_API_KEY, kanal = env.YT_CHANNEL_ID;
  if (!kljuc || !kanal) throw new Error("YouTube još nije podešen: dodaj tajne YT_API_KEY i YT_CHANNEL_ID.");
  const api = (put) => fetch("https://www.googleapis.com/youtube/v3/" + put + "&key=" + kljuc)
    .then(async (r) => { const j = await r.json(); if (!r.ok) throw new Error("YouTube: " + (((j.error || {}).message) || r.status)); return j; });

  const k = await api("channels?part=snippet,statistics,contentDetails&id=" + kanal);
  const c = (k.items || [])[0];
  if (!c) throw new Error("YouTube kanal nije nađen (proveri YT_CHANNEL_ID).");
  const lista = ((c.contentDetails || {}).relatedPlaylists || {}).uploads;
  const stavke = lista ? await api("playlistItems?part=snippet&maxResults=25&playlistId=" + lista) : { items: [] };
  const ids = (stavke.items || []).map((x) => ((x.snippet || {}).resourceId || {}).videoId).filter(Boolean);
  const v = ids.length ? await api("videos?part=snippet,statistics,contentDetails&id=" + ids.join(",")) : { items: [] };

  const st = c.statistics || {};
  return {
    kanal: {
      ime: (c.snippet || {}).title || "", pretplatnici: +st.subscriberCount || 0,
      pregledi: +st.viewCount || 0, videa: +st.videoCount || 0,
      skriveni: st.hiddenSubscriberCount === true,
    },
    videi: (v.items || []).map((x) => ({
      id: x.id, naslov: (x.snippet || {}).title || "",
      objavljen: (x.snippet || {}).publishedAt || "",
      pregledi: +((x.statistics || {}).viewCount) || 0,
      lajkovi: +((x.statistics || {}).likeCount) || 0,
      komentari: +((x.statistics || {}).commentCount) || 0,
      trajanje: (x.contentDetails || {}).duration || "",
    })).sort((a, b) => (a.objavljen < b.objavljen ? 1 : -1)),
    generisano: Date.now(),
  };
}

/* ------------------------------------------------------------- X (Twitter) */

async function xMreza(env) {
  const token = env.X_BEARER, ime = env.X_USERNAME || "Prestonicars";
  if (!token) throw new Error("X još nije podešen: dodaj tajnu X_BEARER (token iz X Developer portala).");
  const api = (put) => fetch("https://api.x.com/2/" + put, { headers: { Authorization: "Bearer " + token } })
    .then(async (r) => {
      const j = await r.json();
      if (r.status === 429) throw new Error("X API je privremeno iscrpeo kvotu. Prikazan je zadnji poznat podatak.");
      if (!r.ok) throw new Error("X: " + ((j.title || (j.errors || [{}])[0].message) || r.status));
      return j;
    });

  const u = await api("users/by/username/" + encodeURIComponent(ime) + "?user.fields=public_metrics,created_at,description");
  const nalog = (u.data || {});
  const pm = nalog.public_metrics || {};
  let objave = [];
  try {
    const t = await api("users/" + nalog.id + "/tweets?max_results=50&exclude=replies&tweet.fields=public_metrics,created_at,entities");
    objave = (t.data || []).map((x) => {
      const m = x.public_metrics || {};
      const linkovi = ((x.entities || {}).urls || []).length > 0;
      return {
        id: x.id, tekst: (x.text || "").slice(0, 200), vreme: x.created_at,
        imp: m.impression_count || 0, lajk: m.like_count || 0, rt: m.retweet_count || 0,
        odg: m.reply_count || 0, cit: m.quote_count || 0, linkKlik: linkovi,
      };
    });
  } catch (e) { objave = []; }

  return {
    nalog: { ime: nalog.username || ime, prikaz: nalog.name || "", pratioci: pm.followers_count || 0, prati: pm.following_count || 0, objava: pm.tweet_count || 0, od: nalog.created_at || "" },
    objave, generisano: Date.now(),
  };
}

/* ----------------------------------------------------------- produkcija */

async function produkcijaUpis(env, telo) {
  const dan = vreme(Date.now()).dan;
  await env.DB.prepare("INSERT INTO produkcija (dan,t,telo) VALUES (?1,?2,?3) ON CONFLICT(dan) DO UPDATE SET t=?2, telo=?3")
    .bind(dan, Date.now(), JSON.stringify(telo)).run();
  await env.DB.prepare("DELETE FROM produkcija WHERE dan < ?1")
    .bind(vreme(Date.now() - 120 * 864e5).dan).run();
}

// Portal pri svakom gradjenju objavi produkcija.json na sajtu. Ako GitHub Actions
// jos ne salje podatke, servis ih sam povuce odatle i pamti po danima, pa se
// istorija gradi bez ijedne tajne.
async function produkcijaPovuci(env) {
  const domen = env.SAJT || "prestonica.rs";
  try {
    const r = await fetch(`https://${domen}/produkcija.json`, { cf: { cacheTtl: 60 } });
    if (!r.ok) return null;
    const telo = await r.json();
    if (!telo || typeof telo !== "object" || !telo.dan) return null;
    await env.DB.prepare("INSERT INTO produkcija (dan,t,telo) VALUES (?1,?2,?3) ON CONFLICT(dan) DO UPDATE SET t=?2, telo=?3")
      .bind(String(telo.dan), Date.now(), JSON.stringify(telo)).run();
    return telo;
  } catch (e) { return null; }
}

async function produkcijaCitaj(env) {
  await produkcijaPovuci(env);
  const { results } = await env.DB.prepare("SELECT dan, t, telo FROM produkcija ORDER BY dan DESC LIMIT 60").all();
  const dani = (results || []).map((r) => Object.assign({ dan: r.dan, t: r.t }, JSON.parse(r.telo)));
  if (!dani.length) return { dani: [], poslednje: null, napomena: "Portal još nije objavio produkcija.json. Pojaviće se posle prvog osvežavanja." };
  return { dani, poslednje: dani[0], generisano: Date.now() };
}

/* ------------------------------------------------------------- čišćenje */

async function ocisti(env) {
  await env.DB.prepare("DELETE FROM pregledi WHERE t < ?1").bind(Date.now() - CUVAJ_DANA * 864e5).run();
}

/* ----------------------------------------------------------------- ulaz */

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const origin = req.headers.get("Origin") || "";
    const put = url.pathname.replace(/^\/api\/stat/, "") || "/";

    if (req.method === "OPTIONS") return json({}, 204, origin);
    if (!env.DB) return json({ greska: "D1 baza nije povezana sa servisom (promenljiva DB)." }, 503, origin);
    await pripremi(env);

    // ---- ulaz sa sajta: bez ključa, uvek odgovara 204 da brojač nikad ne koči stranu
    if (req.method === "POST" && (put === "/v" || put === "/x")) {
      let telo = null;
      try { telo = JSON.parse(await req.text()); } catch (e) { return new Response(null, { status: 204 }); }
      if (telo && typeof telo === "object") {
        const posao = put === "/v" ? upisPregled(req, env, telo) : upisIzlaz(env, telo);
        ctx.waitUntil(posao.catch(() => {}));
        if (put === "/v" && Math.random() < 0.002) ctx.waitUntil(ocisti(env).catch(() => {}));
      }
      return new Response(null, { status: 204 });
    }

    // ---- stanje YouTube kanala i X naloga iz GitHub Actions
    if (req.method === "POST" && put === "/izvori") {
      if (!env.STAT_UPIS || !istiKljuc(req.headers.get("X-Upis") || "", env.STAT_UPIS))
        return json({ greska: "Nije dozvoljeno." }, 401, origin);
      try {
        await primiIzvore(env, JSON.parse(await req.text()));
        return json({ ok: true }, 200, origin);
      } catch (e) { return json({ greska: String(e.message || e) }, 400, origin); }
    }

    // ---- upis produkcije iz GitHub Actions
    if (req.method === "POST" && put === "/produkcija") {
      if (!env.STAT_UPIS || !istiKljuc(req.headers.get("X-Upis") || "", env.STAT_UPIS))
        return json({ greska: "Nije dozvoljeno." }, 401, origin);
      try {
        const telo = JSON.parse(await req.text());
        await produkcijaUpis(env, telo);
        return json({ ok: true }, 200, origin);
      } catch (e) { return json({ greska: String(e.message || e) }, 400, origin); }
    }

    // ---- sve ostalo traži ključ aplikacije
    if (!env.STAT_KLJUC) return json({ greska: "Ključ statistike nije podešen (tajna STAT_KLJUC)." }, 503, origin);
    if (!istiKljuc(req.headers.get("X-Kljuc") || "", env.STAT_KLJUC)) return json({ greska: "Pogrešan PIN." }, 401, origin);

    try {
      if (put === "/izvestaj") {
        const sada = Date.now();
        let od = broj(url.searchParams.get("od"), sada - 864e5);
        let dok = broj(url.searchParams.get("do"), sada);
        if (dok <= od) dok = od + 36e5;
        if (dok - od > 400 * 864e5) od = dok - 400 * 864e5;
        return json(await izvestaj(env, od, dok), 200, origin);
      }
      if (put === "/uzivo") return json(await uzivo(env), 200, origin);

      if (put === "/google") {
        const sada = Date.now();
        const od = broj(url.searchParams.get("od"), sada - 28 * 864e5);
        const dok = broj(url.searchParams.get("do"), sada);
        const k = "google:" + danKljuc(od) + ":" + danKljuc(dok);
        return json(await saKesom(env, k, KES_MIN.google, () => google(env, od, dok)), 200, origin);
      }
      if (put === "/youtube") {
        if (env.YT_API_KEY && env.YT_CHANNEL_ID)
          return json(await saKesom(env, "youtube", KES_MIN.youtube, () => youtube(env)), 200, origin);
        const p = await poslato(env, "youtube");
        if (p) return json(p, 200, origin);
        throw new Error("YouTube još nije povezan. Portal šalje podatke sam pri svakom osvežavanju; sačekaj jedan ciklus ili dodaj tajne YT_API_KEY i YT_CHANNEL_ID.");
      }
      if (put === "/x-mreza") {
        if (env.X_BEARER)
          return json(await saKesom(env, "x", KES_MIN.x, () => xMreza(env)), 200, origin);
        const p = await poslato(env, "x");
        if (p) return json(p, 200, origin);
        throw new Error("X još nije povezan. Portal šalje podatke sam pri svakom osvežavanju; sačekaj jedan ciklus ili dodaj tajnu X_BEARER.");
      }
      if (put === "/produkcija") return json(await produkcijaCitaj(env), 200, origin);

      if (put === "/stanje") {
        const r = await env.DB.prepare("SELECT COUNT(*) n, MIN(t) od, MAX(t) do FROM pregledi").first();
        return json({
          pregledi: (r || {}).n || 0, prvi: (r || {}).od || null, poslednji: (r || {}).do || null,
          podeseno: {
            google: !!(env.GOOGLE_SA_EMAIL && env.GOOGLE_SA_KEY),
            ga4: !!env.GA4_PROPERTY_ID,
            youtube: !!(env.YT_API_KEY && env.YT_CHANNEL_ID),
            x: !!env.X_BEARER, produkcija: !!env.STAT_UPIS,
            youtube_portal: !!(await poslato(env, "youtube")), x_portal: !!(await poslato(env, "x")),
          },
        }, 200, origin);
      }

      return json({ greska: "Nepoznata ruta." }, 404, origin);
    } catch (e) {
      return json({ greska: String(e.message || e) }, 503, origin);
    }
  },
};
