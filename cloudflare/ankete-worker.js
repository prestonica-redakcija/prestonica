// Prestonica: servis za glasove u anketama čitalaca (Cloudflare Worker + D1 baza).
// Ruta: prestonica.rs/api/ankete*   Veza sa bazom: D1, ime promenljive DB.
//
// POST /api/ankete/glas        {"id": "a202611130700", "o": 1}  -> {"ok":true,"tvoj":1,"rezultati":{"0":12,"1":30}}
// GET  /api/ankete/rezultati?ids=a1,a2                           -> {"rezultati":{"a1":{"0":3,"1":5}, ...}}
//
// Koja anketa postoji, koliko ima odgovora i kad ističe, servis čita sa sajta (https://prestonica.rs/ankete.json).
// Jedan glas po anketi po uređaju: čuva se samo jednosmerni otisak (hash) adrese i pregledača, nikad sama IP adresa.

const SITE = "https://prestonica.rs";
const ORIGINS = ["https://prestonica.rs", "https://www.prestonica.rs"];
let ready = false;

function json(data, status, origin, extra) {
  return new Response(JSON.stringify(data), {
    status: status || 200,
    headers: Object.assign({
      "Content-Type": "application/json; charset=utf-8",
      "Access-Control-Allow-Origin": ORIGINS.includes(origin) ? origin : ORIGINS[0],
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Vary": "Origin",
      "Cache-Control": "no-store",
    }, extra || {}),
  });
}

async function setup(env) {
  if (ready) return;
  await env.DB.exec("CREATE TABLE IF NOT EXISTS glasovi (anketa TEXT NOT NULL, kljuc TEXT NOT NULL, opcija INTEGER NOT NULL, t INTEGER NOT NULL, PRIMARY KEY (anketa, kljuc))");
  ready = true;
}

async function polls() {
  const r = await fetch(SITE + "/ankete.json", { cf: { cacheTtl: 120, cacheEverything: true } });
  if (!r.ok) return [];
  return r.json();
}

async function counts(env, ids) {
  const out = {};
  ids.forEach((id) => { out[id] = {}; });
  if (!ids.length) return out;
  const q = "SELECT anketa, opcija, COUNT(*) AS n FROM glasovi WHERE anketa IN (" + ids.map(() => "?").join(",") + ") GROUP BY anketa, opcija";
  const { results } = await env.DB.prepare(q).bind(...ids).all();
  for (const row of results || []) out[row.anketa][String(row.opcija)] = row.n;
  return out;
}

async function sha(text) {
  const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(b)).map((x) => x.toString(16).padStart(2, "0")).join("");
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const origin = req.headers.get("Origin") || "";
    if (req.method === "OPTIONS") return json({}, 204, origin);
    await setup(env);
    const path = url.pathname.replace(/^\/api\/ankete/, "") || "/";

    if (req.method === "GET" && path === "/rezultati") {
      const ids = (url.searchParams.get("ids") || "").split(",").filter((x) => /^a\d{12}x*$/.test(x)).slice(0, 60);
      return json({ rezultati: await counts(env, ids) }, 200, origin, { "Cache-Control": "public, max-age=15" });
    }

    if (req.method === "POST" && path === "/glas") {
      if (origin && !ORIGINS.includes(origin)) return json({ greska: "nedozvoljen izvor" }, 403, origin);
      let body;
      try { body = await req.json(); } catch (e) { return json({ greska: "neispravan zahtev" }, 400, origin); }
      const id = String(body.id || ""), o = Number(body.o);
      if (!/^a\d{12}x*$/.test(id) || !Number.isInteger(o)) return json({ greska: "neispravan zahtev" }, 400, origin);
      const p = (await polls()).find((x) => x.id === id);
      if (!p) return json({ greska: "nema ankete" }, 404, origin);
      if (Date.parse(p.kraj) < Date.now()) return json({ greska: "anketa je završena" }, 410, origin);
      if (o < 0 || o >= p.opcije) return json({ greska: "nema odgovora" }, 400, origin);
      const ip = req.headers.get("CF-Connecting-IP") || "";
      const ua = req.headers.get("User-Agent") || "";
      const kljuc = await sha(id + "|" + ip + "|" + ua + "|" + (env.SOL || "prestonica"));
      const r = await env.DB.prepare("INSERT OR IGNORE INTO glasovi (anketa, kljuc, opcija, t) VALUES (?, ?, ?, ?)").bind(id, kljuc, o, Date.now()).run();
      let tvoj = o;
      if (!r.meta || r.meta.changes === 0) {
        const old = await env.DB.prepare("SELECT opcija FROM glasovi WHERE anketa = ? AND kljuc = ?").bind(id, kljuc).first();
        tvoj = old ? old.opcija : o;
      }
      const c = await counts(env, [id]);
      return json({ ok: true, ponovo: !r.meta || r.meta.changes === 0, tvoj: tvoj, rezultati: c[id] }, 200, origin);
    }

    return json({ greska: "nepoznata putanja" }, 404, origin);
  },
};
