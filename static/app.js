(function () {
  "use strict";
  var ROOT = document.documentElement.getAttribute("data-root") || "";
  var $ = function (s, el) { return (el || document).querySelector(s); };
  var $$ = function (s, el) { return Array.prototype.slice.call((el || document).querySelectorAll(s)); };

  /* ---------- relativno vreme ---------- */
  var MON = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "avg", "sep", "okt", "nov", "dec"];
  function rel(iso, abs) {
    var d = new Date(iso), s = (Date.now() - d.getTime()) / 1000;
    if (isNaN(s)) return "";
    var hm = ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
    if (abs) return s < 86400 && new Date().getDate() === d.getDate() ? hm : d.getDate() + ". " + MON[d.getMonth()] + " " + hm;
    if (s < 60) return "upravo";
    if (s < 3600) return "pre " + Math.floor(s / 60) + " min";
    if (s < 86400) return "pre " + Math.floor(s / 3600) + " h";
    if (s < 172800) return "juče " + hm;
    return d.getDate() + ". " + MON[d.getMonth()];
  }
  function tickTimes() { $$("[data-t]").forEach(function (el) { var v = rel(el.getAttribute("data-t"), el.hasAttribute("data-abs")); if (v) el.textContent = v; }); }
  tickTimes(); setInterval(tickTimes, 60000);

  /* ---------- zaglavlje i progres ---------- */
  var gut = document.querySelector(".gutters"), wide = window.matchMedia ? window.matchMedia("(min-width: 1680px)") : null, mast = $("#mast"), tb = document.querySelector(".topbar"), prog = $("#progress"), art = $("[data-article]");
  var mobile = window.matchMedia ? window.matchMedia("(max-width: 620px)") : null, navEl = mast && mast.querySelector(".nav"), navStuck = false;
  var navTop = 0; try { navTop = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--sat")) || 0; } catch (e) {}
  function onScroll() {
    if (mast) {
      if (mobile && mobile.matches) {
        if (mast.classList.contains("scrolled")) mast.classList.remove("scrolled"); if (tb) tb.style.opacity = "";
        if (navEl) { var st = navEl.getBoundingClientRect().top <= navTop + 54.5 && window.scrollY > 0; if (st !== navStuck) { navStuck = st; navEl.classList.toggle("stuck", st); } }
      } else { const y = window.scrollY; mast.classList.toggle("senka", y > 4); if (tb) tb.style.opacity = String(Math.max(0, 1 - y / 38)); }
    }
    if (prog && art) {
      var r = art.getBoundingClientRect(), h = art.offsetHeight - window.innerHeight;
      prog.style.width = Math.max(0, Math.min(100, (-r.top / Math.max(1, h)) * 100)) + "%";
    }
  }
  window.addEventListener("scroll", onScroll, { passive: true }); onScroll();
  /* telefon: kad veliki logo ode sa ekrana, odozgo glatko uđe traka sa imenom i pretragom (samo transform, bez pomeranja stranice) */
  var mbar = $("#mbar"), bigLogo = mast && mast.querySelector(".logo");
  if (mbar && bigLogo && "IntersectionObserver" in window) {
    new IntersectionObserver(function (en) {
      var on = !en[0].isIntersecting && en[0].boundingClientRect.top < 0;
      if (mbar.classList.contains("on") !== on) { mbar.classList.toggle("on", on); if (on) mbar.removeAttribute("inert"); else mbar.setAttribute("inert", ""); }
    }, { rootMargin: "-24px 0px 0px 0px", threshold: 0 }).observe(bigLogo);
  }

  /* ---------- toast ---------- */
  var toastEl = $("#toast"), toastT;
  function toast(msg) { if (!toastEl) return; toastEl.textContent = msg; toastEl.classList.add("show"); clearTimeout(toastT); toastT = setTimeout(function () { toastEl.classList.remove("show"); }, 2200); }

  /* ---------- kopiraj link ---------- */
  $$("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var url = location.href;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(function () { toast("Link je kopiran"); }, function () { fallbackCopy(url); });
      } else fallbackCopy(url);
    });
  });
  function fallbackCopy(url) {
    var i = document.createElement("input"); i.value = url; document.body.appendChild(i); i.select();
    try { document.execCommand("copy"); toast("Link je kopiran"); } catch (e) { toast("Kopirajte link iz adresne trake"); }
    i.remove();
  }

  /* ---------- prozori (overlay) ---------- */
  var lastFocus = null;
  function openOv(id, focusSel) {
    var ov = document.getElementById(id); if (!ov) return;
    lastFocus = document.activeElement; ov.hidden = false; document.body.style.overflow = "hidden";
    var f = focusSel ? $(focusSel, ov) : null; if (f) setTimeout(function () { f.focus(); }, 30);
  }
  function closeOv(ov) { ov.hidden = true; document.body.style.overflow = ""; if (lastFocus) lastFocus.focus(); }
  $$(".overlay").forEach(function (ov) {
    ov.addEventListener("click", function (e) { if (e.target === ov || e.target.closest("[data-close]")) closeOv(ov); });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") $$(".overlay").forEach(function (ov) { if (!ov.hidden) closeOv(ov); });
    var tag = (e.target.tagName || "").toLowerCase();
    if (e.key === "/" && tag !== "input" && tag !== "textarea" && tag !== "select") { e.preventDefault(); openOv("search-overlay", "#ov-q"); }
  });
  $$("[data-open-search]").forEach(function (b) { b.addEventListener("click", function () { openOv("search-overlay", "#ov-q"); }); });
  $$("[data-open-news]").forEach(function (b) { b.addEventListener("click", function () { openOv("news-overlay", "#nl-email"); }); });

  /* ---------- newsletter ---------- */
  var nl = $("#nl-form");
  if (nl) nl.addEventListener("submit", function (e) {
    e.preventDefault();
    var em = $("#nl-email").value.trim(), msg = $("#nl-msg");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(em)) { msg.textContent = "Upišite ispravnu e-adresu, npr. ime@domen.rs"; msg.style.color = "var(--brand)"; return; }
    var action = nl.getAttribute("data-action");
    if (!action) { msg.textContent = "Hvala! Prijave se otvaraju sa prvim izdanjem newslettera."; msg.style.color = "var(--ok)"; return; }
    var fd = new FormData(); fd.append("email", em);
    fetch(action, { method: "POST", body: fd, mode: "no-cors" }).then(function () {
      msg.textContent = "Prijava je poslata. Proverite sanduče i potvrdite adresu."; msg.style.color = "var(--ok)"; nl.reset();
    }).catch(function () { msg.textContent = "Prijava nije uspela. Pokušajte ponovo za minut."; msg.style.color = "var(--brand)"; });
  });

  /* ---------- deljenje ---------- */
  function shareUrl(path) {
    var root = document.documentElement.getAttribute("data-root") || "";
    try { var u = new URL(root + path, location.href); u.search = ""; return u.href; } catch (e) { return location.href; }
  }
  function doShare(net, path, title) {
    var url = shareUrl(path), U = encodeURIComponent(url), T = encodeURIComponent(title || document.title);
    var links = {
      x: "https://x.com/intent/post?text=" + T + "&url=" + U,
      facebook: "https://www.facebook.com/sharer/sharer.php?u=" + U,
      whatsapp: "https://wa.me/?text=" + T + "%20" + U,
      viber: "viber://forward?text=" + T + "%20" + U,
      telegram: "https://t.me/share/url?url=" + U + "&text=" + T,
      linkedin: "https://www.linkedin.com/sharing/share-offsite/?url=" + U,
      email: "mailto:?subject=" + T + "&body=" + T + "%0A%0A" + U
    };
    if (net === "copy") {
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(function () { toast("Link je kopiran"); }, function () { fallbackCopy(url); });
      else fallbackCopy(url);
      return;
    }
    if (net === "native") { if (navigator.share) navigator.share({ title: title, url: url }).catch(function () {}); return; }
    var l = links[net]; if (!l) return;
    if (net === "viber" || net === "email") { location.href = l; return; }
    window.open(l, "_blank", "noopener,width=620,height=560");
  }
  function wireShare(box) {
    $$("[data-net]", box).forEach(function (b) {
      if (b.hasAttribute("data-native") && navigator.share) b.hidden = false;
      b.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); doShare(b.getAttribute("data-net"), box.getAttribute("data-path"), box.getAttribute("data-title")); });
    });
  }
  $$("[data-share]").forEach(wireShare);
  var pop = null;
  function closePop() { if (pop) { pop.remove(); pop = null; } }
  $$("[data-share-pop]").forEach(function (b) {
    b.addEventListener("click", function (e) {
      e.preventDefault(); e.stopPropagation();
      var same = pop && pop._src === b; closePop(); if (same) return;
      var t = document.getElementById("share-tpl"), tpl = t && t.content.firstElementChild;
      if (!tpl) return;
      pop = document.createElement("div"); pop.className = "share-popover"; pop._src = b;
      var box = tpl.cloneNode(true); box.className = "share"; box.setAttribute("data-path", b.getAttribute("data-path")); box.setAttribute("data-title", b.getAttribute("data-title"));
      pop.appendChild(box); document.body.appendChild(pop); wireShare(box);
      var r = b.getBoundingClientRect(), w = pop.offsetWidth;
      pop.style.top = (window.scrollY + r.top - pop.offsetHeight - 10) + "px";
      pop.style.left = Math.max(12, Math.min(window.scrollX + r.right - w, window.scrollX + document.documentElement.clientWidth - w - 12)) + "px";
    });
  });
  document.addEventListener("click", function (e) { if (pop && !pop.contains(e.target)) closePop(); });
  window.addEventListener("scroll", closePop, { passive: true });
  if (location.hash.indexOf("#v-") === 0) {
    var tv = document.getElementById(location.hash.slice(1));
    if (tv) { tv.classList.add("vc-hl"); setTimeout(function () { tv.scrollIntoView({ block: "center" }); }, 200); }
  }

  /* ---------- YouTube plejer (učitava se tek na klik: brže i bez kolačića) ---------- */
  function playYt(el) {
    if (el.querySelector("iframe")) return;
    var f = document.createElement("iframe");
    f.src = "https://www.youtube-nocookie.com/embed/" + el.getAttribute("data-yt") + "?autoplay=1&rel=0&modestbranding=1&playsinline=1&fs=1";
    f.title = el.getAttribute("aria-label") || "YouTube video";
    f.setAttribute("allow", "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share; fullscreen");
    f.setAttribute("allowfullscreen", "");
    f.allowFullscreen = true;
    el.appendChild(f);
    el.classList.add("yt-on");
    var fs = document.createElement("button");
    fs.type = "button"; fs.className = "yt-fs"; fs.setAttribute("aria-label", "Ceo ekran"); fs.title = "Ceo ekran";
    fs.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    fs.addEventListener("click", function (e) {
      e.stopPropagation();
      var t = el, req = t.requestFullscreen || t.webkitRequestFullscreen || f.webkitEnterFullscreen;
      if (document.fullscreenElement || document.webkitFullscreenElement) { (document.exitFullscreen || document.webkitExitFullscreen).call(document); return; }
      if (req) req.call(t === el && !t.requestFullscreen && !t.webkitRequestFullscreen ? f : t);
    });
    el.appendChild(fs);
  }
  $$("[data-yt]").forEach(function (el) {
    el.addEventListener("click", function () { playYt(el); });
    el.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); playYt(el); } });
  });
  $$("[data-vk]").forEach(function (b) {
    b.addEventListener("click", function () {
      var k = b.getAttribute("data-vk");
      $$("[data-vk]").forEach(function (x) { x.classList.toggle("on", x === b); });
      $$("#vid-all .vc").forEach(function (v) { v.hidden = !!k && v.getAttribute("data-vr") !== k; });
      $$("#vid-all .vid-day").forEach(function (d) { d.hidden = !d.querySelector(".vc:not([hidden])"); });
    });
  });

  /* ======================= PRETRAGA ======================= */
  var CYR = { "а":"a","б":"b","в":"v","г":"g","д":"d","ђ":"dj","е":"e","ж":"z","з":"z","и":"i","ј":"j","к":"k","л":"l","љ":"lj","м":"m","н":"n","њ":"nj","о":"o","п":"p","р":"r","с":"s","т":"t","ћ":"c","у":"u","ф":"f","х":"h","ц":"c","ч":"c","џ":"dz","ш":"s" };
  function fold(s) {
    s = (s || "").toLowerCase();
    var o = "";
    for (var i = 0; i < s.length; i++) { var c = s[i]; o += CYR[c] !== undefined ? CYR[c] : c; }
    return o.replace(/đ/g, "dj").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9"\-\s]/g, " ").replace(/\s+/g, " ").trim();
  }
  var IDX = null, loading = null;
  function loadIndex() {
    if (IDX) return Promise.resolve(IDX);
    if (loading) return loading;
    loading = fetch(ROOT + "search.json").then(function (r) { return r.json(); }).then(function (d) {
      d.forEach(function (x) {
        x._t = fold(x.t); x._l = fold(x.l); x._b = fold(x.b); x._g = fold((x.g || []).join(" ") + " " + (x.p || []).join(" "));
        x._s = fold((x.src || []).join(" ")); x._all = " " + [x._t, x._l, x._g, x._s, x._b].join(" ") + " ";
        x._time = new Date(x.d).getTime();
      });
      IDX = d; return d;
    });
    return loading;
  }
  function parseQ(q) {
    var phrases = [], neg = [], terms = [];
    q = q.replace(/"([^"]+)"/g, function (_, p) { var f = fold(p); if (f) phrases.push(f); return " "; });
    fold(q).split(" ").forEach(function (w) {
      if (!w) return;
      if (w[0] === "-" && w.length > 1) neg.push(w.slice(1).replace(/-/g, ""));
      else { w = w.replace(/^-+|-+$/g, ""); if (w) terms.push(w); }
    });
    return { terms: terms, phrases: phrases, neg: neg };
  }
  function wordHit(field, t) {
    if (!field) return 0;
    if ((" " + field + " ").indexOf(" " + t + " ") >= 0) return 2;
    if ((" " + field).indexOf(" " + t) >= 0) return 1.4;
    if (t.length >= 4 && field.indexOf(t) >= 0) return 0.6;
    return 0;
  }
  function score(x, P) {
    var s = 0, i, t, h, miss = 0;
    for (i = 0; i < P.neg.length; i++) if (x._all.indexOf(P.neg[i]) >= 0) return -1;
    for (i = 0; i < P.phrases.length; i++) { if (x._all.indexOf(" " + P.phrases[i]) < 0) return -1; s += x._t.indexOf(P.phrases[i]) >= 0 ? 14 : 7; }
    for (i = 0; i < P.terms.length; i++) {
      t = P.terms[i];
      h = wordHit(x._t, t) * 6 + wordHit(x._g, t) * 4 + wordHit(x._l, t) * 3 + wordHit(x._s, t) * 3 + wordHit(x._b, t) * 1;
      if (!h) miss++;
      s += h;
    }
    if (P.terms.length && miss === P.terms.length) return -1;
    if (miss) s *= 0.35;  // nisu pronađene sve reči: niže rangiranje
    var ageD = (Date.now() - x._time) / 864e5;
    return s * (1 + 0.5 / (1 + ageD / 3));
  }
  function esc(s) { return (s || "").replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function highlight(text, P) {
    var words = P.terms.concat(P.phrases).filter(function (w) { return w.length > 1; });
    if (!words.length) return esc(text);
    var src = text, f = fold(src), marks = [];
    // poravnanje: fold zadržava dužinu osim za đ/dž/lj/nj; za te slučajeve radimo po rečima
    var orig = src.split(/(\s+)/), out = "";
    orig.forEach(function (tok) {
      var ft = fold(tok), hit = words.some(function (w) { return w.indexOf(" ") < 0 ? ft.indexOf(w) === 0 : false; });
      out += hit ? "<mark>" + esc(tok) + "</mark>" : esc(tok);
    });
    return out;
  }
  function secName(k) {
    if (k === "video") return "Video";
    var el = $('.nav-a.r-' + k); return el ? el.textContent.trim() : (k === "analiza" ? "Analiza" : k);
  }
  function search(q, opts) {
    opts = opts || {};
    var P = parseQ(q), now = Date.now();
    var empty = !P.terms.length && !P.phrases.length;
    return IDX.map(function (x) {
      if (opts.s) { if (opts.s === "analiza") { if (x.k === "vest") return null; } else if (x.s !== opts.s || x.k !== "vest") return null; }
      if (opts.h && now - x._time > opts.h * 3600e3) return null;
      if (opts.src && (x.src || []).indexOf(opts.src) < 0) return null;
      var sc = empty ? 1 : score(x, P);
      return sc > 0 ? { x: x, s: sc } : null;
    }).filter(Boolean).sort(function (a, b) {
      if (opts.sort === "new" || (empty && opts.sort !== "old")) return b.x._time - a.x._time;
      if (opts.sort === "old") return a.x._time - b.x._time;
      return b.s - a.s;
    });
  }

  // brza pretraga u prozoru
  var ovq = $("#ov-q"), ovr = $("#ov-results"), ovForm = $("#ov-form"), sel = -1;
  if (ovq) {
    ovq.addEventListener("focus", loadIndex);
    ovq.addEventListener("input", function () {
      var q = ovq.value.trim(); sel = -1;
      if (q.length < 2) { ovr.innerHTML = ""; return; }
      loadIndex().then(function () {
        var res = search(q).slice(0, 7), P = parseQ(q);
        if (!res.length) { ovr.innerHTML = '<p class="ov-hint">Nema rezultata za „' + esc(q) + '“. Probajte deo reči ili drugi pojam.</p>'; return; }
        ovr.innerHTML = res.map(function (r) {
          var x = r.x;
          return '<a href="' + ROOT + x.u + '"><img src="' + (x.i.indexOf("http") === 0 ? x.i : ROOT + x.i) + '" alt="" loading="lazy"><span><b>' + highlight(x.t, P) + "</b><small>" + secName(x.k === "vest" ? x.s : x.k === "video" ? "video" : "analiza") + " · " + rel(x.d) + " · " + esc((x.src || []).slice(0, 2).join(", ")) + "</small></span></a>";
        }).join("") + '<a class="btn btn-ghost ov-all" href="' + ROOT + "pretraga.html?q=" + encodeURIComponent(q) + '">Svi rezultati i filteri →</a>';
      });
    });
    ovq.addEventListener("keydown", function (e) {
      var links = $$("a", ovr); if (!links.length) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault(); sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + links.length) % links.length;
        links.forEach(function (l, i) { l.classList.toggle("sel", i === sel); }); links[sel].scrollIntoView({ block: "nearest" });
      } else if (e.key === "Enter" && sel >= 0) { e.preventDefault(); location.href = links[sel].href; }
    });
  }

  // stranica pretrage
  var page = $("[data-search-page]");
  if (page) {
    var qi = $("#sp-q"), list = $("#sp-results"), status = $("#sp-status"), more = $("#sp-more"), teme = $("#sp-teme");
    var state = { s: "", h: 0, src: "", sort: "rel", shown: 20 };
    try {
      var params = new URLSearchParams(location.search);
      qi.value = params.get("q") || ""; state.s = params.get("s") || "";
    } catch (e) {}
    function setChip() { $$("[data-f-s]").forEach(function (c) { c.classList.toggle("on", c.getAttribute("data-f-s") === state.s); }); }
    function run() {
      loadIndex().then(function () {
        var q = qi.value.trim(), P = parseQ(q);
        var res = search(q, state);
        if (teme) teme.hidden = !!q;
        status.textContent = q ? (res.length ? "Pronađeno " + res.length + (res.length === 1 ? " tekst" : res.length < 5 ? " teksta" : " tekstova") + " za „" + q + "“" : "Nema rezultata za „" + q + "“. Probajte kraći pojam ili uklonite filtere.")
                               : res.length + " tekstova u arhivi";
        list.innerHTML = res.slice(0, state.shown).map(function (r) {
          var x = r.x, cls = "r-" + (x.k === "vest" || x.k === "video" ? x.s : "analiza");
          return '<li class="res ' + cls + '"><a href="' + ROOT + x.u + '"><span class="res-th"><img src="' + (x.i.indexOf("http") === 0 ? x.i : ROOT + x.i) + '" alt="" loading="lazy"></span><span><span class="res-meta"><b>' +
            secName(x.k === "vest" ? x.s : x.k === "video" ? "video" : "analiza") + "</b><span>" + rel(x.d) + "</span><span>" + esc((x.src || []).slice(0, 3).join(", ")) + "</span></span><h3>" + highlight(x.t, P) + "</h3><p>" + highlight(x.l, P) + "</p></span></a></li>";
        }).join("");
        more.hidden = res.length <= state.shown;
        try { var u = new URL(location.href); if (q) u.searchParams.set("q", q); else u.searchParams.delete("q"); history.replaceState(null, "", u.pathname + u.search); } catch (e) {}
      }).catch(function () { status.textContent = "Pretraga trenutno nije dostupna. Osvežite stranicu."; });
    }
    loadIndex().then(function (d) {
      var srcs = {}; d.forEach(function (x) { (x.src || []).forEach(function (s) { srcs[s] = (srcs[s] || 0) + 1; }); });
      var selEl = $("#sp-src");
      Object.keys(srcs).sort().forEach(function (s) { var o = document.createElement("option"); o.value = s; o.textContent = s + " (" + srcs[s] + ")"; selEl.appendChild(o); });
    });
    var deb;
    qi.addEventListener("input", function () { clearTimeout(deb); state.shown = 20; deb = setTimeout(run, 140); });
    $("#sp-form").addEventListener("submit", function (e) { e.preventDefault(); run(); });
    $$("[data-f-s]").forEach(function (c) { c.addEventListener("click", function () { state.s = c.getAttribute("data-f-s"); state.shown = 20; setChip(); run(); }); });
    $("#sp-period").addEventListener("change", function (e) { state.h = +e.target.value; state.shown = 20; run(); });
    $("#sp-src").addEventListener("change", function (e) { state.src = e.target.value; state.shown = 20; run(); });
    $("#sp-sort").addEventListener("change", function (e) { state.sort = e.target.value; run(); });
    more.addEventListener("click", function () { state.shown += 20; run(); });
    $$("[data-q]").forEach(function (b) { b.addEventListener("click", function () { qi.value = b.getAttribute("data-q"); run(); qi.focus(); }); });
    setChip(); run(); if (!qi.value) qi.focus();
  }
})();

/* ---------- X objave: ugrađuju se jedna po jedna, tek kad su blizu ekrana ---------- */
(function () {
  var els = [].slice.call(document.querySelectorAll("blockquote.x-embed"));
  if (!els.length) return;
  var t = document.documentElement.getAttribute("data-theme");
  var dark = t ? t === "dark" : !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  var queue = [], busy = false, ready = null;
  function api() {
    if (ready) return ready;
    ready = new Promise(function (res, rej) {
      if (window.twttr && window.twttr.widgets) return res(window.twttr);
      var s = document.createElement("script");
      s.src = "https://platform.twitter.com/widgets.js"; s.async = true; s.charset = "utf-8";
      s.onload = function () { var n = 0; (function w() { if (window.twttr && window.twttr.widgets) res(window.twttr); else if (n++ < 50) setTimeout(w, 100); else rej(); })(); };
      s.onerror = rej;
      document.body.appendChild(s);
    });
    return ready;
  }
  function next() {
    if (busy || !queue.length) return;
    busy = true;
    var el = queue.shift();
    api().then(function (tw) {
      el.classList.add("twitter-tweet"); el.setAttribute("data-theme", dark ? "dark" : "light");
      var done = false, fin = function () { if (!done) { done = true; busy = false; setTimeout(next, 150); } };
      tw.widgets.load(el.parentNode).then(fin, fin); setTimeout(fin, 6000);
    }, function () { busy = false; queue = []; });
  }
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { io.unobserve(e.target); queue.push(e.target); } });
      next();
    }, { rootMargin: "400px 0px" });
    els.forEach(function (el) { io.observe(el); });
  } else { queue = els.slice(0, 6); next(); }
})();


/* ---------- ankete čitalaca: glas jednim dodirom, rezultati tek posle glasa ---------- */
(function () {
  var els = Array.prototype.slice.call(document.querySelectorAll("[data-anketa]"));
  if (!els.length || !window.fetch) return;
  function ls(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } return null; }
  function glasova(n) { var d = n % 10, s = n % 100; return n + (d === 1 && s !== 11 ? " glas" : d >= 2 && d <= 4 && (s < 12 || s > 14) ? " glasa" : " glasova"); }
  function show(el, counts, mine) {
    var bs = el.querySelectorAll(".poll-b"), tot = 0, i;
    for (i = 0; i < bs.length; i++) tot += +(counts[i] || 0);
    el.classList.remove("busy"); el.classList.add("done");
    for (i = 0; i < bs.length; i++) {
      var n = +(counts[i] || 0), pc = tot ? Math.round(100 * n / tot) : 0, b = bs[i];
      b.disabled = true; b.style.setProperty("--p", pc + "%");
      b.querySelector(".poll-pct").textContent = pc + "%";
      b.classList.toggle("mine", mine !== null && String(i) === String(mine));
      b.setAttribute("aria-label", b.querySelector(".poll-t").textContent + ", " + pc + " odsto");
    }
    var nn = el.querySelector(".poll-n");
    if (nn) nn.textContent = glasova(tot) + (el.getAttribute("data-open") === "1" ? " · glasanje traje" : " · konačni rezultat");
  }
  function load(el) {
    var id = el.getAttribute("data-anketa");
    fetch(el.getAttribute("data-api") + "/rezultati?ids=" + encodeURIComponent(id))
      .then(function (r) { return r.json(); })
      .then(function (d) { show(el, (d.rezultati || {})[id] || {}, ls("pa:" + id)); })
      .catch(function () {});
  }
  els.forEach(function (el) {
    var id = el.getAttribute("data-anketa"), open = el.getAttribute("data-open") === "1";
    if (!open || ls("pa:" + id) !== null) { load(el); return; }
    Array.prototype.slice.call(el.querySelectorAll(".poll-b")).forEach(function (b) {
      b.addEventListener("click", function () {
        if (el.classList.contains("busy") || el.classList.contains("done")) return;
        el.classList.add("busy");
        var o = b.getAttribute("data-o");
        fetch(el.getAttribute("data-api") + "/glas", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: id, o: +o }) })
          .then(function (r) { if (!r.ok && r.status !== 409) throw new Error(r.status); return r.json(); })
          .then(function (d) { ls("pa:" + id, d.tvoj !== undefined ? String(d.tvoj) : o); show(el, d.rezultati || {}, d.tvoj !== undefined ? d.tvoj : o); })
          .catch(function () { el.classList.remove("busy"); var nn = el.querySelector(".poll-n"); if (nn) nn.textContent = "Glas nije zabeležen, pokušajte ponovo"; });
      });
    });
  });
})();

/* ---------- sirok ekran: desno polje uzivo (sat, vreme, kursna lista) ---------- */
(function () {
  var box = document.querySelector("[data-live]");
  if (!box || !window.matchMedia) return;
  var mq = window.matchMedia("(min-width: 1680px)"), started = false;
  var DAN = ["nedelja", "ponedeljak", "utorak", "sreda", "četvrtak", "petak", "subota"], DK = ["Ned", "Pon", "Uto", "Sre", "Čet", "Pet", "Sub"];
  var MES = ["januar", "februar", "mart", "april", "maj", "jun", "jul", "avgust", "septembar", "oktobar", "novembar", "decembar"];
  var WMO = {0: "Vedro", 1: "Pretežno vedro", 2: "Delimično oblačno", 3: "Oblačno", 45: "Magla", 48: "Magla", 51: "Rosulja", 53: "Rosulja", 55: "Rosulja", 61: "Slaba kiša", 63: "Kiša", 65: "Jaka kiša", 66: "Ledena kiša", 67: "Ledena kiša", 71: "Slab sneg", 73: "Sneg", 75: "Jak sneg", 77: "Sneg", 80: "Pljuskovi", 81: "Pljuskovi", 82: "Jaki pljuskovi", 85: "Snežni pljuskovi", 86: "Snežni pljuskovi", 95: "Grmljavina", 96: "Grmljavina", 99: "Grmljavina"};
  var S = 'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"';
  var SUN = '<circle cx="12" cy="12" r="4.2" ' + S + '/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6" ' + S + '/>';
  var CLOUD = '<path d="M7 18h10a4 4 0 0 0 .6-7.95A5.5 5.5 0 0 0 7.1 9.6 4.2 4.2 0 0 0 7 18z" ' + S + '/>';
  var ICO = {
    sun: SUN,
    part: '<circle cx="8.5" cy="8.5" r="3.2" ' + S + '/><path d="M8.5 2.5v1.4M2.5 8.5h1.4M4.3 4.3l1 1M12.7 4.3l-1 1" ' + S + '/><path d="M9 19h8.5a3.4 3.4 0 0 0 .5-6.76 4.7 4.7 0 0 0-8.9-1.2A3.6 3.6 0 0 0 9 19z" ' + S + '/>',
    cloud: CLOUD,
    fog: '<path d="M4 9h16M6 13h12M4 17h16" ' + S + '/>',
    rain: CLOUD + '<path d="M9 20.5l-.8 1.6M13 20.5l-.8 1.6M17 20.5l-.8 1.6" ' + S + '/>',
    snow: CLOUD + '<path d="M9 21h.01M13 21h.01M17 21h.01" ' + S + ' stroke-width="3"/>',
    storm: CLOUD + '<path d="M12.5 17.5l-2 3h3l-2 3" ' + S + '/>'
  };
  function ico(c) {
    var k = c === 0 ? "sun" : c <= 2 ? "part" : c === 3 ? "cloud" : c <= 48 ? "fog" : c <= 67 || (c >= 80 && c <= 82) ? "rain" : c <= 86 ? "snow" : "storm";
    return '<svg viewBox="0 0 24 24" aria-hidden="true">' + ICO[k] + "</svg>";
  }
  function bg() { return new Date(new Date().toLocaleString("en-US", { timeZone: "Europe/Belgrade" })); }
  function sat() {
    var d = bg(), t = box.querySelector("[data-g-time]"), dt = box.querySelector("[data-g-date]");
    if (t) t.textContent = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    if (dt) dt.textContent = DAN[d.getDay()] + ", " + d.getDate() + ". " + MES[d.getMonth()];
  }
  function metKod(s) {
    s = (s || "").split("_")[0];
    if (/thunder/.test(s)) return 95; if (/snow/.test(s)) return 73; if (/sleet/.test(s)) return 66; if (/showers/.test(s)) return 80;
    if (/heavyrain/.test(s)) return 65; if (/lightrain/.test(s)) return 61; if (/rain/.test(s)) return 63; if (/fog/.test(s)) return 45;
    if (s === "partlycloudy") return 2; if (s === "fair") return 1; if (s === "clearsky") return 0; return 3;
  }
  function crtaj(temp, kod, feel, dani) {
    box.querySelector("[data-g-temp]").textContent = Math.round(temp) + "°";
    box.querySelector("[data-g-opis]").textContent = WMO[kod] || "";
    box.querySelector("[data-g-ico]").innerHTML = ico(kod);
    box.querySelector("[data-g-feel]").textContent = feel;
    box.querySelector("[data-g-days]").innerHTML = dani.slice(0, 3).map(function (x) {
      return "<li>" + DK[x.d.getDay()] + ico(x.kod) + "<b>" + Math.round(x.max) + "° <span>" + Math.round(x.min) + "°</span></b></li>";
    }).join("");
  }
  function vremeMet() {
    fetch("https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=44.82&lon=20.46").then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
      if (!j || !j.properties) return;
      var ts = j.properties.timeseries, c = ts[0].data, det = c.instant.details;
      var sym = ((c.next_1_hours || c.next_6_hours || {}).summary || {}).symbol_code;
      var danas = new Date().toLocaleDateString("en-CA", { timeZone: "Europe/Belgrade" }), po = {}, red = [];
      ts.forEach(function (x) {
        var dt = new Date(x.time), k = dt.toLocaleDateString("en-CA", { timeZone: "Europe/Belgrade" });
        if (k === danas) return;
        var h = +dt.toLocaleString("en-US", { timeZone: "Europe/Belgrade", hour: "2-digit", hour12: false }) % 24, t = x.data.instant.details.air_temperature;
        if (!po[k]) { po[k] = { d: new Date(k + "T12:00:00"), max: t, min: t, kod: 3, h: 99 }; red.push(po[k]); }
        var p = po[k]; p.max = Math.max(p.max, t); p.min = Math.min(p.min, t);
        var s6 = ((x.data.next_6_hours || x.data.next_1_hours || {}).summary || {}).symbol_code;
        if (s6 && Math.abs(h - 12) < Math.abs(p.h - 12)) { p.h = h; p.kod = metKod(s6); }
      });
      crtaj(det.air_temperature, metKod(sym), "vlažnost " + Math.round(det.relative_humidity) + "% · vetar " + Math.round(det.wind_speed * 3.6) + " km/h", red);
    }).catch(function () {});
  }
  function vreme() {
    fetch("https://api.open-meteo.com/v1/forecast?latitude=44.82&longitude=20.46&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m&daily=weather_code,temperature_2m_max,temperature_2m_min&timezone=Europe%2FBelgrade&forecast_days=4")
      .then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
        if (!j || !j.current) return vremeMet();
        var c = j.current, d = j.daily, dani = [];
        for (var i = 1; i < 4 && d && d.time && i < d.time.length; i++) dani.push({ d: new Date(d.time[i] + "T12:00:00"), kod: d.weather_code[i], max: d.temperature_2m_max[i], min: d.temperature_2m_min[i] });
        crtaj(c.temperature_2m, c.weather_code, "oseća se " + Math.round(c.apparent_temperature) + "° · vetar " + Math.round(c.wind_speed_10m) + " km/h", dani);
      }).catch(vremeMet);
  }
  function kurs() {
    fetch("https://kurs.resenje.org/api/v1/rates/today").then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
      if (!j || !j.rates) return;
      var want = ["EUR", "USD", "CHF", "GBP", "BAM"], by = {};
      j.rates.forEach(function (x) { by[x.code] = x; });
      var html = "";
      want.forEach(function (c) {
        var x = by[c]; if (!x) return;
        var v = x.exchange_middle / (x.parity || 1);
        html += "<li><span>1 " + c + "</span><i></i><b>" + v.toFixed(2).replace(".", ",") + " <small>din</small></b></li>";
      });
      if (html) box.querySelector("[data-g-fx]").innerHTML = html;
    }).catch(function () {});
  }
  function start() {
    if (started || !mq.matches) return;
    started = true;
    sat(); vreme(); kurs();
    setInterval(sat, 20000);
    setInterval(function () { if (!document.hidden) vreme(); }, 20 * 60000);
    setInterval(function () { if (!document.hidden) kurs(); }, 60 * 60000);
  }
  start();
  if (mq.addEventListener) mq.addEventListener("change", start);
})();

/* ---------- sirok ekran: bocna polja i usi se sami prilagodjavaju sirini, visini i zumu pregledaca ---------- */
(function () {
  var g = document.querySelector(".gutters"), m = document.getElementById("mast"), de = document.documentElement;
  if (!g || !m || !window.matchMedia) return;
  var mq = window.matchMedia("(min-width: 1680px)"), sides = g.querySelectorAll(".g-side"), q = 0;
  function place() {
    q = 0;
    if (!mq.matches) return;
    var mb = Math.max(0, m.getBoundingClientRect().bottom), vh = window.innerHeight, gw = (de.clientWidth - 1240) / 2;
    var px = Math.min(24, window.scrollY * 0.02), tiny = false;
    de.style.setProperty("--es", Math.max(0.72, Math.min(1.3, gw / 300)).toFixed(3));
    for (var i = 0; i < sides.length; i++) {
      var s = sides[i], h = s.offsetHeight || 1, sc = Math.min((gw - 48) / 320, (vh - mb - 36) / h, 1.35);
      if (sc < 0.58) tiny = true;
      sc = Math.max(sc, 0.58);
      s.style.setProperty("--gs", sc.toFixed(3));
      s.style.top = Math.round(mb + Math.max(18, (vh - mb - h * sc) / 2) + (i ? px * 0.5 : px)) + "px";
    }
    g.classList.toggle("g-tiny", tiny);
  }
  function req() { if (!q) { q = 1; requestAnimationFrame(place); } }
  addEventListener("scroll", req, { passive: true });
  addEventListener("resize", req);
  if (mq.addEventListener) mq.addEventListener("change", req);
  place(); setTimeout(place, 700);
})();
