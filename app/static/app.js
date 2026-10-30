const state = {
  view: localGet("view") === "tendenze" ? "tendenze" : "novita",
  tipo: "all",
  q: "",
  platforms: new Set(JSON.parse(localGet("platforms") || "[]")),
  data: null,
  hero: { items: [], index: 0, timer: null },
};

const $ = (s) => document.querySelector(s);
const content = $("#content");
const POLL_MS = 5 * 60 * 1000;
const HERO_MS = 8000;

function localGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem(k, v); } catch { /* ignora */ } }

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const fmtShort = new Intl.DateTimeFormat("it-IT", { day: "numeric", month: "short" });
const fmtLong = new Intl.DateTimeFormat("it-IT", { day: "numeric", month: "long", year: "numeric" });
const toDate = (iso) => new Date(iso + "T00:00:00");

function daysAgo(iso) {
  if (!iso) return Infinity;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.round((today - toDate(iso)) / 86400000);
}

function relDate(iso) {
  const d = daysAgo(iso);
  if (d === 0) return "Oggi";
  if (d === 1) return "Ieri";
  if (d < 7) return `${d} giorni fa`;
  return fmtShort.format(toDate(iso));
}

const BUCKETS = [
  { max: 0, label: "Usciti oggi" },
  { max: 1, label: "Usciti ieri" },
  { max: 6, label: "Questa settimana" },
  { max: 13, label: "La settimana scorsa" },
  { max: 31, label: "Nell'ultimo mese" },
  { max: Infinity, label: "Nei mesi scorsi" },
];

function platform(key) { return state.data?.piattaforme?.[key]; }

function logoHtml(key) {
  const p = platform(key);
  if (!p) return "";
  return p.logo
    ? `<img src="${esc(p.logo)}" alt="${esc(p.name)}" title="${esc(p.name)}" loading="lazy">`
    : `<span class="txt" style="--c:${esc(p.color)}">${esc(p.name)}</span>`;
}

function badgeHtml(key) {
  const p = platform(key);
  if (!p) return "";
  const logo = p.logo ? `<img src="${esc(p.logo)}" alt="" loading="lazy">` : "";
  return `<span class="badge" style="--c:${esc(p.color)}">${logo}${esc(p.name)}</span>`;
}

function filtered(view = state.view) {
  const items = state.data?.[view] || [];
  const q = state.q.toLowerCase();
  return items.filter((i) =>
    (state.tipo === "all" || i.type === state.tipo) &&
    (!state.platforms.size || i.platforms.some((p) => state.platforms.has(p))) &&
    (!q || (i.title || "").toLowerCase().includes(q) || (i.original_title || "").toLowerCase().includes(q))
  );
}

// ---------------------------------------------------------------- schede

function cardHtml(i) {
  const fresh = state.view === "novita" && daysAgo(i.date) <= 2;
  const pill = fresh ? "Novità" : i.type === "movie" ? "Film" : "Serie TV";
  const poster = i.poster
    ? `<img class="p" src="${esc(i.poster)}" alt="" loading="lazy">`
    : `<div class="noimg">${esc(i.title)}</div>`;
  const meta = state.view === "novita"
    ? `<b>${esc(relDate(i.date))}</b>${i.genres[0] ? ` · ${esc(i.genres[0])}` : ""}`
    : `${esc(i.year)}${i.genres[0] ? ` · ${esc(i.genres[0])}` : ""}`;
  let posterBox = `
    <div class="poster">
      ${poster}
      <span class="pill ${fresh ? "new" : ""}">${esc(pill)}</span>
      <div class="logos">${i.platforms.map(logoHtml).join("")}</div>
      ${i.rating ? `<span class="rating">★ ${i.rating}</span>` : ""}
    </div>`;
  if (i.rank) {
    posterBox = `<div class="rank-row"><span class="rank" aria-label="Posizione ${i.rank}">${i.rank}</span>${posterBox}</div>`;
  }
  return `<button class="card ${i.rank >= 10 ? "wide" : ""}" data-id="${esc(i.id)}">
    ${posterBox}
    <h3>${esc(i.title)}</h3>
    ${i.type === "tv" ? `<span class="sub">${esc(i.badge)}</span>` : ""}
    <span class="meta">${meta}</span>
  </button>`;
}

function blockHtml(title, items, ranked = false) {
  const n = items.length;
  return `<section class="block">
    <div class="block-head"><h2>${esc(title)}</h2><span class="count">${n} ${n === 1 ? "titolo" : "titoli"}</span></div>
    <div class="grid ${ranked ? "ranked" : ""}">${items.map(cardHtml).join("")}</div>
  </section>`;
}

function render() {
  const items = filtered();
  content.setAttribute("aria-busy", "false");
  $("#empty").hidden = items.length > 0 || !state.data;

  if (state.view === "tendenze") {
    content.innerHTML = items.length ? blockHtml("I più visti questa settimana", items, true) : "";
    return;
  }
  const groups = BUCKETS.map((b) => ({ ...b, items: [] }));
  for (const i of items) groups.find((g) => daysAgo(i.date) <= g.max).items.push(i);
  content.innerHTML = groups.filter((g) => g.items.length).map((g) => blockHtml(g.label, g.items)).join("");
}

function renderChips() {
  const ps = state.data?.piattaforme || {};
  $("#platforms").innerHTML = Object.entries(ps).map(([k, p]) => `
    <button class="chip ${state.platforms.has(k) ? "on" : ""}" data-p="${esc(k)}" aria-pressed="${state.platforms.has(k)}">
      ${p.logo ? `<img src="${esc(p.logo)}" alt="">` : ""}${esc(p.name)}
    </button>`).join("");
}

function renderStatus(s) {
  const el = $("#status");
  if (s.aggiornamento_in_corso) {
    el.innerHTML = `<span class="dot busy"></span> Aggiornamento in corso…`;
  } else {
    const when = s.aggiornato
      ? new Date(s.aggiornato).toLocaleString("it-IT", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
      : "mai";
    el.innerHTML = `<span class="dot"></span> Aggiornato ${esc(when)} <button id="refresh" title="Aggiorna ora">↻ Aggiorna</button>`;
  }
  $("#error").hidden = !s.errore;
  $("#error").textContent = s.errore || "";
}

function skeleton() {
  const card = `<div class="card skeleton"><div class="poster"></div><h3></h3><span class="meta"></span></div>`;
  content.innerHTML = `<section class="block"><div class="grid">${card.repeat(12)}</div></section>`;
}

// ------------------------------------------------------------------ hero

function pickHero() {
  const pool = state.view === "tendenze"
    ? filtered("tendenze")
    : [...filtered("novita")].filter((i) => daysAgo(i.date) <= 14).sort((a, b) => b.popularity - a.popularity);
  return pool.filter((i) => i.backdrop && i.overview).slice(0, 5);
}

function renderHero() {
  const items = pickHero();
  const h = state.hero;
  const same = items.map((i) => i.id).join() === h.items.map((i) => i.id).join();
  h.items = items;
  $("#hero").hidden = !items.length;
  if (!items.length) return;
  if (!same) h.index = 0;
  $("#hero-dots").innerHTML = items.map((_, n) => `<button data-n="${n}" aria-label="Titolo ${n + 1}"></button>`).join("");
  showHero(h.index, !same);
  startHeroTimer();
}

function startHeroTimer() {
  const h = state.hero;
  clearInterval(h.timer);
  if (h.items.length > 1) h.timer = setInterval(() => showHero((h.index + 1) % h.items.length), HERO_MS);
}

function showHero(n, instant = false) {
  const h = state.hero;
  const i = h.items[n];
  if (!i) return;
  h.index = n;
  const bg = $("#hero-bg");
  const apply = () => {
    bg.style.backgroundImage = `url("${i.backdrop.replace("/w780/", "/w1280/")}")`;
    $("#hero-eyebrow").textContent = state.view === "tendenze"
      ? `N° ${i.rank} di tendenza in Italia`
      : `${i.type === "movie" ? "Film" : i.badge} · ${relDate(i.date)}`;
    $("#hero-title").textContent = i.title;
    $("#hero-meta").textContent = [i.year, i.genres.slice(0, 3).join(", "), i.rating ? `★ ${i.rating}` : ""].filter(Boolean).join("  ·  ");
    $("#hero-overview").textContent = i.overview;
    $("#hero-platforms").innerHTML = i.platforms.map(logoHtml).join("");
    $("#hero-open").dataset.id = i.id;
    document.querySelectorAll("#hero-dots button").forEach((b, k) => b.classList.toggle("on", k === n));
    bg.classList.remove("fade");
  };
  if (instant) return apply();
  bg.classList.add("fade");
  setTimeout(apply, 450);
}

// -------------------------------------------------------------- dati

async function load() {
  const r = await fetch("/api/catalogo");
  state.data = await r.json();
  document.body.classList.toggle("needs-key", !!state.data.serve_chiave);
  $("#setup").hidden = !state.data.serve_chiave;
  if (state.data.serve_chiave) {
    renderStatus({ ...state.data, errore: null });
    if (state.data.errore && state.data.aggiornato === null && $("#setup-key").value) showSetupMsg(state.data.errore, true);
    return;
  }
  renderStatus(state.data);
  renderChips();
  renderHero();
  render();
  if (state.data.aggiornamento_in_corso || (!state.data.aggiornato && !state.data.errore)) {
    if (!state.data.novita.length) skeleton();
    setTimeout(load, 4000);
  }
}

async function poll() {
  try {
    const s = await (await fetch("/api/stato")).json();
    if (s.aggiornato !== state.data?.aggiornato) await load();
    else renderStatus(s);
  } catch { /* offline: riprova al prossimo giro */ }
}

function findItem(id) {
  return [...(state.data?.novita || []), ...(state.data?.tendenze || [])].find((x) => x.id === id);
}

function openDetail(id) {
  const i = findItem(id);
  if (!i) return;
  $("#d-hero").style.backgroundImage = i.backdrop ? `url("${i.backdrop.replace("/w780/", "/w1280/")}")` : "";
  const poster = $("#d-poster");
  poster.hidden = !i.poster;
  if (i.poster) poster.src = i.poster;
  $("#d-badge").textContent = i.type === "movie" ? "Film" : i.badge;
  $("#d-title").textContent = i.title;
  const orig = i.original_title || "";
  $("#d-orig").textContent = orig.toLowerCase() !== (i.title || "").toLowerCase() ? orig : "";
  $("#d-meta").textContent = [
    i.date ? `Uscita ${fmtLong.format(toDate(i.date))}` : null,
    i.genres.join(", ") || null,
    i.runtime ? `${i.runtime} min` : null,
    i.seasons ? `${i.seasons} stagion${i.seasons === 1 ? "e" : "i"}` : null,
    i.rating ? `★ ${i.rating} (${i.votes.toLocaleString("it-IT")} voti)` : null,
  ].filter(Boolean).join("  ·  ");
  $("#d-overview").textContent = i.overview || "Trama non ancora disponibile.";
  $("#d-platforms").innerHTML = i.platforms.map(badgeHtml).join("");
  $("#d-links").innerHTML =
    (i.watch_link ? `<a href="${esc(i.watch_link)}" target="_blank" rel="noopener">▶ Dove guardarlo</a>` : "") +
    `<a href="${esc(i.tmdb_url)}" target="_blank" rel="noopener">Scheda completa su TMDB</a>`;
  $("#detail").showModal();
}

// ------------------------------------------------------------ primo avvio

function showSetupMsg(text, isError) {
  const m = $("#setup-msg");
  m.textContent = text;
  m.className = `setup-msg ${isError ? "err" : "ok"}`;
}

$("#setup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.currentTarget.querySelector("button");
  btn.disabled = true;
  showSetupMsg("Verifico la chiave…", false);
  try {
    const r = await fetch("/api/chiave", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chiave: $("#setup-key").value }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Chiave non accettata.");
    showSetupMsg("Chiave valida! Sto preparando il catalogo, ci vuole circa mezzo minuto…", false);
    setTimeout(load, 1500);
  } catch (err) {
    showSetupMsg(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

// ---------------------------------------------------------------- eventi

function setView(view) {
  state.view = view;
  localSet("view", view);
  document.querySelectorAll(".tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.view === view);
    t.setAttribute("aria-selected", t.dataset.view === view);
  });
}

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
  setView(t.dataset.view);
  renderHero();
  render();
}));

$("#tipo").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  state.tipo = b.dataset.tipo;
  $("#tipo").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
  renderHero();
  render();
});

$("#search").addEventListener("input", (e) => { state.q = e.target.value.trim(); render(); });

$("#platforms").addEventListener("click", (e) => {
  const b = e.target.closest(".chip");
  if (!b) return;
  const k = b.dataset.p;
  state.platforms.has(k) ? state.platforms.delete(k) : state.platforms.add(k);
  localSet("platforms", JSON.stringify([...state.platforms]));
  renderChips();
  renderHero();
  render();
});

content.addEventListener("click", (e) => {
  const c = e.target.closest(".card[data-id]");
  if (c) openDetail(c.dataset.id);
});

$("#hero-open").addEventListener("click", (e) => openDetail(e.currentTarget.dataset.id));
$("#hero-dots").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-n]");
  if (!b) return;
  showHero(Number(b.dataset.n));
  startHeroTimer();
});

$("#status").addEventListener("click", async (e) => {
  if (e.target.id !== "refresh") return;
  await fetch("/api/aggiorna", { method: "POST" });
  renderStatus({ aggiornamento_in_corso: true });
  setTimeout(load, 3000);
});

$("#detail").addEventListener("click", (e) => {
  if (e.target.matches("[data-close]") || e.target === e.currentTarget) e.currentTarget.close();
});

setView(state.view);
skeleton();
load().catch(() => { $("#error").hidden = false; $("#error").textContent = "Impossibile contattare il server."; });
setInterval(poll, POLL_MS);
