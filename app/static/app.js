const VIEWS = ["novita", "prossimamente", "migliori", "per_te", "tendenze", "bambini"];
const savedView = localGet("view") === "da_non_perdere" ? "migliori" : localGet("view");
const state = {
  view: VIEWS.includes(savedView) ? savedView : "novita",
  tipo: "all",
  categoria: "",
  periodo: localGet("periodo") || "n5",
  q: "",
  platforms: new Set(JSON.parse(localGet("platforms") || "[]")),
  data: null,
  hero: { items: [], index: 0, timer: null },
  servizi: { radarr: false, sonarr: false, plex: false },
  plex: { movie: new Set(), tv: new Set() },
  seasons: null,
  requests: [],
  requestsLoaded: false,
  gusti: new Map(),
  visti: {},
  suggeriti: null,
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

// ------------------------------------------------------------ periodo ("I migliori")

const THIS_YEAR = new Date().getFullYear();

function periodOptions() {
  const max = state.data?.anni_migliori || 10;
  const years = Array.from({ length: Math.min(5, max) }, (_, k) => THIS_YEAR - k)
    .map((y) => ({ id: `y${y}`, label: String(y) }));
  const spans = [3, 5, 10].filter((n) => n <= max).map((n) => ({ id: `n${n}`, label: `Ultimi ${n} anni` }));
  return [...years, ...spans];
}

function inPeriod(i, periodo = state.periodo) {
  const y = Number(i.year);
  if (!y) return false;
  if (periodo.startsWith("y")) return y === Number(periodo.slice(1));
  return y > THIS_YEAR - Number(periodo.slice(1));
}

function periodTitle() {
  const p = state.periodo;
  return p.startsWith("y") ? `I migliori del ${p.slice(1)}` : `I migliori degli ultimi ${p.slice(1)} anni`;
}

function filtered(view = state.view, skip = "") {
  const items = state.data?.[view] || [];
  const q = state.q.toLowerCase();
  return items.filter((i) =>
    (state.tipo === "all" || i.type === state.tipo) &&
    (skip === "platforms" || !state.platforms.size || (i.platforms || []).some((p) => state.platforms.has(p))) &&
    (skip === "categoria" || !state.categoria || (i.categorie || []).includes(state.categoria)) &&
    (view !== "migliori" || inPeriod(i)) &&
    (!q || (i.title || "").toLowerCase().includes(q) || (i.original_title || "").toLowerCase().includes(q))
  );
}

// ---------------------------------------------------------------- schede

function onPlex(i) {
  return state.plex[i.type]?.has(i.tmdb_id);
}

function untilText(iso) {
  const d = -daysAgo(iso);
  if (d <= 0) return "Oggi";
  if (d === 1) return "Domani";
  if (d < 7) return `Fra ${d} giorni`;
  return fmtShort.format(toDate(iso));
}

function calHtml(iso) {
  const d = toDate(iso);
  return `<span class="cal"><b>${d.getDate()}</b>${esc(d.toLocaleDateString("it-IT", { month: "short" }).replace(".", ""))}</span>`;
}

function cardHtml(i, n = 0) {
  const fresh = state.view === "novita" && daysAgo(i.date) <= 2;
  const upcoming = state.view === "prossimamente";
  const pill = fresh ? "Novità" : upcoming ? "In arrivo" : i.type === "movie" ? "Film" : "Serie TV";
  const poster = i.poster
    ? `<img class="p" src="${esc(i.poster)}" alt="" loading="lazy" onload="this.classList.add('ok')" onerror="this.classList.add('ok')">`
    : `<div class="noimg">${esc(i.title)}</div>`;
  const genre = i.genres?.[0] ? ` · ${esc(i.genres[0])}` : "";
  const forYou = state.view === "per_te";
  const meta = state.view === "novita" ? `<b>${esc(relDate(i.date))}</b>${genre}`
    : upcoming ? `<b>${esc(untilText(i.date))}</b>${genre}`
    : `${esc(i.year)}${genre}`;
  const best = state.view === "migliori";
  const corner = [
    upcoming && i.date ? calHtml(i.date) : "",
    best ? scoreHtml(i) : "",
    onPlex(i) ? `<span class="on-plex">Su Plex</span>` : "",
    seenHtml(i),
    tasteHtml(i),
  ].join("");
  const about = forYou && i.motivo ? i.motivo : i.overview;
  let posterBox = `
    <div class="poster">
      ${poster}
      <span class="pill ${fresh ? "new" : upcoming ? "soon" : ""}">${esc(pill)}</span>
      ${corner ? `<div class="corner">${corner}</div>` : ""}
      <div class="logos">${(i.platforms || []).map(logoHtml).join("")}</div>
      ${!best && !upcoming ? ratingHtml(i) : ""}
      ${about ? `<div class="hover-info">${forYou && i.motivo ? `<b class="why">Perché a te</b>` : ""}<p>${esc(about)}</p><span class="go">▶ Dettagli</span></div>` : ""}
      <span class="glare"></span>
    </div>`;
  if (i.rank) {
    posterBox = `<div class="rank-row"><span class="rank" aria-label="Posizione ${i.rank}">${i.rank}</span>${posterBox}</div>`;
  }
  return `<button class="card ${i.rank >= 10 ? "wide" : ""}" data-id="${esc(i.id)}" style="--i:${Math.min(n, 18)}">
    ${posterBox}
    <h3>${esc(i.title)}</h3>
    ${i.type === "tv" ? `<span class="sub">${esc(i.badge)}</span>` : ""}
    <span class="meta">${meta}</span>
  </button>`;
}

// Media di IMDb e Rotten Tomatoes (in decimi); senza, il voto TMDB
function ratingHtml(i) {
  const v = i.voti;
  if (v?.media != null) {
    const parts = [v.imdb ? `IMDb ${fmtVote(v.imdb.voto)}` : "", v.rt ? `Rotten Tomatoes ${v.rt.voto}%` : ""].filter(Boolean);
    return `<span class="rating avg" title="Media di ${esc(parts.join(" e "))}">★ ${fmtVote(v.media)}</span>`;
  }
  return i.rating ? `<span class="rating" title="Voto TMDB">★ ${fmtVote(i.rating)}</span>` : "";
}

function fmtVote(n) { return Number(n).toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 }); }

// Cuore e stelle che hai dato, nell'angolo della locandina
function tasteHtml(i) {
  const g = state.gusti.get(i.id);
  if (!g) return "";
  return `<span class="my-taste">${g.mi_piace ? `<i class="h">♥</i>` : ""}${g.voto != null ? `<i>★ ${g.voto}</i>` : ""}${!g.mi_piace && g.voto == null ? `<i>✎</i>` : ""}</span>`;
}

const SEEN_LABELS = { Completed: "Visto", "In progress": "Lo stai guardando", Planning: "Da vedere", Paused: "In pausa", Dropped: "Abbandonato" };

function seenHtml(i) {
  const v = state.visti[i.id];
  return v ? `<span class="seen ${v.stato === "Completed" ? "done" : ""}">${esc(SEEN_LABELS[v.stato] || "Su Yamtrack")}</span>` : "";
}

// Voto mostrato in "I migliori": Tomatometer se c'e', altrimenti IMDb (o TMDB)
function scoreHtml(i) {
  if (i.rt) {
    const fresh = i.rt.voto >= 60;
    return `<span class="medal rt ${fresh ? "" : "rotten"}" title="Rotten Tomatoes: ${i.rt.voto}% delle recensioni della critica sono positive"><i>${fresh ? "🍅" : "🤢"}</i><b>${i.rt.voto}%</b></span>`;
  }
  if (!i.rating) return "";
  return `<span class="medal" title="Voto ${esc(i.score?.fonte || "TMDB")} su ${(i.votes || 0).toLocaleString("it-IT")} voti"><i>${esc(i.score?.fonte || "★")}</i><b>${i.rating.toFixed(1)}</b></span>`;
}

function blockHtml(title, items, ranked = false) {
  const n = items.length;
  return `<section class="block">
    <div class="block-head"><h2>${esc(title)}</h2><span class="count">${n} ${n === 1 ? "titolo" : "titoli"}</span></div>
    <div class="grid ${ranked ? "ranked" : ""}">${items.map((it, n) => cardHtml(it, n)).join("")}</div>
  </section>`;
}

function render() {
  const items = filtered();
  content.setAttribute("aria-busy", "false");
  $("#empty").hidden = items.length > 0 || !state.data;

  if (state.view === "richieste") {
    renderRequests();
    return;
  }
  if (state.view === "per_te") {
    renderForYou(items);
    return;
  }
  if (state.view === "tendenze") {
    content.innerHTML = items.length ? blockHtml("I più visti questa settimana", items, true) : "";
    return;
  }
  if (state.view === "migliori") {
    const note = state.data && !state.data.rotten_tomatoes
      ? `<p class="note">Per ordinare la classifica con i voti di <b>Rotten Tomatoes</b> serve una chiave OMDb gratuita: inseriscila nel menu impostazioni (l'ingranaggio in alto). Intanto la classifica usa i voti IMDb.</p>`
      : "";
    content.innerHTML = items.length ? note + blockHtml(periodTitle(), items.slice(0, 100)) : note;
    $("#empty").hidden = items.length > 0 || !state.data;
    return;
  }
  if (state.view === "bambini") {
    const movies = items.filter((i) => i.type === "movie");
    const shows = items.filter((i) => i.type === "tv");
    content.innerHTML = (movies.length ? blockHtml("Film per bambini", movies) : "") +
      (shows.length ? blockHtml("Serie per bambini", shows) : "");
    return;
  }
  if (state.view === "prossimamente") {
    const groups = new Map();
    for (const i of items) {
      const d = -daysAgo(i.date);
      const label = d <= 7 ? "Questa settimana" : d <= 14 ? "La prossima settimana"
        : `A ${toDate(i.date).toLocaleDateString("it-IT", { month: "long" })}`;
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(i);
    }
    content.innerHTML = [...groups].map(([label, list]) => blockHtml(label, list)).join("");
    return;
  }
  const groups = BUCKETS.map((b) => ({ ...b, items: [] }));
  for (const i of items) groups.find((g) => daysAgo(i.date) <= g.max).items.push(i);
  content.innerHTML = groups.filter((g) => g.items.length).map((g) => blockHtml(g.label, g.items)).join("");
}

function platformCounts() {
  // Quanti titoli ha ogni piattaforma con gli altri filtri attuali
  const items = filtered(state.view, "platforms");
  const counts = { all: items.length };
  for (const i of items) for (const p of i.platforms || []) counts[p] = (counts[p] || 0) + 1;
  return counts;
}

function chipHtml(key, name, logo, color, count, on) {
  return `<button class="chip ${on ? "on" : ""} ${key === "all" ? "all" : ""}" data-p="${esc(key)}" style="--c:${esc(color)}" aria-pressed="${on}">
    <span class="chip-logo">${logo}</span>
    <span class="chip-text"><span class="chip-name">${esc(name)}</span><span class="chip-count">${count} ${count === 1 ? "titolo" : "titoli"}</span></span>
    <span class="chip-check" aria-hidden="true">✓</span>
  </button>`;
}

function renderChips() {
  const ps = state.data?.piattaforme || {};
  const counts = platformCounts();
  const allIcon = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h7v7H4zm9 0h7v7h-7zM4 13h7v7H4zm9 0h7v7h-7z"/></svg>`;
  $("#platforms").innerHTML =
    chipHtml("all", "Tutte", allIcon, "#f3c463", counts.all, !state.platforms.size) +
    Object.entries(ps).map(([k, p]) => chipHtml(
      k, p.name, p.logo ? `<img src="${esc(p.logo)}" alt="">` : "", p.color, counts[k] || 0, state.platforms.has(k),
    )).join("");
}

function renderGenres() {
  // Solo le categorie che hanno titoli con gli altri filtri attuali (e quella scelta)
  const items = filtered(state.view, "categoria");
  const counts = {};
  for (const i of items) for (const c of i.categorie || []) counts[c] = (counts[c] || 0) + 1;
  const cats = (state.data?.categorie || []).filter((c) => counts[c.id] || c.id === state.categoria);
  $("#genres").hidden = !cats.length;
  $("#genres").innerHTML =
    `<button class="genre ${state.categoria ? "" : "on"}" data-c="" aria-pressed="${!state.categoria}">Tutti i generi</button>` +
    cats.map((c) => `<button class="genre ${c.id === state.categoria ? "on" : ""}" data-c="${esc(c.id)}" aria-pressed="${c.id === state.categoria}">${esc(c.nome)}<small>${counts[c.id] || 0}</small></button>`).join("");
}

function renderPeriods() {
  const el = $("#periods");
  el.hidden = state.view !== "migliori";
  if (el.hidden) return;
  const opts = periodOptions();
  if (!opts.some((o) => o.id === state.periodo)) state.periodo = "n5";
  el.innerHTML = `<span class="periods-label">Periodo</span>` + opts.map((o) =>
    `<button class="period ${o.id === state.periodo ? "on" : ""} ${o.id.startsWith("n") ? "span" : ""}" data-p="${o.id}" aria-pressed="${o.id === state.periodo}">${esc(o.label)}</button>`).join("");
}

function renderFilters() {
  renderPeriods();
  renderGenres();
  renderChips();
}

function renderStatus(s) {
  if (s.versione) $("#version").textContent = `CosaGuardo · versione ${s.versione}`;
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
  if (state.view === "richieste") return [];
  const pool = state.view === "novita"
    ? [...filtered("novita")].filter((i) => daysAgo(i.date) <= 14).sort((a, b) => b.popularity - a.popularity)
    : state.view === "prossimamente"
      ? [...filtered("prossimamente")].filter((i) => -daysAgo(i.date) <= 21).sort((a, b) => b.popularity - a.popularity)
      : filtered(state.view);
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
    const where = (i.platforms || []).map((p) => platform(p)?.name).filter(Boolean).join(", ");
    $("#hero-eyebrow").textContent = {
      tendenze: `N° ${i.rank} di tendenza in Italia`,
      prossimamente: `${i.type === "movie" ? "Film" : i.badge} · dal ${fmtLong.format(toDate(i.date))}${where ? ` su ${where}` : ""}`,
      migliori: `${i.rt ? `🍅 ${i.rt.voto}% Rotten Tomatoes` : `${i.score?.fonte || "★"} ${i.rating?.toFixed(1)}`} · ${periodTitle()}`,
      bambini: `${i.type === "movie" ? "Film" : "Serie"} per bambini`,
      per_te: `Suggerito per te · ${i.type === "movie" ? "Film" : "Serie TV"} del ${i.year}`,
    }[state.view] || `${i.type === "movie" ? "Film" : i.badge} · ${relDate(i.date)}`;
    $("#hero-title").textContent = i.title;
    const score = i.voti?.media != null ? `★ ${fmtVote(i.voti.media)}` : i.rating ? `★ ${i.rating}` : "";
    $("#hero-meta").textContent = [i.year, (i.genres || []).slice(0, 3).join(", "), score].filter(Boolean).join("  ·  ");
    $("#hero-overview").textContent = state.view === "per_te" && i.motivo ? i.motivo : i.overview;
    $("#hero-platforms").innerHTML = (i.platforms || []).map(logoHtml).join("");
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
  attachPersonal();
  document.body.classList.toggle("needs-key", !!state.data.serve_chiave);
  $("#setup").hidden = !state.data.serve_chiave;
  if (state.data.serve_chiave) {
    renderStatus({ ...state.data, errore: null });
    if (state.data.errore && state.data.aggiornato === null && $("#setup-key").value) showSetupMsg(state.data.errore, true);
    return;
  }
  renderStatus(state.data);
  renderFilters();
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
  // Prima la sezione aperta (es. in "I migliori" l'elemento ha il voto IMDb), poi le altre
  const d = state.data || {};
  const lists = [d[state.view], d.novita, d.tendenze, d.prossimamente, d.migliori, d.per_te, d.bambini,
    [...state.gusti.values()], state.requests];
  for (const list of lists) {
    const found = (list || []).find((x) => x.id === id);
    if (found) return found;
  }
  return undefined;
}

// -------------------------------------------------------------- trailer

const trailerCache = new Map();

async function loadTrailer(i) {
  const key = `${i.type}/${i.tmdb_id}`;
  if (!trailerCache.has(key)) {
    trailerCache.set(key, fetch(`/api/trailer/${key}`).then((r) => r.ok ? r.json() : {}).then((d) => d.trailer || null).catch(() => null));
  }
  return trailerCache.get(key);
}

function playTrailer(t) {
  const hero = $("#d-hero");
  hero.classList.add("playing");
  $("#d-play").hidden = true;
  const f = document.createElement("iframe");
  f.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(t.key)}?autoplay=1&rel=0&modestbranding=1&hl=it&cc_lang_pref=it`;
  f.title = t.name || "Trailer";
  f.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
  f.allowFullscreen = true;
  hero.appendChild(f);
  hero.scrollIntoView({ behavior: "smooth", block: "start" });
}

function stopTrailer() {
  $("#d-hero").classList.remove("playing");
  $("#d-hero iframe")?.remove();
}

// ------------------------------------------------------------ richieste

const REQ_LABELS = {
  assente: "＋ Richiedi",
  richiesto: "⏳ Richiesto, in attesa",
  parziale: "⬇ In parte scaricato",
  disponibile: "✓ Già scaricato",
  sconosciuto: "Servizio non raggiungibile",
};
const REQ_BADGES = { assente: "Non in libreria", richiesto: "Richiesto", parziale: "In parte", disponibile: "Scaricato", sconosciuto: "Stato sconosciuto" };

function fmtSize(bytes) {
  if (!bytes) return "";
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${gb.toLocaleString("it-IT", { maximumFractionDigits: 1 })} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
}

function fmtTimeLeft(t) {
  // "01:02:03" o "1.02:03:04" (giorni.ore:min:sec)
  if (!t) return "";
  const m = /^(?:(\d+)\.)?(\d+):(\d+):\d+$/.exec(t);
  if (!m) return "";
  const [d, h, min] = [Number(m[1] || 0), Number(m[2]), Number(m[3])];
  if (d) return `circa ${d} g ${h} h`;
  if (h) return `circa ${h} h ${min} min`;
  return min ? `circa ${min} min` : "quasi finito";
}

function progressHtml(dl) {
  if (!dl) return "";
  const label = dl.in_pausa ? "In pausa" : dl.in_attesa ? "In coda" : `${String(dl.percentuale).replace(".", ",")}%`;
  const extra = [dl.download > 1 ? `${dl.download} file` : "", fmtTimeLeft(dl.tempo_rimanente)].filter(Boolean).join(" · ");
  return `<div class="dl"><div class="dl-bar"><span style="width:${Math.max(2, dl.percentuale)}%"></span></div>
    <div class="dl-text"><b>⬇ ${esc(label)}</b>${extra ? `<span>${esc(extra)}</span>` : ""}</div></div>`;
}

function seasonsSummary(det) {
  const wanted = (det?.stagioni || []).filter((s) => s.monitorata);
  if (!wanted.length) return "";
  const names = wanted.length === det.stagioni.length ? "Tutte le stagioni" : `Stagion${wanted.length === 1 ? "e" : "i"} ${wanted.map((s) => s.numero).join(", ")}`;
  return `${names} · ${det.episodi_scaricati}/${det.episodi_previsti} episodi`;
}

function seasonBarsHtml(list, cls = "") {
  return `<div class="season-bars ${cls}">${list.map((s) => {
    const pct = s.tot ? Math.min(100, Math.round(s.have / s.tot * 100)) : s.have ? 100 : 0;
    const full = s.tot && s.have >= s.tot;
    return `<div class="sb ${s.off ? "off" : ""} ${full ? "full" : ""}" title="Stagione ${s.n}: ${s.have}${s.tot ? `/${s.tot}` : ""} episodi">
      <span class="sb-n">S${s.n}</span><span class="sb-bar"><i style="width:${pct}%"></i></span><span class="sb-c">${full ? "✓ " : ""}${s.have}${s.tot ? `/${s.tot}` : ""}</span></div>`;
  }).join("")}</div>`;
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// Stato della serie: prossima stagione o episodio in arrivo, oppure serie terminata
function seriesRowHtml(serie) {
  const p = serie.prossimo;
  const when = (iso) => {
    const d = -daysAgo(iso);
    const rel = d <= 0 ? "oggi" : d === 1 ? "domani" : `fra ${d} giorni`;
    return `${fmtLong.format(toDate(iso))} · ${rel}`;
  };
  let icon = "📅", cls = "next", title, text;
  if (p?.nuova_stagione) {
    title = p.stagione === 1 ? "La serie debutta" : `Stagione ${p.stagione} in arrivo`;
    text = p.data ? when(p.data) : "Confermata, data non ancora annunciata";
  } else if (p) {
    title = `Stagione ${p.stagione} in corso`;
    text = `Episodio ${p.episodio}: ${when(p.data)}`;
  } else if (serie.stato === "terminata" || serie.stato === "cancellata") {
    icon = "■"; cls = "ended";
    title = serie.stato === "terminata" ? "Serie terminata" : "Serie cancellata";
    const n = serie.stagioni?.length || 0;
    text = [n ? plural(n, "stagione", "stagioni") : "", serie.ultimo?.data ? `ultimo episodio il ${fmtLong.format(toDate(serie.ultimo.data))}` : ""]
      .filter(Boolean).join(", ") || "Non sono previste nuove stagioni";
  } else if (serie.stato === "in_produzione") {
    title = "In produzione";
    text = "Data di uscita non ancora annunciata";
  } else {
    icon = "…"; cls = "wait";
    title = "Serie in corso";
    text = "Nessuna nuova stagione annunciata per ora";
  }
  return `<div class="st-row serie ${cls}"><span class="st-icon">${icon}</span><div><b>${esc(title)}</b><span>${esc(text)}</span></div></div>`;
}

function statusPanelHtml(i, r) {
  const rows = [];
  if (r.serie) rows.push(seriesRowHtml(r.serie));
  if (r.plex) {
    const what = i.type === "tv"
      ? `${plural(r.plex.stagioni, "stagione", "stagioni")} · ${r.plex.episodi} episodi`
      : "Pronto da guardare";
    rows.push(`<div class="st-row plex"><span class="st-icon">▶</span><div><b>Già su Plex</b><span>${esc(what)}</span></div>
      ${r.plex.url ? `<a class="st-link" href="${esc(r.plex.url)}" target="_blank" rel="noopener">Apri in Plex</a>` : ""}</div>`);
    if (r.plex.per_stagione?.length) {
      rows.push(seasonBarsHtml(r.plex.per_stagione.map((s) => ({ n: s.numero, have: s.episodi, tot: s.totali, off: !s.episodi })), "plex"));
    }
  }
  const det = r.dettagli;
  if (det) {
    let text;
    if (i.type === "tv") {
      text = seasonsSummary(det) || "Nessuna stagione monitorata";
    } else {
      text = det.stato === "disponibile" ? ["Scaricato", det.qualita, fmtSize(det.dimensione)].filter(Boolean).join(" · ") : "In attesa del download";
    }
    rows.push(`<div class="st-row arr ${esc(det.stato)}"><span class="st-icon">${det.stato === "disponibile" ? "✓" : "⏳"}</span>
      <div><b>In ${esc(r.servizio)}</b><span>${esc(text)}</span></div></div>`);
    if (i.type === "tv" && det.stagioni?.length) {
      rows.push(seasonBarsHtml(det.stagioni.map((s) => ({ n: s.numero, have: s.scaricati, tot: s.totali, off: !s.monitorata }))));
    }
  }
  if (r.download) rows.push(progressHtml(r.download));
  return rows.join("");
}

function setRequestButton(stato, servizio, det) {
  const b = $("#d-request");
  b.className = `btn-request ${stato || ""}`;
  b.disabled = stato !== "assente";
  b.textContent = stato === "assente" ? `${REQ_LABELS.assente} a ${servizio}` : REQ_LABELS[stato] || "";
  b.title = stato === "assente" ? `Aggiunge il titolo a ${servizio} e avvia la ricerca` : "";
  // Serie gia' in Sonarr con stagioni non ancora richieste
  $("#d-more").hidden = !(det?.stagioni?.some((s) => !s.monitorata));
}

function requestMsg(text, isError) {
  const m = $("#d-request-msg");
  m.textContent = text || "";
  m.className = `d-request-msg ${isError ? "err" : "ok"}`;
}

async function loadRequestState(i) {
  const svc = i.type === "movie" ? state.servizi.radarr : state.servizi.sonarr;
  const b = $("#d-request");
  const panel = $("#d-status");
  b.hidden = !svc;
  $("#d-more").hidden = true;
  panel.hidden = true;
  if (!svc && !state.servizi.plex && i.type !== "tv") return;
  if (svc) {
    b.className = "btn-request loading";
    b.disabled = true;
    b.textContent = "Controllo…";
  }
  try {
    const r = await (await fetch(`/api/richieste/${encodeURIComponent(i.id)}`)).json();
    if ($("#detail").dataset.id !== i.id) return;
    if (svc) setRequestButton(r.stato, r.servizio, r.dettagli);
    if (r.errore) requestMsg(r.errore, true);
    panel.innerHTML = statusPanelHtml(i, r);
    panel.hidden = !panel.innerHTML.trim();
  } catch {
    if (svc) setRequestButton("sconosciuto");
  }
}

// Scelta delle stagioni (solo serie)
async function openSeasons() {
  const id = $("#detail").dataset.id;
  const i = findItem(id);
  const box = $("#d-seasons");
  box.hidden = false;
  $("#d-seasons-grid").innerHTML = `<p class="seasons-loading">Carico le stagioni…</p>`;
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  try {
    const r = await fetch(`/api/serie/${i.tmdb_id}/stagioni`);
    if (!r.ok) throw new Error();
    const list = (await r.json()).stagioni;
    if ($("#detail").dataset.id !== id) return;
    state.seasons = list.map((s) => {
      const requested = !!s.sonarr?.monitorata;
      const plexFull = s.plex != null && s.episodi && s.plex >= s.episodi;
      return { ...s, requested, selected: !requested && !plexFull };
    });
    renderSeasons();
  } catch {
    $("#d-seasons-grid").innerHTML = `<p class="seasons-loading err">Impossibile caricare le stagioni.</p>`;
  }
}

function renderSeasons() {
  const list = state.seasons || [];
  $("#d-seasons-grid").innerHTML = list.map((s, k) => {
    const notes = [];
    if (s.requested) notes.push(`<em class="n-req">Già richiesta${s.sonarr.totali ? ` · ${s.sonarr.scaricati}/${s.sonarr.totali}` : ""}</em>`);
    if (s.plex != null) notes.push(`<em class="n-plex">Su Plex ${s.plex}/${s.episodi}</em>`);
    if (s.data && s.data > new Date().toISOString().slice(0, 10)) notes.push(`<em>Dal ${esc(fmtShort.format(toDate(s.data)))}</em>`);
    return `<button type="button" class="season ${s.selected || s.requested ? "on" : ""} ${s.requested ? "locked" : ""}" data-k="${k}" ${s.requested ? "disabled" : ""}>
      <span class="season-check">✓</span>
      <span class="season-name">Stagione ${s.numero}</span>
      <span class="season-eps">${s.episodi} episodi</span>
      ${notes.join("")}
    </button>`;
  }).join("") || `<p class="seasons-loading">Nessuna stagione trovata.</p>`;
  const n = list.filter((s) => s.selected).length;
  const ok = $("#d-seasons-ok");
  ok.disabled = !n;
  ok.textContent = n ? `Richiedi ${n} stagion${n === 1 ? "e" : "i"} a Sonarr` : "Scegli almeno una stagione";
  const free = list.filter((s) => !s.requested);
  $("#d-seasons-all").textContent = free.length && free.every((s) => s.selected) ? "Deseleziona tutte" : "Seleziona tutte";
}

$("#d-seasons-grid").addEventListener("click", (e) => {
  const b = e.target.closest(".season[data-k]");
  if (!b || b.disabled) return;
  const s = state.seasons[Number(b.dataset.k)];
  s.selected = !s.selected;
  renderSeasons();
});
$("#d-seasons-all").addEventListener("click", () => {
  const free = state.seasons.filter((s) => !s.requested);
  const all = free.every((s) => s.selected);
  free.forEach((s) => { s.selected = !all; });
  renderSeasons();
});
$("#d-seasons-cancel").addEventListener("click", () => { $("#d-seasons").hidden = true; });
$("#d-seasons-ok").addEventListener("click", () => {
  const free = state.seasons.filter((s) => !s.requested);
  const chosen = state.seasons.filter((s) => s.selected).map((s) => s.numero);
  // Tutte le stagioni di una serie nuova: le future verranno monitorate da Sonarr
  const all = free.length === state.seasons.length && chosen.length === state.seasons.length;
  sendRequest(all ? null : chosen);
});

async function sendRequest(stagioni) {
  const id = $("#detail").dataset.id;
  const item = findItem(id);
  const b = $("#d-request");
  b.className = "btn-request loading";
  b.disabled = true;
  b.textContent = "Invio richiesta…";
  $("#d-more").hidden = true;
  $("#d-seasons").hidden = true;
  requestMsg("");
  try {
    const r = await fetch("/api/richieste", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(stagioni === undefined ? { id } : { id, stagioni }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Richiesta non riuscita.");
    requestMsg(`Fatto! ${body.servizio} sta cercando ${stagioni?.length ? `le stagioni ${stagioni.join(", ")}` : "il titolo"}.`);
    state.requestsLoaded = false;
    loadRequestState(item);
  } catch (err) {
    setRequestButton("assente", item?.type === "movie" ? "Radarr" : "Sonarr");
    requestMsg(err.message, true);
  }
}

async function loadServizi() {
  try {
    state.servizi = await (await fetch("/api/servizi")).json();
  } catch { /* ignora */ }
  const any = state.servizi.radarr || state.servizi.sonarr;
  document.querySelector('.tab[data-view="richieste"]').hidden = !any;
  if (!any && state.view === "richieste") setView("novita");
  if (state.servizi.plex) loadPlex();
  if (state.servizi.yamtrack) loadVisti();
}

async function loadPlex() {
  try {
    const d = await (await fetch("/api/plex")).json();
    state.plex = { movie: new Set(d.movie), tv: new Set(d.tv) };
    if (state.view !== "richieste") render();
  } catch { /* ignora */ }
}

function requestCardHtml(r, n) {
  const poster = r.poster
    ? `<img class="p" src="${esc(r.poster)}" alt="" loading="lazy" onload="this.classList.add('ok')" onerror="this.classList.add('ok')">`
    : `<div class="noimg">${esc(r.title)}</div>`;
  const when = new Date(r.richiesto_il).toLocaleDateString("it-IT", { day: "numeric", month: "short" });
  const det = r.dettagli;
  const info = r.type === "tv"
    ? seasonsSummary(det) || (r.stagioni ? `Stagioni ${r.stagioni.join(", ")}` : "Tutte le stagioni")
    : det?.stato === "disponibile" ? ["Scaricato", det.qualita, fmtSize(det.dimensione)].filter(Boolean).join(" · ") : "";
  return `<button class="card req-card" data-id="${esc(r.id)}" style="--i:${Math.min(n, 18)}">
    <div class="poster">
      ${poster}
      <span class="pill">${r.type === "movie" ? "Film" : "Serie TV"}</span>
      <div class="corner"><span class="req-status ${esc(r.stato)}">${esc(REQ_BADGES[r.stato] || r.stato)}</span>
        ${r.plex ? `<span class="on-plex">Su Plex${r.type === "tv" && r.plex.stagioni ? ` · ${r.plex.stagioni} st.` : ""}</span>` : ""}</div>
      ${r.download ? progressHtml(r.download) : `<div class="logos">${(r.platforms || []).map(logoHtml).join("")}</div>`}
      <span class="glare"></span>
    </div>
    <h3>${esc(r.title)}</h3>
    ${info ? `<span class="sub">${esc(info)}</span>` : ""}
    <span class="meta"><b>Richiesto ${esc(when)}</b> · ${r.type === "movie" ? "Radarr" : "Sonarr"}</span>
  </button>`;
}

let requestsTimer = null;

async function renderRequests(silent = false) {
  if (!state.requestsLoaded) {
    if (!silent) content.innerHTML = `<section class="block"><div class="grid">${'<div class="card skeleton"><div class="poster"></div><h3></h3><span class="meta"></span></div>'.repeat(6)}</div></section>`;
    try {
      const d = await (await fetch("/api/richieste")).json();
      state.requests = d.richieste || [];
      state.requestsLoaded = true;
    } catch {
      content.innerHTML = `<div class="req-empty"><h3>Richieste non disponibili</h3><p>Impossibile contattare il server.</p></div>`;
      return;
    }
  }
  if (state.view !== "richieste") return;
  $("#empty").hidden = true;
  const q = state.q.toLowerCase();
  const list = state.requests.filter((r) => (state.tipo === "all" || r.type === state.tipo) &&
    (!q || (r.title || "").toLowerCase().includes(q)));
  const active = list.filter((r) => r.download).length;
  content.innerHTML = list.length
    ? `<section class="block">
        <div class="block-head"><h2>Le mie richieste</h2><span class="count">${list.length} ${list.length === 1 ? "titolo" : "titoli"}${active ? ` · ${active} in download` : ""}</span></div>
        <div class="grid">${list.map(requestCardHtml).join("")}</div>
      </section>`
    : state.requests.length
      ? `<div class="req-empty"><h3>Nessuna richiesta con questi filtri</h3><p>Prova a scegliere “Tutti” o a cambiare la ricerca.</p></div>`
      : `<div class="req-empty"><h3>Nessuna richiesta, per ora</h3><p>Apri un titolo e premi “Richiedi”: i film vanno a Radarr, le serie a Sonarr.</p></div>`;
  if (silent) content.querySelectorAll(".card").forEach((c) => { c.style.animation = "none"; });
  // Con download in corso aggiorna i progressi ogni 15 secondi
  clearTimeout(requestsTimer);
  if (active) {
    requestsTimer = setTimeout(() => {
      if (state.view !== "richieste") return;
      state.requestsLoaded = false;
      renderRequests(true);
    }, 15000);
  }
}

function openDetail(id) {
  const i = findItem(id);
  if (!i) return;
  const dlg = $("#detail");
  dlg.dataset.id = id;
  stopTrailer();
  requestMsg("");
  $("#d-play").hidden = true;
  $("#d-trailer").hidden = true;
  $("#d-seasons").hidden = true;
  state.seasons = null;
  loadTrailer(i).then((t) => {
    if (!t || dlg.dataset.id !== id) return;
    $("#d-play").hidden = false;
    $("#d-trailer").hidden = false;
    $("#d-play").onclick = $("#d-trailer").onclick = () => playTrailer(t);
  });
  loadRequestState(i);
  $("#d-hero").style.backgroundImage = i.backdrop ? `url("${i.backdrop.replace("/w780/", "/w1280/")}")` : "";
  const poster = $("#d-poster");
  poster.hidden = !i.poster;
  if (i.poster) poster.src = i.poster;
  $("#d-badge").textContent = i.type === "movie" ? "Film" : i.badge || "Serie TV";
  $("#d-title").textContent = i.title;
  const orig = i.original_title || "";
  $("#d-orig").textContent = orig.toLowerCase() !== (i.title || "").toLowerCase() ? orig : "";
  $("#d-meta").textContent = [
    i.date ? `Uscita ${fmtLong.format(toDate(i.date))}` : null,
    (i.genres || []).join(", ") || null,
    i.runtime ? `${i.runtime} min` : null,
    i.seasons ? `${i.seasons} stagion${i.seasons === 1 ? "e" : "i"}` : null,
    i.rating && !i.voti ? `${i.score ? `${i.score.fonte} ` : ""}★ ${i.rating} (${(i.votes || 0).toLocaleString("it-IT")} voti)` : null,
  ].filter(Boolean).join("  ·  ");
  $("#d-overview").textContent = i.overview || "Trama non ancora disponibile.";
  $("#d-why").hidden = !i.motivo;
  $("#d-why").innerHTML = i.motivo ? `<b>Perché te lo suggeriamo</b><span>${esc(i.motivo)}</span>` : "";
  renderScores(i.voti);
  loadScores(i);
  renderTaste(i);
  $("#d-platforms").innerHTML = (i.platforms || []).map(badgeHtml).join("");
  $("#d-links").innerHTML =
    (i.watch_link ? `<a href="${esc(i.watch_link)}" target="_blank" rel="noopener">▶ Dove guardarlo</a>` : "") +
    (i.imdb_id ? `<a href="https://www.imdb.com/title/${esc(i.imdb_id)}/" target="_blank" rel="noopener">Scheda su IMDb</a>` : "") +
    `<a href="${esc(i.tmdb_url || `https://www.themoviedb.org/${i.type}/${i.tmdb_id}`)}" target="_blank" rel="noopener">Scheda completa su TMDB</a>`;
  $("#detail").showModal();
}

// --------------------------------------------------- voti nella scheda

function renderScores(v) {
  const el = $("#d-scores");
  el.hidden = !v;
  if (!v) { el.innerHTML = ""; return; }
  const chips = [];
  if (v.imdb) {
    chips.push(`<a class="score imdb" href="${esc(v.imdb.url)}" target="_blank" rel="noopener" title="${v.imdb.voti.toLocaleString("it-IT")} voti su IMDb">
      <i>IMDb</i><b>${fmtVote(v.imdb.voto)}</b><small>/10</small></a>`);
  }
  if (v.rt) {
    const fresh = v.rt.voto >= 60;
    chips.push(`<a class="score rt ${fresh ? "" : "rotten"}" href="${esc(v.rt.url)}" target="_blank" rel="noopener" title="Tomatometer: recensioni positive della critica">
      <i>${fresh ? "🍅" : "🤢"} Rotten Tomatoes</i><b>${v.rt.voto}%</b></a>`);
  }
  if (v.imdb && v.rt) {
    chips.push(`<span class="score avg" title="Media tra IMDb e Rotten Tomatoes (in decimi)"><i>Media</i><b>${fmtVote(v.media)}</b><small>/10</small></span>`);
  }
  el.innerHTML = chips.join("");
}

const scoresCache = new Map();

async function loadScores(i) {
  if (i.date && i.date > new Date().toISOString().slice(0, 10)) return;  // non ancora uscito
  const key = `${i.type}/${i.tmdb_id}`;
  if (!scoresCache.has(key)) {
    scoresCache.set(key, fetch(`/api/voti/${key}`).then((r) => r.ok ? r.json() : {}).then((d) => d.voti || null).catch(() => null));
  }
  const v = await scoresCache.get(key);
  if ($("#detail").dataset.id === i.id && v) renderScores(v);
}

// ------------------------------------------- mi piace, stelle e note

function attachPersonal() {
  if (!state.data) return;
  state.data.per_te = state.suggeriti?.titoli || [];
  state.data.preferiti = [...state.gusti.values()];
}

async function loadGusti() {
  try {
    const d = await (await fetch("/api/gusti")).json();
    state.gusti = new Map(d.titoli.map((g) => [g.id, g]));
    attachPersonal();
    if (state.data && state.view !== "richieste") render();
  } catch { /* ignora */ }
}

async function loadVisti() {
  try {
    const d = await (await fetch("/api/visti")).json();
    state.visti = d.titoli || {};
    if (state.data && state.view !== "richieste") render();
  } catch { /* ignora */ }
}

function renderTaste(i) {
  const g = state.gusti.get(i.id) || {};
  const like = $("#d-like");
  like.classList.toggle("on", !!g.mi_piace);
  like.setAttribute("aria-pressed", !!g.mi_piace);
  like.querySelector("span").textContent = g.mi_piace ? "Ti piace" : "Mi piace";
  const v = g.voto ?? null;
  $("#d-stars").innerHTML =
    `<button type="button" class="zero ${v === 0 ? "on" : ""}" data-v="0" role="radio" aria-checked="${v === 0}" title="0 stelle: non mi è piaciuto">0</button>` +
    [1, 2, 3, 4, 5].map((n) => `<button type="button" class="star ${v != null && n <= v ? "on" : ""}" data-v="${n}" role="radio" aria-checked="${v === n}" title="${n} stell${n === 1 ? "a" : "e"}">★</button>`).join("") +
    `<span class="stars-label">${v == null ? "Il tuo voto" : `${v} su 5`}</span>`;
  const note = $("#d-note");
  note.value = g.note || "";
  note.dataset.saved = g.note || "";
  $("#d-note-save").hidden = true;
  tasteMsg("");
  const seen = state.visti[i.id];
  $("#d-seen").hidden = !seen;
  if (seen) {
    $("#d-seen").textContent = `Yamtrack: ${SEEN_LABELS[seen.stato] || seen.stato}${seen.voto != null ? ` · voto ${seen.voto}/10` : ""}`;
  }
}

function tasteMsg(text, isError) {
  const m = $("#d-taste-msg");
  m.textContent = text || "";
  m.className = `taste-msg ${isError ? "err" : "ok"}`;
}

function scheda(i) {
  const keys = ["id", "tmdb_id", "type", "title", "original_title", "year", "date", "badge", "poster", "backdrop", "overview",
    "genres", "categorie", "platforms", "watch_link", "tmdb_url", "imdb_id", "runtime", "seasons", "voti"];
  return Object.fromEntries(keys.filter((k) => i[k] != null).map((k) => [k, i[k]]));
}

async function saveTaste(values) {
  const id = $("#detail").dataset.id;
  const i = findItem(id);
  if (!i) return;
  try {
    const r = await fetch(`/api/gusti/${encodeURIComponent(id)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...values, scheda: scheda(i) }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Non sono riuscito a salvare.");
    if (body.gusto) state.gusti.set(id, body.gusto);
    else state.gusti.delete(id);
    attachPersonal();
    if ($("#detail").dataset.id === id) {
      renderTaste(i);
      tasteMsg(values.note !== undefined ? "Note salvate." : "Salvato: terrò conto di questo nei suggerimenti.");
    }
    if (state.suggeriti) state.suggeriti.da_aggiornare = true;
    if (state.view !== "richieste") render();
  } catch (err) {
    tasteMsg(err.message, true);
  }
}

$("#d-like").addEventListener("click", () => {
  const g = state.gusti.get($("#detail").dataset.id);
  saveTaste({ mi_piace: !g?.mi_piace });
});
$("#d-stars").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-v]");
  if (!b) return;
  const v = Number(b.dataset.v);
  const g = state.gusti.get($("#detail").dataset.id);
  saveTaste({ voto: g?.voto === v ? null : v });
});
$("#d-note").addEventListener("input", (e) => {
  $("#d-note-save").hidden = e.target.value.trim() === (e.target.dataset.saved || "").trim();
  tasteMsg("");
});
function saveNote() {
  const note = $("#d-note");
  if (note.value.trim() === (note.dataset.saved || "").trim()) return;
  note.dataset.saved = note.value.trim();
  saveTaste({ note: note.value });
}
$("#d-note-save").addEventListener("click", saveNote);
$("#d-note").addEventListener("blur", saveNote);

// ----------------------------------------------------- suggeriti per te

let suggTimer = null;

async function loadSuggeriti(force = false) {
  clearTimeout(suggTimer);
  try {
    const r = await fetch(force ? "/api/suggeriti/aggiorna" : "/api/suggeriti", { method: force ? "POST" : "GET" });
    const body = await r.json();
    if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Suggerimenti non disponibili.");
    state.suggeriti = body;
  } catch (err) {
    state.suggeriti = { ...(state.suggeriti || { titoli: [] }), errore: err.message, in_corso: false };
  }
  attachPersonal();
  if (state.view === "per_te") {
    renderFilters();
    renderHero();
    render();
  }
  if (state.suggeriti.in_corso) suggTimer = setTimeout(() => loadSuggeriti(), 4000);
}

function forYouBarHtml(sg) {
  const when = sg.generato
    ? new Date(sg.generato).toLocaleString("it-IT", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
    : "";
  const by = sg.fonte === "ai" ? "Scelti da Claude" : "Consigliati da TMDB";
  const status = sg.in_corso
    ? `<span class="dot busy"></span> ${sg.ai ? "Claude sta scegliendo i titoli per te…" : "Cerco titoli simili a quelli che ti piacciono…"}`
    : sg.generato ? `${by} in base a ${plural(sg.gusti, "giudizio", "giudizi")}${sg.yamtrack ? " e a quello che hai visto su Yamtrack" : ""} · ${esc(when)}` : "";
  const btn = !sg.in_corso && (sg.gusti || sg.yamtrack)
    ? `<button class="btn-request ${sg.da_aggiornare ? "hot" : ""}" id="sugg-refresh" type="button">↻ ${sg.da_aggiornare ? "Aggiorna con i nuovi giudizi" : "Nuovi suggerimenti"}</button>` : "";
  const hint = !sg.ai
    ? `<p class="note">Per suggerimenti davvero su misura, che tengano conto delle tue note e dei film di tutti gli ultimi 50 anni, aggiungi una <b>chiave API di Anthropic</b> nel menu impostazioni (l'ingranaggio in alto). Intanto uso i titoli consigliati da TMDB per quelli che ti sono piaciuti.</p>` : "";
  return `${hint}<div class="for-you-bar"><span class="for-you-status">${status}</span>${btn}</div>
    ${sg.errore ? `<p class="note err">${esc(sg.errore)}</p>` : ""}`;
}

function renderForYou(items) {
  const sg = state.suggeriti;
  $("#empty").hidden = true;
  if (!sg) {
    skeleton();
    return;
  }
  const mine = filtered("preferiti").sort((a, b) => (b.voto ?? -1) - (a.voto ?? -1) || (b.mi_piace ? 1 : 0) - (a.mi_piace ? 1 : 0));
  if (!sg.gusti && !sg.yamtrack && !items.length) {
    content.innerHTML = `<div class="req-empty"><h3>Dimmi cosa ti piace</h3>
      <p>Apri un film o una serie e premi <b>♥ Mi piace</b>, dai un voto da 0 a 5 stelle e scrivi nelle note cosa ti è piaciuto.<br>
      Qui troverai i titoli scelti per te, anche dei decenni passati.</p></div>`;
    return;
  }
  const sugg = items.length
    ? blockHtml("Suggeriti per te", items)
    : sg.in_corso ? `<section class="block"><div class="grid">${'<div class="card skeleton"><div class="poster"></div><h3></h3><span class="meta"></span></div>'.repeat(6)}</div></section>`
      : sg.titoli?.length ? `<div class="req-empty"><h3>Nessun suggerimento con questi filtri</h3><p>Prova a scegliere “Tutti” o un'altra piattaforma.</p></div>` : "";
  content.innerHTML = forYouBarHtml(sg) + sugg + (mine.length ? blockHtml("I tuoi giudizi", mine) : "");
}

content.addEventListener("click", (e) => {
  if (e.target.closest("#sugg-refresh")) loadSuggeriti(true);
});

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

// ------------------------------------------------------------- menu admin

const admin = { token: null, conf: null, values: {} };
try { admin.token = sessionStorage.getItem("adminToken"); } catch { /* ignora */ }

function setAdminToken(t) {
  admin.token = t;
  try { t ? sessionStorage.setItem("adminToken", t) : sessionStorage.removeItem("adminToken"); } catch { /* ignora */ }
}

async function adminFetch(url, opts = {}) {
  const r = await fetch(url, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(admin.token ? { "X-Admin-Token": admin.token } : {}) },
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(typeof body.detail === "string" ? body.detail : "Qualcosa è andato storto.");
    err.status = r.status;
    throw err;
  }
  return body;
}

function adminMsg(sel, text, isError) {
  const m = $(sel);
  m.textContent = text || "";
  m.className = `a-msg ${isError ? "err" : "ok"}`;
}

async function openAdmin() {
  const dlg = $("#admin");
  $("#a-lock").hidden = true;
  $("#a-panel").hidden = true;
  if (!dlg.open) dlg.showModal();
  let st;
  try {
    st = await adminFetch("/api/admin");
  } catch (err) {
    showLock(true);
    adminMsg("#a-lock-msg", err.message, true);
    return;
  }
  if (st.sbloccato) return showPanel();
  setAdminToken(null);
  showLock(st.pin_impostato);
}

function showLock(pinSet) {
  const form = $("#a-lock");
  form.dataset.mode = pinSet ? "login" : "create";
  $("#a-lock-lead").textContent = pinSet
    ? "Inserisci il PIN per cambiare le impostazioni."
    : "Primo accesso: scegli un PIN da 4 a 8 cifre. Servirà per entrare in questo menu.";
  $("#a-pin2-wrap").hidden = pinSet;
  $("#a-pin2").required = !pinSet;
  $("#a-lock-btn").textContent = pinSet ? "Entra" : "Imposta il PIN";
  $("#a-pin").value = "";
  $("#a-pin2").value = "";
  adminMsg("#a-lock-msg", "");
  $("#a-panel").hidden = true;
  form.hidden = false;
  $("#a-pin").focus();
}

$("#a-lock").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  const pin = $("#a-pin").value.trim();
  const create = form.dataset.mode === "create";
  if (!/^\d{4,8}$/.test(pin)) return adminMsg("#a-lock-msg", "Il PIN deve avere da 4 a 8 cifre.", true);
  if (create && pin !== $("#a-pin2").value.trim()) return adminMsg("#a-lock-msg", "I due PIN non coincidono.", true);
  const btn = $("#a-lock-btn");
  btn.disabled = true;
  try {
    const r = await adminFetch(create ? "/api/admin/pin" : "/api/admin/accesso", {
      method: "POST", body: JSON.stringify({ pin }),
    });
    setAdminToken(r.token);
    await showPanel();
  } catch (err) {
    form.classList.remove("shake");
    void form.offsetWidth;
    form.classList.add("shake");
    $("#a-pin").value = "";
    $("#a-pin").focus();
    adminMsg("#a-lock-msg", err.message, true);
  } finally {
    btn.disabled = false;
  }
});

function optionList(list, current, fallback) {
  const def = `<option value="">Predefinito${fallback ? ` (${esc(fallback)})` : " (il primo)"}</option>`;
  return def + list.map((o) => {
    const v = typeof o === "string" ? o : String(o.id);
    const label = typeof o === "string" ? o : o.nome;
    const sel = current && (current === v || (typeof o !== "string" && current.toLowerCase() === o.nome.toLowerCase()));
    return `<option value="${esc(v)}"${sel ? " selected" : ""}>${esc(label)}</option>`;
  }).join("");
}

function serviceHtml(key, s) {
  const intro = key === "sonarr"
    ? "Tag, profilo e cartella applicati alle serie che aggiungi da CosaGuardo."
    : "Tag, profilo e cartella applicati ai film che aggiungi da CosaGuardo.";
  const head = `<div class="a-sec-head"><h3>${esc(s.nome)}</h3><span class="a-pill ${s.configurato && !s.errore ? "on" : ""}">${
    !s.configurato ? "Non collegato" : s.errore ? "Non raggiungibile" : "Collegato"}</span></div>`;
  if (!s.configurato) {
    return `<section class="a-section off">${head}<p class="a-note">Aggiungi <code>${key.toUpperCase()}_URL</code> e <code>${key.toUpperCase()}_API_KEY</code> al container per collegarlo.</p></section>`;
  }
  if (s.errore || !s.opzioni) {
    return `<section class="a-section off">${head}<p class="a-note err">${esc(s.errore || "Opzioni non disponibili.")}</p></section>`;
  }
  const v = admin.values[key];
  const tags = s.opzioni.tag.length
    ? s.opzioni.tag.map((t) => `<button type="button" class="a-tag${v.tag.includes(t.id) ? " on" : ""}" data-svc="${key}" data-tag="${t.id}" aria-pressed="${v.tag.includes(t.id)}"><i aria-hidden="true">✓</i>${esc(t.nome)}</button>`).join("")
    : `<span class="a-note">Nessun tag in ${esc(s.nome)}: creane uno qui accanto.</span>`;
  return `<section class="a-section" data-svc="${key}">
    ${head}
    <p class="a-note">${intro}</p>
    <div class="a-field">
      <span class="ticket-label">Tag</span>
      <div class="a-tags">${tags}</div>
      <form class="a-newtag" data-svc="${key}">
        <input type="text" placeholder="Nuovo tag" aria-label="Nuovo tag per ${esc(s.nome)}" maxlength="40" autocomplete="off" spellcheck="false">
        <button type="submit" class="a-link">＋ Crea</button>
      </form>
    </div>
    <div class="a-grid">
      <label class="a-field"><span class="ticket-label">Profilo di qualità</span>
        <select data-svc="${key}" data-k="profilo_qualita">${optionList(s.opzioni.profili_qualita, v.profilo_qualita, s.predefiniti.profilo_qualita)}</select></label>
      <label class="a-field"><span class="ticket-label">Cartella</span>
        <select data-svc="${key}" data-k="cartella">${optionList(s.opzioni.cartelle, v.cartella, s.predefiniti.cartella)}</select></label>
    </div>
  </section>`;
}

function renderAdmin() {
  const c = admin.conf;
  $("#a-services").innerHTML = ["sonarr", "radarr"].map((k) => serviceHtml(k, c[k])).join("");
  const anyEditable = ["sonarr", "radarr"].some((k) => c[k].configurato && c[k].opzioni);
  $("#a-save").closest(".a-save").hidden = !anyEditable;
  $("#a-pinchange").hidden = c.pin_da_env;
}

function omdbHtml(o) {
  const pill = !o.configurata ? "Non collegato" : o.errore ? "Errore" : "Collegato";
  const where = o.da_env ? `La chiave arriva da <code>OMDB_API_KEY</code>; se ne inserisci una qui, vale questa.` : "";
  return `<div class="a-sec-head"><h3>Rotten Tomatoes</h3><span class="a-pill ${o.configurata && !o.errore ? "on" : ""}">${pill}</span></div>
    <p class="a-note">Serve per ordinare “I migliori” con il voto della critica di Rotten Tomatoes. I voti arrivano da OMDb:
      chiedi una chiave gratuita su <a href="https://www.omdbapi.com/apikey.aspx" target="_blank" rel="noopener">omdbapi.com</a>
      (piano FREE, 1.000 richieste al giorno) e attivala dal link nella mail. ${where}</p>
    ${o.errore ? `<p class="a-note err">OMDb: ${esc(o.errore)}</p>` : ""}
    ${o.configurata ? `<p class="a-note">Chiave attuale: <code>${esc(o.chiave)}</code> · richieste usate oggi: ${o.usate_oggi} su ${o.limite}</p>` : ""}
    <form class="a-newtag" id="a-omdb-form">
      <input type="text" id="a-omdb-key" placeholder="${o.configurata ? "Nuova chiave OMDb" : "Chiave OMDb"}" aria-label="Chiave OMDb" maxlength="40" autocomplete="off" spellcheck="false">
      <button type="submit" class="a-link">Salva chiave</button>
      ${o.configurata && !o.da_env ? `<button type="button" class="a-link" id="a-omdb-remove">Rimuovi</button>` : ""}
    </form>
    <p class="a-msg" id="a-omdb-msg" role="status"></p>`;
}

async function loadOmdb() {
  try {
    $("#a-omdb").innerHTML = omdbHtml(await adminFetch("/api/admin/omdb"));
  } catch (err) {
    if (err.status === 401) { setAdminToken(null); return showLock(true); }
    $("#a-omdb").innerHTML = `<p class="a-note err">${esc(err.message)}</p>`;
  }
}

async function saveOmdb(chiave) {
  adminMsg("#a-omdb-msg", chiave ? "Verifico la chiave…" : "");
  try {
    const o = await adminFetch("/api/admin/omdb", { method: "PUT", body: JSON.stringify({ chiave }) });
    $("#a-omdb").innerHTML = omdbHtml(o);
    adminMsg("#a-omdb-msg", chiave
      ? "Chiave salvata. Sto ricalcolando “I migliori” con i voti di Rotten Tomatoes: ci vuole qualche minuto."
      : "Chiave rimossa.", false);
    if (state.data) state.data.rotten_tomatoes = o.configurata;
  } catch (err) {
    if (err.status === 401) { setAdminToken(null); return showLock(true); }
    adminMsg("#a-omdb-msg", err.message, true);
  }
}

$("#a-omdb").addEventListener("submit", (e) => {
  e.preventDefault();
  const key = $("#a-omdb-key").value.trim();
  if (!key) return $("#a-omdb-key").focus();
  saveOmdb(key);
});
$("#a-omdb").addEventListener("click", (e) => {
  if (e.target.id === "a-omdb-remove") saveOmdb("");
});


// Suggerimenti con l'AI (Claude)
function aiHtml(o) {
  const pill = !o.configurata ? "Non collegato" : o.errore ? "Errore" : "Collegato";
  const where = o.da_env ? `La chiave arriva da <code>ANTHROPIC_API_KEY</code>; se ne inserisci una qui, vale questa.` : "";
  const models = o.modelli.some((m) => m.id === o.modello) ? o.modelli : [...o.modelli, { id: o.modello, nome: o.modello }];
  return `<div class="a-sec-head"><h3>Suggeriti per te · AI</h3><span class="a-pill ${o.configurata && !o.errore ? "on" : ""}">${pill}</span></div>
    <p class="a-note">Con una chiave API di Anthropic i suggerimenti li sceglie Claude, leggendo i tuoi mi piace, i voti e le note
      (e i titoli già visti su Yamtrack), tra film e serie degli ultimi 50 anni. Crea la chiave su
      <a href="https://platform.claude.com/settings/keys" target="_blank" rel="noopener">platform.claude.com</a> (servono crediti prepagati:
      ogni aggiornamento dei suggerimenti costa pochi centesimi). Senza chiave si usano i titoli consigliati da TMDB. ${where}</p>
    ${o.errore ? `<p class="a-note err">${esc(o.errore)}</p>` : ""}
    ${o.configurata ? `<p class="a-note">Chiave attuale: <code>${esc(o.chiave)}</code></p>` : ""}
    <div class="a-grid">
      <label class="a-field"><span class="ticket-label">Modello</span>
        <select id="a-ai-model">${models.map((m) => `<option value="${esc(m.id)}"${m.id === o.modello ? " selected" : ""}>${esc(m.nome)}</option>`).join("")}</select></label>
    </div>
    <form class="a-newtag a-wide" id="a-ai-form">
      <input type="password" id="a-ai-key" placeholder="${o.configurata ? "Nuova chiave (lascia vuoto per tenere quella attuale)" : "Chiave API (sk-ant-…)"}" aria-label="Chiave API di Anthropic" autocomplete="off" spellcheck="false">
      <button type="submit" class="a-link">Salva</button>
      ${o.configurata && !o.da_env ? `<button type="button" class="a-link" id="a-ai-remove">Rimuovi chiave</button>` : ""}
    </form>
    <p class="a-msg" id="a-ai-msg" role="status"></p>`;
}

async function loadAi() {
  try {
    $("#a-ai").innerHTML = aiHtml(await adminFetch("/api/admin/ai"));
  } catch (err) {
    if (err.status === 401) { setAdminToken(null); return showLock(true); }
    $("#a-ai").innerHTML = `<p class="a-note err">${esc(err.message)}</p>`;
  }
}

async function saveAi(body) {
  adminMsg("#a-ai-msg", "Verifico chiave e modello…");
  try {
    const o = await adminFetch("/api/admin/ai", { method: "PUT", body: JSON.stringify(body) });
    $("#a-ai").innerHTML = aiHtml(o);
    adminMsg("#a-ai-msg", body.chiave === "" ? "Chiave rimossa." : "Salvato. I prossimi suggerimenti li sceglie Claude.", false);
    state.servizi.ai = o.configurata;
    state.suggeriti = null;
    if (state.view === "per_te") loadSuggeriti();
  } catch (err) {
    if (err.status === 401) { setAdminToken(null); return showLock(true); }
    adminMsg("#a-ai-msg", err.message, true);
  }
}

$("#a-ai").addEventListener("submit", (e) => {
  e.preventDefault();
  const key = $("#a-ai-key").value.trim();
  saveAi({ modello: $("#a-ai-model").value, ...(key ? { chiave: key } : {}) });
});
$("#a-ai").addEventListener("click", (e) => {
  if (e.target.id === "a-ai-remove") saveAi({ chiave: "" });
});

// Yamtrack
function yamtrackHtml(y) {
  const pill = !y.configurato ? "Non collegato" : y.errore ? "Errore" : "Collegato";
  return `<div class="a-sec-head"><h3>Yamtrack</h3><span class="a-pill ${y.configurato && !y.errore ? "on" : ""}">${pill}</span></div>
    <p class="a-note">Collega il tuo Yamtrack per segnare i titoli che hai già visto, non suggerirteli di nuovo e usare i tuoi voti
      per capire meglio i tuoi gusti. CosaGuardo entra con il tuo utente e legge l'esportazione CSV (solo lettura, una volta l'ora).
      ${y.da_env ? "I dati arrivano da <code>YAMTRACK_URL</code>, <code>YAMTRACK_USER</code> e <code>YAMTRACK_PASSWORD</code>; se li inserisci qui, valgono questi." : ""}</p>
    ${y.errore ? `<p class="a-note err">${esc(y.errore)}</p>` : ""}
    ${y.configurato && y.titoli != null ? `<p class="a-note">Film e serie letti: ${y.titoli}</p>` : ""}
    <form id="a-yam-form">
      <div class="a-grid">
        <label class="a-field"><span class="ticket-label">Indirizzo</span>
          <input type="url" id="a-yam-url" value="${esc(y.url)}" placeholder="http://192.168.1.10:8000" autocomplete="off" spellcheck="false"></label>
        <label class="a-field"><span class="ticket-label">Utente</span>
          <input type="text" id="a-yam-user" value="${esc(y.utente)}" autocomplete="off" spellcheck="false"></label>
        <label class="a-field"><span class="ticket-label">Password</span>
          <input type="password" id="a-yam-pass" placeholder="${y.configurato ? "Invariata se vuota" : ""}" autocomplete="new-password"></label>
      </div>
      <div class="a-save">
        <button type="submit" class="btn-request">Collega e prova</button>
        ${y.configurato && !y.da_env ? `<button type="button" class="a-link" id="a-yam-remove">Scollega</button>` : ""}
        <p class="a-msg" id="a-yam-msg" role="status"></p>
      </div>
    </form>`;
}

async function loadYamtrack() {
  try {
    $("#a-yamtrack").innerHTML = yamtrackHtml(await adminFetch("/api/admin/yamtrack"));
  } catch (err) {
    if (err.status === 401) { setAdminToken(null); return showLock(true); }
    $("#a-yamtrack").innerHTML = `<p class="a-note err">${esc(err.message)}</p>`;
  }
}

async function saveYamtrack(body) {
  adminMsg("#a-yam-msg", body.url ? "Provo ad accedere a Yamtrack…" : "");
  try {
    const y = await adminFetch("/api/admin/yamtrack", { method: "PUT", body: JSON.stringify(body) });
    $("#a-yamtrack").innerHTML = yamtrackHtml(y);
    adminMsg("#a-yam-msg", y.configurato ? `Collegato: ${plural(y.titoli || 0, "titolo letto", "titoli letti")}.` : "Yamtrack scollegato.", false);
    state.servizi.yamtrack = y.configurato;
    state.visti = {};
    if (y.configurato) loadVisti(); else render();
  } catch (err) {
    if (err.status === 401) { setAdminToken(null); return showLock(true); }
    adminMsg("#a-yam-msg", err.message, true);
  }
}

$("#a-yamtrack").addEventListener("submit", (e) => {
  e.preventDefault();
  const url = $("#a-yam-url").value.trim();
  if (!url) return $("#a-yam-url").focus();
  saveYamtrack({ url, utente: $("#a-yam-user").value.trim(), password: $("#a-yam-pass").value || null });
});
$("#a-yamtrack").addEventListener("click", (e) => {
  if (e.target.id === "a-yam-remove") saveYamtrack({ url: "" });
});

async function showPanel() {
  $("#a-lock").hidden = true;
  $("#a-panel").hidden = false;
  $("#a-services").innerHTML = `<p class="a-note">Leggo le impostazioni di Sonarr e Radarr…</p>`;
  adminMsg("#a-save-msg", "");
  loadOmdb();
  loadAi();
  loadYamtrack();
  try {
    admin.conf = await adminFetch("/api/admin/impostazioni");
  } catch (err) {
    if (err.status === 401) { setAdminToken(null); return showLock(true); }
    $("#a-services").innerHTML = `<p class="a-note err">${esc(err.message)}</p>`;
    return;
  }
  admin.values = {};
  for (const k of ["sonarr", "radarr"]) admin.values[k] = structuredClone(admin.conf[k].valori);
  renderAdmin();
}

$("#a-services").addEventListener("click", (e) => {
  const b = e.target.closest(".a-tag");
  if (!b) return;
  const list = admin.values[b.dataset.svc].tag;
  const id = Number(b.dataset.tag);
  const i = list.indexOf(id);
  i >= 0 ? list.splice(i, 1) : list.push(id);
  b.classList.toggle("on", i < 0);
  b.setAttribute("aria-pressed", i < 0);
  adminMsg("#a-save-msg", "");
});

$("#a-services").addEventListener("change", (e) => {
  const sel = e.target.closest("select[data-svc]");
  if (!sel) return;
  admin.values[sel.dataset.svc][sel.dataset.k] = sel.value;
  adminMsg("#a-save-msg", "");
});

$("#a-services").addEventListener("submit", async (e) => {
  const form = e.target.closest(".a-newtag");
  if (!form) return;
  e.preventDefault();
  const input = form.querySelector("input");
  const nome = input.value.trim();
  if (!nome) return input.focus();
  const svc = form.dataset.svc;
  try {
    const t = await adminFetch("/api/admin/tag", { method: "POST", body: JSON.stringify({ servizio: svc, nome }) });
    const opts = admin.conf[svc].opzioni.tag;
    if (!opts.some((x) => x.id === t.id)) opts.push(t);
    if (!admin.values[svc].tag.includes(t.id)) admin.values[svc].tag.push(t.id);
    renderAdmin();
    adminMsg("#a-save-msg", `Tag "${t.nome}" pronto: salva per applicarlo.`, false);
  } catch (err) {
    if (err.status === 401) { setAdminToken(null); return showLock(true); }
    adminMsg("#a-save-msg", err.message, true);
  }
});

$("#a-save").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  const body = {};
  for (const k of ["sonarr", "radarr"]) if (admin.conf[k].configurato && admin.conf[k].opzioni) body[k] = admin.values[k];
  try {
    const saved = await adminFetch("/api/admin/impostazioni", { method: "PUT", body: JSON.stringify(body) });
    for (const k of Object.keys(body)) admin.conf[k].valori = saved[k];
    adminMsg("#a-save-msg", "Impostazioni salvate. Valgono per i prossimi titoli che richiedi.", false);
  } catch (err) {
    if (err.status === 401) { setAdminToken(null); return showLock(true); }
    adminMsg("#a-save-msg", err.message, true);
  } finally {
    btn.disabled = false;
  }
});

$("#a-pin-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await adminFetch("/api/admin/pin", {
      method: "PUT", body: JSON.stringify({ attuale: $("#a-pin-old").value.trim(), nuovo: $("#a-pin-new").value.trim() }),
    });
    e.currentTarget.reset();
    adminMsg("#a-pin-msg", "PIN cambiato.", false);
  } catch (err) {
    adminMsg("#a-pin-msg", err.message, true);
  }
});

$("#a-logout").addEventListener("click", async () => {
  await adminFetch("/api/admin/esci", { method: "POST" }).catch(() => {});
  setAdminToken(null);
  showLock(true);
});

$("#admin-open").addEventListener("click", openAdmin);
$("#admin").addEventListener("click", (e) => {
  if (e.target.matches("[data-close]") || e.target === e.currentTarget) e.currentTarget.close();
});

// ---------------------------------------------------------------- eventi

function setView(view) {
  state.view = view;
  if (view !== "richieste") localSet("view", view);
  document.body.classList.toggle("view-richieste", view === "richieste");
  document.querySelectorAll(".tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.view === view);
    t.setAttribute("aria-selected", t.dataset.view === view);
  });
}

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
  if (t.dataset.view === "richieste") state.requestsLoaded = false;
  setView(t.dataset.view);
  if (t.dataset.view === "per_te") loadSuggeriti();
  renderFilters();
  renderHero();
  render();
}));

$("#tipo").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  state.tipo = b.dataset.tipo;
  $("#tipo").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
  renderFilters();
  renderHero();
  render();
});

$("#search").addEventListener("input", (e) => { state.q = e.target.value.trim(); renderGenres(); renderChips(); render(); });

$("#genres").addEventListener("click", (e) => {
  const b = e.target.closest(".genre");
  if (!b) return;
  state.categoria = b.dataset.c === state.categoria ? "" : b.dataset.c;
  renderGenres();
  renderChips();
  renderHero();
  render();
});

$("#periods").addEventListener("click", (e) => {
  const b = e.target.closest(".period");
  if (!b) return;
  state.periodo = b.dataset.p;
  localSet("periodo", state.periodo);
  renderFilters();
  renderHero();
  render();
});

$("#platforms").addEventListener("click", (e) => {
  const b = e.target.closest(".chip");
  if (!b) return;
  const k = b.dataset.p;
  if (k === "all") state.platforms.clear();
  else state.platforms.has(k) ? state.platforms.delete(k) : state.platforms.add(k);
  localSet("platforms", JSON.stringify([...state.platforms]));
  renderGenres();
  renderChips();
  renderHero();
  render();
});

content.addEventListener("click", (e) => {
  const c = e.target.closest(".card[data-id]");
  if (c) openDetail(c.dataset.id);
});

// Inclinazione 3D della locandina sotto il mouse
if (matchMedia("(hover: hover) and (prefers-reduced-motion: no-preference)").matches) {
  content.addEventListener("pointermove", (e) => {
    const poster = e.target.closest(".poster");
    if (!poster) return;
    const card = poster.closest(".card");
    const r = poster.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width;
    const y = (e.clientY - r.top) / r.height;
    card.classList.add("tilting");
    card.style.setProperty("--ry", `${(x - 0.5) * 14}deg`);
    card.style.setProperty("--rx", `${(0.5 - y) * 12}deg`);
    card.style.setProperty("--mx", `${x * 100}%`);
    card.style.setProperty("--my", `${y * 100}%`);
  });
  content.addEventListener("pointerout", (e) => {
    const card = e.target.closest(".card");
    if (!card || card.contains(e.relatedTarget)) return;
    card.classList.remove("tilting");
    card.style.setProperty("--rx", "0deg");
    card.style.setProperty("--ry", "0deg");
  });
}

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
$("#detail").addEventListener("close", stopTrailer);
$("#d-request").addEventListener("click", () => {
  const i = findItem($("#detail").dataset.id);
  if (i?.type === "tv") openSeasons();
  else sendRequest();
});
$("#d-more").addEventListener("click", openSeasons);

setView(state.view);
loadServizi();
loadGusti().then(() => { if (state.view === "per_te") loadSuggeriti(); });
skeleton();
load().catch(() => { $("#error").hidden = false; $("#error").textContent = "Impossibile contattare il server."; });
setInterval(poll, POLL_MS);
