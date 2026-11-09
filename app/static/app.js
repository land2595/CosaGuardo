const VIEWS = ["novita", "prossimamente", "migliori", "per_te", "tendenze", "bambini"];
const savedView = localGet("view") === "da_non_perdere" ? "migliori" : localGet("view");
const state = {
  view: VIEWS.includes(savedView) ? savedView : "novita",
  tipo: "all",
  categoria: "",
  periodo: localGet("periodo") || "n5",
  bestKids: localGet("bestKids") === "1",
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
  liste: { watchlist: new Map(), ignorati: new Map() },
  lista: ["watchlist", "richieste", "ignorati"].includes(localGet("lista")) ? localGet("lista") : "watchlist",
  visti: {},
  suggeriti: null,
  extra: new Map(),
  person: null,
  searchRes: null,
  memOpen: false,
  memEdit: false,
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
    !state.liste.ignorati.has(i.id) &&
    (state.tipo === "all" || i.type === state.tipo) &&
    (skip === "platforms" || !state.platforms.size || (i.platforms || []).some((p) => state.platforms.has(p))) &&
    (skip === "categoria" || !state.categoria || (i.categorie || []).includes(state.categoria)) &&
    (view !== "migliori" || inPeriod(i)) &&
    (view !== "migliori" || skip === "kids" || state.bestKids || !i.bambini) &&
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
    state.liste.watchlist.has(i.id) && state.view !== "richieste" ? `<span class="on-watch" title="Nella tua watchlist">Da vedere</span>` : "",
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

function fmtScore(n) { return Number(n).toLocaleString("it-IT", { maximumFractionDigits: 1 }); }

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
  if (!v) return "";
  const from = (v.fonti || []).map((f) => VISTI_NAMES[f]).join(" e ");
  return `<span class="seen ${v.stato === "Completed" ? "done" : ""}" title="${esc(from ? `Da ${from}` : "")}">${esc(SEEN_LABELS[v.stato] || "Visto")}</span>`;
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
  renderView();
  renderSearchMore();
}

function renderView() {
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
  const kids = filtered("migliori", "kids").filter((i) => i.bambini).length;
  el.innerHTML = `<span class="periods-label">Periodo</span>` + opts.map((o) =>
    `<button class="period ${o.id === state.periodo ? "on" : ""} ${o.id.startsWith("n") ? "span" : ""}" data-p="${o.id}" aria-pressed="${o.id === state.periodo}">${esc(o.label)}</button>`).join("") +
    `<button class="period kids ${state.bestKids ? "on" : ""}" data-kids aria-pressed="${state.bestKids}" title="Film d'animazione e per famiglie, serie per bambini">` +
    `${state.bestKids ? "✓ Con" : "＋ Includi"} contenuti per bambini${kids ? ` <small>${kids}</small>` : ""}</button>`;
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
    [...state.gusti.values()], [...state.liste.watchlist.values()], [...state.liste.ignorati.values()], state.requests,
    [...state.extra.values()]];
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
    if (state.liste.watchlist.delete(id)) renderListButtons(item);
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
  if (state.servizi.plex) loadPlex();
  if ($("#detail").open) $("#d-note-ai").hidden = !state.servizi.ai;
  if (state.servizi.visti) loadVisti();
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
  return `<div class="tile" style="--i:${Math.min(n, 18)}"><button class="card req-card" data-id="${esc(r.id)}">
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
  </button>
  <div class="tile-actions">
    <button type="button" class="tile-btn danger" data-act="cancella" data-id="${esc(r.id)}"
      title="Lo toglie solo da questa schermata: su Plex, Radarr e Sonarr resta tutto com'è">🗑 Cancella</button>
  </div></div>`;
}

// Locandina della watchlist o degli ignorati, con i pulsanti sotto
function listTileHtml(i, n, lista) {
  const svc = i.type === "movie" ? state.servizi.radarr : state.servizi.sonarr;
  const acts = lista === "watchlist"
    ? (svc ? `<button type="button" class="tile-btn gold" data-act="richiedi" data-id="${esc(i.id)}">Richiedi</button>` : "") +
      `<button type="button" class="tile-btn" data-act="togli" data-id="${esc(i.id)}" title="Togli dalla watchlist">✕ Togli</button>`
    : `<button type="button" class="tile-btn" data-act="ripristina" data-id="${esc(i.id)}" title="Torna a vederlo nelle sezioni">↺ Ripristina</button>`;
  const html = cardHtml(i, n).replace(/ style="--i:\d+"/, "");
  return `<div class="tile ${lista === "ignorati" ? "dim" : ""}" style="--i:${Math.min(n, 18)}">${html}<div class="tile-actions">${acts}</div></div>`;
}

let requestsTimer = null;

function listaFilter(list) {
  const q = state.q.toLowerCase();
  return list.filter((r) => (state.tipo === "all" || r.type === state.tipo) &&
    (!q || (r.title || "").toLowerCase().includes(q) || (r.original_title || "").toLowerCase().includes(q)));
}

const LISTA_EMPTY = {
  watchlist: ["La watchlist è vuota", "Apri un film o una serie e premi <b>＋ Watchlist</b> per metterlo da parte senza richiederlo subito."],
  richieste: ["Nessuna richiesta, per ora", "Apri un titolo e premi “Richiedi”: i film vanno a Radarr, le serie a Sonarr."],
  ignorati: ["Nessun titolo ignorato", "Premi <b>⊘ Non mi interessa</b> nella scheda di un titolo per non vederlo più nelle sezioni e nei suggerimenti."],
};

async function renderRequests(silent = false) {
  const arr = state.servizi.radarr || state.servizi.sonarr;
  if (!state.requestsLoaded && (arr || state.requests.length || !silent)) {
    if (!silent && state.lista === "richieste") content.innerHTML = `<section class="block"><div class="grid">${'<div class="card skeleton"><div class="poster"></div><h3></h3><span class="meta"></span></div>'.repeat(6)}</div></section>`;
    try {
      const d = await (await fetch("/api/richieste")).json();
      state.requests = d.richieste || [];
      state.requestsLoaded = true;
    } catch {
      if (state.lista === "richieste") {
        content.innerHTML = `<div class="req-empty"><h3>Richieste non disponibili</h3><p>Impossibile contattare il server.</p></div>`;
        return;
      }
    }
  }
  if (state.view !== "richieste") return;
  $("#empty").hidden = true;
  const showReq = arr || state.requests.length;
  if (!showReq && state.lista === "richieste") state.lista = "watchlist";
  const tabs = [
    ["watchlist", "Da vedere", state.liste.watchlist.size],
    showReq ? ["richieste", "Richieste", state.requests.length] : null,
    ["ignorati", "Ignorati", state.liste.ignorati.size],
  ].filter(Boolean);
  const seg = `<div class="list-bar"><div class="seg list-seg" role="tablist">${tabs.map(([k, label, n]) =>
    `<button type="button" data-lista="${k}" class="${state.lista === k ? "active" : ""}" role="tab" aria-selected="${state.lista === k}">${label} <span class="n">${n}</span></button>`).join("")}</div></div>`;

  let body = "";
  const lista = state.lista;
  const all = lista === "richieste" ? state.requests : [...state.liste[lista].values()];
  const list = listaFilter(all);
  let active = 0;
  if (lista === "richieste") {
    active = list.filter((r) => r.download).length;
    body = list.length ? `<section class="block">
        <div class="block-head"><h2>Le mie richieste</h2><span class="count">${plural(list.length, "titolo", "titoli")}${active ? ` · ${active} in download` : ""}</span></div>
        <p class="note">“Cancella” toglie il titolo solo da questa schermata: su Plex, Radarr e Sonarr resta tutto com'è.</p>
        <div class="grid">${list.map(requestCardHtml).join("")}</div>
      </section>` : "";
  } else if (list.length) {
    const title = lista === "watchlist" ? "Da vedere" : "Non mi interessano";
    const extra = lista === "ignorati" ? `<p class="note">Questi titoli non compaiono nelle sezioni e non ti vengono suggeriti.</p>` : "";
    body = `<section class="block">
        <div class="block-head"><h2>${title}</h2><span class="count">${plural(list.length, "titolo", "titoli")}</span></div>${extra}
        <div class="grid">${list.map((i, n) => listTileHtml(i, n, lista)).join("")}</div>
      </section>`;
  }
  if (!body) {
    const [h, p] = all.length
      ? ["Nessun titolo con questi filtri", "Prova a scegliere “Tutti” o a cambiare la ricerca."]
      : LISTA_EMPTY[lista];
    body = `<div class="req-empty"><h3>${h}</h3><p>${p}</p></div>`;
  }
  content.innerHTML = seg + body;
  if (silent) content.querySelectorAll(".card, .tile").forEach((c) => { c.style.animation = "none"; });
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

// --------------------------------------------------- watchlist e ignorati

async function loadListe() {
  try {
    const d = await (await fetch("/api/liste")).json();
    state.liste = { watchlist: new Map(d.watchlist.map((v) => [v.id, v])), ignorati: new Map(d.ignorati.map((v) => [v.id, v])) };
    rerender();
  } catch { /* ignora */ }
}

function rerender() {
  if (!state.data) return;
  if (state.view === "richieste") { renderRequests(true); return; }
  renderFilters();
  renderHero();
  render();
}

// Aggiunge (on=true) o toglie un titolo da una lista
async function setLista(lista, i, on) {
  const url = `/api/liste/${lista}/${encodeURIComponent(i.id)}`;
  const r = await fetch(url, on
    ? { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scheda: scheda(i) }) }
    : { method: "DELETE" });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Non sono riuscito a salvare.");
  for (const k of ["watchlist", "ignorati"]) state.liste[k].delete(i.id);
  if (on) state.liste[lista].set(i.id, body.voce);
  rerender();
}

function renderListButtons(i) {
  const w = $("#d-watch");
  const inW = state.liste.watchlist.has(i.id);
  w.classList.toggle("on", inW);
  w.textContent = inW ? "✓ In watchlist" : "＋ Watchlist";
  w.title = inW ? "Togli dalla watchlist" : "Mettilo tra le cose da vedere, senza richiederlo subito";
  const x = $("#d-ignore");
  const ign = state.liste.ignorati.has(i.id);
  x.classList.toggle("on", ign);
  x.textContent = ign ? "↺ Ignorato · ripristina" : "⊘ Non mi interessa";
  x.title = ign ? "Torna a vederlo nelle sezioni e nei suggerimenti" : "Non lo vedrai più nelle sezioni e nei suggerimenti";
}

async function toggleLista(lista) {
  const id = $("#detail").dataset.id;
  const i = findItem(id);
  if (!i) return;
  const on = !state.liste[lista].has(id);
  try {
    await setLista(lista, i, on);
    if ($("#detail").dataset.id !== id) return;
    renderListButtons(i);
    requestMsg(lista === "watchlist"
      ? (on ? "Aggiunto alla watchlist: lo trovi in “Le mie liste”." : "Tolto dalla watchlist.")
      : (on ? "Non lo vedrai più nelle sezioni e nei suggerimenti. Lo ritrovi in “Le mie liste” → Ignorati." : "Ripristinato."));
  } catch (err) {
    requestMsg(err.message, true);
  }
}

$("#d-watch").addEventListener("click", () => toggleLista("watchlist"));
$("#d-ignore").addEventListener("click", () => toggleLista("ignorati"));

async function deleteRequest(id) {
  const r = await fetch(`/api/richieste/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!r.ok && r.status !== 404) throw new Error("Non sono riuscito a cancellarla.");
  state.requests = state.requests.filter((x) => x.id !== id);
  renderRequests(true);
}

content.addEventListener("click", async (e) => {
  const seg = e.target.closest(".list-seg button[data-lista]");
  if (seg) {
    state.lista = seg.dataset.lista;
    localSet("lista", state.lista);
    renderRequests();
    return;
  }
  const b = e.target.closest(".tile-btn[data-act]");
  if (!b) return;
  const id = b.dataset.id;
  const act = b.dataset.act;
  try {
    if (act === "cancella") {
      // Due tocchi: il primo chiede conferma
      if (!b.classList.contains("confirm")) {
        b.classList.add("confirm");
        b.textContent = "Sicuro? Tocca ancora";
        setTimeout(() => { if (b.isConnected) { b.classList.remove("confirm"); b.textContent = "🗑 Cancella"; } }, 3500);
        return;
      }
      b.disabled = true;
      await deleteRequest(id);
    } else if (act === "togli" || act === "ripristina") {
      b.disabled = true;
      await setLista(act === "togli" ? "watchlist" : "ignorati", findItem(id), false);
    } else if (act === "richiedi") {
      openDetail(id);
      const i = findItem(id);
      if (i?.type === "tv") openSeasons();
      else sendRequest();
    }
  } catch (err) {
    b.disabled = false;
    b.textContent = err.message;
  }
});

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
  renderListButtons(i);
  $("#d-note-ai").hidden = !state.servizi.ai;
  $("#d-note-ai").disabled = false;
  $("#d-note-ai").textContent = "✦ Suggerisci con l'AI";
  $("#d-note-sugg").hidden = true;
  $("#d-note-sugg").innerHTML = "";
  $("#d-hero").style.backgroundImage = i.backdrop ? `url("${i.backdrop.replace("/w780/", "/w1280/")}")` : "";
  const poster = $("#d-poster");
  poster.hidden = !i.poster;
  if (i.poster) poster.src = i.poster;
  $("#d-badge").textContent = i.type === "movie" ? "Film" : i.badge || "Serie TV";
  $("#d-title").textContent = i.title;
  renderGenresDetail(i.genres);
  loadCast(i);
  const back = $("#d-back");
  back.hidden = !state.person?.back;
  if (state.person?.back) back.textContent = `← Torna a ${state.person.nome}`;
  if (state.person) state.person.back = false;
  const orig = i.original_title || "";
  $("#d-orig").textContent = orig.toLowerCase() !== (i.title || "").toLowerCase() ? orig : "";
  $("#d-meta").textContent = [
    i.date ? `Uscita ${fmtLong.format(toDate(i.date))}` : null,
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
  if (!dlg.open) dlg.showModal();
  dlg.scrollTop = 0;
}

// ------------------------------------------- generi e attori nella scheda

function renderGenresDetail(genres) {
  $("#d-genres").innerHTML = (genres || []).map((g) => `<span class="d-genre">${esc(g)}</span>`).join("");
}

const schedaCache = new Map();

function initials(name) {
  return (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
}

function actorHtml(c) {
  const face = c.foto
    ? `<img class="face" src="${esc(c.foto)}" alt="" loading="lazy">`
    : `<span class="face none" aria-hidden="true">${esc(initials(c.nome))}</span>`;
  const role = [c.personaggio, c.episodi ? `${c.episodi} ep.` : ""].filter(Boolean).join(" · ");
  return `<button type="button" class="actor" data-person="${c.id}" title="Film e serie con ${esc(c.nome)}">
    ${face}<b>${esc(c.nome)}</b>${role ? `<small>${esc(role)}</small>` : ""}</button>`;
}

async function loadCast(i) {
  const box = $("#d-cast");
  const row = $("#d-cast-row");
  box.hidden = false;
  row.innerHTML = `<span class="cast-load">Carico gli attori…</span>`;
  const key = `${i.type}/${i.tmdb_id}`;
  if (!schedaCache.has(key)) {
    schedaCache.set(key, fetch(`/api/scheda/${key}`).then((r) => r.ok ? r.json() : null).catch(() => null));
  }
  const sc = await schedaCache.get(key);
  if (!sc) schedaCache.delete(key);
  if ($("#detail").dataset.id !== i.id) return;
  if (sc && !(i.genres || []).length && sc.generi?.length) renderGenresDetail(sc.generi);
  box.hidden = !sc?.cast?.length;
  row.innerHTML = (sc?.cast || []).map(actorHtml).join("");
}

// ------------------------------------------------- filmografia attore

const personCache = new Map();

function personTileHtml(t) {
  const poster = t.poster
    ? `<img src="${esc(t.poster)}" alt="" loading="lazy">`
    : `<div class="noimg">${esc(t.title)}</div>`;
  const role = [t.ruolo, t.episodi ? `${t.episodi} episod${t.episodi === 1 ? "io" : "i"}` : ""].filter(Boolean).join(" · ");
  return `<button type="button" class="credit" data-kind="${t.type}" data-tmdb="${t.tmdb_id}">
    <div class="cp">${poster}
      <span class="kind ${t.type}">${t.type === "movie" ? "Film" : "Serie"}</span>
      ${state.plex[t.type]?.has(t.tmdb_id) ? `<span class="on-plex">Su Plex</span>` : ""}
    </div>
    <b>${esc(t.title)}</b>
    <small>${esc(t.year || "In arrivo")}${role ? ` · ${esc(role)}` : ""}</small>
  </button>`;
}

function renderPersonGrid() {
  const p = state.person;
  if (!p?.titoli) return;
  const counts = { all: p.titoli.length, movie: 0, tv: 0 };
  for (const t of p.titoli) counts[t.type]++;
  const tabs = [["all", "Tutto"], ["movie", "Film"], ["tv", "Serie TV"]].filter(([k]) => k === "all" || counts[k]);
  $("#p-filter").innerHTML = tabs.map(([k, label]) =>
    `<button class="genre ${p.filtro === k ? "on" : ""}" data-f="${k}" aria-pressed="${p.filtro === k}">${label}<small>${counts[k]}</small></button>`).join("");
  const list = p.titoli.filter((t) => p.filtro === "all" || t.type === p.filtro);
  $("#p-grid").innerHTML = list.length ? list.map(personTileHtml).join("")
    : `<p class="cast-load">Nessun titolo trovato.</p>`;
}

async function openPerson(id) {
  const dlg = $("#person");
  dlg.dataset.id = id;
  $("#p-name").textContent = "";
  $("#p-meta").textContent = "";
  $("#p-bio").textContent = "";
  $("#p-bio-more").hidden = true;
  $("#p-links").innerHTML = "";
  $("#p-photo").hidden = true;
  $("#p-filter").innerHTML = "";
  $("#p-grid").innerHTML = `<p class="cast-load">Carico i film e le serie…</p>`;
  if (!dlg.open) dlg.showModal();
  dlg.scrollTop = 0;
  if (!personCache.has(id)) {
    personCache.set(id, fetch(`/api/persona/${id}`).then((r) => r.ok ? r.json() : null).catch(() => null));
  }
  const p = await personCache.get(id);
  if (dlg.dataset.id !== String(id)) return;
  if (!p) {
    personCache.delete(id);
    $("#p-grid").innerHTML = `<p class="cast-load">Non riesco a caricare questa persona. Riprova tra poco.</p>`;
    return;
  }
  state.person = { ...p, filtro: "all", back: false };
  $("#p-role").textContent = p.mestiere === "regia" ? "Regia e sceneggiatura"
    : p.sesso === 1 ? "Attrice" : p.sesso === 2 ? "Attore" : "Interprete";
  $("#p-name").textContent = p.nome;
  const age = (from, to) => {
    const a = toDate(from), b = to ? toDate(to) : new Date();
    return b.getFullYear() - a.getFullYear() - (b < new Date(b.getFullYear(), a.getMonth(), a.getDate()) ? 1 : 0);
  };
  $("#p-meta").textContent = [
    p.nascita ? `Nascita: ${fmtLong.format(toDate(p.nascita))}${p.morte ? "" : ` (${age(p.nascita)} anni)`}` : null,
    p.luogo || null,
    p.morte ? `Morte: ${fmtLong.format(toDate(p.morte))} (${age(p.nascita || p.morte, p.morte)} anni)` : null,
  ].filter(Boolean).join("  ·  ");
  const bio = $("#p-bio");
  bio.textContent = p.biografia || "";
  bio.classList.add("short");
  $("#p-bio-more").hidden = (p.biografia || "").length < 320;
  $("#p-photo").hidden = !p.foto;
  if (p.foto) $("#p-photo").src = p.foto;
  $("#p-links").innerHTML =
    (p.imdb_id ? `<a href="https://www.imdb.com/name/${esc(p.imdb_id)}/" target="_blank" rel="noopener">Scheda su IMDb</a>` : "") +
    `<a href="${esc(p.tmdb_url)}" target="_blank" rel="noopener">Scheda su TMDB</a>`;
  renderPersonGrid();
}

async function openTitle(kind, tmdbId) {
  const id = `${kind}-${tmdbId}`;
  let i = findItem(id);
  if (!i) {
    i = await fetch(`/api/titolo/${kind}/${tmdbId}`).then((r) => r.ok ? r.json() : null).catch(() => null);
    if (!i) return;
    state.extra.set(i.id, i);
  }
  if (state.person) state.person.back = true;
  $("#person").close();
  openDetail(i.id);
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
    const from = (seen.fonti || []).map((f) => VISTI_NAMES[f]).join(" e ");
    $("#d-seen").textContent = `${from ? `${from}: ` : ""}${SEEN_LABELS[seen.stato] || seen.stato}${seen.voto != null ? ` · voto ${fmtScore(seen.voto)}/10` : ""}`;
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
    if (state.suggeriti?.generato) state.suggeriti.da_aggiornare = true;
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

// Note proposte dall'AI: una richiesta solo quando premi il pulsante
$("#d-note-ai").addEventListener("click", async () => {
  const id = $("#detail").dataset.id;
  const i = findItem(id);
  const b = $("#d-note-ai");
  if (!i) return;
  b.disabled = true;
  b.textContent = "L'AI sta pensando…";
  tasteMsg("");
  try {
    const r = await fetch(`/api/gusti/${encodeURIComponent(id)}/note-ai`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scheda: scheda(i) }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Suggerimenti non riusciti.");
    if ($("#detail").dataset.id !== id) return;
    const box = $("#d-note-sugg");
    box.innerHTML = `<span class="note-sugg-label">Tocca per aggiungerla alle note</span>` +
      body.note.map((n) => `<button type="button" class="note-chip">${esc(n)}</button>`).join("");
    box.hidden = false;
  } catch (err) {
    if ($("#detail").dataset.id === id) tasteMsg(err.message, true);
  } finally {
    if ($("#detail").dataset.id === id) {
      b.disabled = false;
      b.textContent = "✦ Altri suggerimenti";
    }
  }
});
$("#d-note-sugg").addEventListener("click", (e) => {
  const chip = e.target.closest(".note-chip");
  if (!chip) return;
  const note = $("#d-note");
  const cur = note.value.trim();
  note.value = cur ? `${cur}${/[.!?…]$/.test(cur) ? "" : "."} ${chip.textContent}` : chip.textContent;
  chip.remove();
  if (!$("#d-note-sugg").querySelector(".note-chip")) $("#d-note-sugg").hidden = true;
  saveNote();
});

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
  const by = sg.fonte === "ai" ? `Scelti da ${sg.modello || "Claude"}` : "Consigliati da TMDB";
  const canAsk = sg.gusti || (sg.ai && sg.visti?.length);
  // Con l'AI i suggerimenti partono solo dal pulsante: una richiesta per pressione
  const firstAi = sg.ai && sg.fonte !== "ai";
  const status = sg.in_corso
    ? `<span class="dot busy"></span> Selezione in corso…`
    : sg.generato ? `${esc(by)} · ${esc(when)}` : "";
  const label = firstAi ? "✦ Genera suggerimenti" : sg.da_aggiornare ? "↻ Aggiorna suggerimenti" : "↻ Nuovi suggerimenti";
  const btn = !sg.in_corso && canAsk
    ? `<button class="btn-request ${sg.da_aggiornare || firstAi ? "hot" : ""}" id="sugg-refresh" type="button">${label}</button>` : "";
  const hint = !sg.ai
    ? `<p class="note">Suggerimenti basati sui consigliati di TMDB. Per una selezione personalizzata configura un servizio AI nelle impostazioni.</p>` : "";
  return `${hint}<div class="for-you-bar"><span class="for-you-status">${status}</span>${btn}</div>
    ${sg.errore ? `<p class="note err">${esc(sg.errore)}</p>` : ""}${memoryHtml(sg)}`;
}

// La memoria dei gusti che l'AI riscrive a ogni aggiornamento (e che si puo' correggere)
function memoryHtml(sg) {
  const m = sg.profilo;
  if (!sg.ai || !m) return "";
  const when = m.aggiornato
    ? new Date(m.aggiornato).toLocaleString("it-IT", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
    : "";
  const meta = m.testo ? `${m.autore === "utente" ? "Modificato da te" : "Aggiornato dall'AI"} · ${when}` : "Da creare";
  let body;
  if (state.memEdit) {
    body = `<textarea id="mem-text" rows="9" maxlength="3000" spellcheck="true" aria-label="Profilo dei gusti"
        placeholder="Ama: …&#10;Non ama: …&#10;Autori e interpreti: …&#10;Toni e temi: …&#10;Da esplorare: …">${esc(m.testo)}</textarea>
      <div class="mem-actions"><button type="button" class="btn-request" id="mem-save">Salva</button>
        <button type="button" class="a-link" id="mem-cancel">Annulla</button><span class="taste-msg" id="mem-msg"></span></div>`;
  } else {
    const rows = m.testo.split("\n").filter((l) => l.trim()).map((l) => {
      const k = l.match(/^([^:]{2,32}):\s*(.*)$/);
      return k ? `<div class="mem-row"><dt>${esc(k[1])}</dt><dd>${esc(k[2])}</dd></div>` : `<div class="mem-row"><dd>${esc(l)}</dd></div>`;
    }).join("");
    body = `${rows ? `<dl class="mem-list">${rows}</dl>` : `<p class="mem-empty">Il profilo viene creato alla prima generazione dei suggerimenti.</p>`}
      <div class="mem-actions">
        <button type="button" class="a-link" id="mem-edit" ${sg.in_corso ? "disabled" : ""}>${m.testo ? "Modifica" : "Scrivi"}</button>
        ${m.suggeriti ? `<span class="mem-count">${plural(m.suggeriti, "titolo già suggerito", "titoli già suggeriti")}</span>
          <button type="button" class="a-link" id="mem-reset">Azzera</button>` : ""}
      </div>`;
  }
  return `<details class="memory" id="memory" ${state.memOpen || state.memEdit ? "open" : ""}>
    <summary><span class="mem-title">Profilo dei gusti</span><span class="mem-meta">${esc(meta)}</span></summary>
    <div class="mem-body">${body}</div></details>`;
}

async function saveMemory(testo) {
  const r = await fetch("/api/profilo", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ testo }) });
  if (!r.ok) throw new Error("Salvataggio non riuscito.");
  return r.json();
}

content.addEventListener("toggle", (e) => {
  if (e.target.id === "memory") state.memOpen = e.target.open;
}, true);

content.addEventListener("click", async (e) => {
  const sg = state.suggeriti;
  if (!sg) return;
  const id = e.target.closest("button")?.id;
  if (id === "mem-edit") { state.memEdit = true; render(); $("#mem-text")?.focus(); }
  if (id === "mem-cancel") { state.memEdit = false; render(); }
  if (id === "mem-save") {
    try {
      sg.profilo = await saveMemory($("#mem-text").value);
      if (sg.generato) sg.da_aggiornare = true;
      state.memEdit = false;
      render();
    } catch (err) {
      $("#mem-msg").textContent = err.message;
      $("#mem-msg").classList.add("err");
    }
  }
  if (id === "mem-reset") {
    const b = e.target.closest("button");
    if (!b.dataset.armed) { b.dataset.armed = "1"; b.textContent = "Conferma"; return; }
    const r = await fetch("/api/profilo/storico", { method: "DELETE" });
    if (r.ok) { sg.profilo = await r.json(); render(); }
  }
});

function renderForYou(items) {
  const sg = state.suggeriti;
  $("#empty").hidden = true;
  if (!sg) {
    skeleton();
    return;
  }
  const mine = filtered("preferiti").sort((a, b) => (b.voto ?? -1) - (a.voto ?? -1) || (b.mi_piace ? 1 : 0) - (a.mi_piace ? 1 : 0));
  if (!sg.gusti && !(sg.ai && sg.visti?.length) && !items.length) {
    content.innerHTML = `<div class="req-empty"><h3>Nessun giudizio</h3>
      <p>Valuta film e serie con <b>♥ Mi piace</b>, le stelle e le note. Con la ricerca trovi qualsiasi titolo, anche dei decenni passati.</p></div>`;
    return;
  }
  const sugg = items.length
    ? blockHtml("Suggeriti per te", items)
    : sg.in_corso ? `<section class="block"><div class="grid">${'<div class="card skeleton"><div class="poster"></div><h3></h3><span class="meta"></span></div>'.repeat(6)}</div></section>`
      : sg.titoli?.length ? `<div class="req-empty"><h3>Nessun suggerimento con questi filtri</h3><p>Prova a scegliere “Tutti” o un'altra piattaforma.</p></div>` : "";
  content.innerHTML = forYouBarHtml(sg) + sugg + (mine.length ? blockHtml("I tuoi giudizi", mine) : "");
}

// ------------------------------------------- ricerca di qualsiasi titolo

let searchTimer = null;

function scheduleSearch() {
  clearTimeout(searchTimer);
  const q = state.q;
  if (q.length < 2) { state.searchRes = null; return; }
  if (state.searchRes?.q === q) return;
  state.searchRes = { q, loading: true, titoli: [], persone: [] };
  searchTimer = setTimeout(async () => {
    let d = { titoli: [], persone: [], errore: true };
    try {
      const r = await fetch(`/api/cerca?q=${encodeURIComponent(q)}`);
      if (r.ok) d = await r.json();
    } catch { /* resta l'errore */ }
    if (state.q !== q) return;
    state.searchRes = { q, ...d, loading: false };
    renderSearchMore();
  }, 350);
}

function searchTileHtml(t) {
  const g = state.gusti.get(t.id);
  const mine = g ? `<span class="my-taste">${g.mi_piace ? "♥" : ""}${g.voto != null ? ` ★ ${g.voto}` : ""}</span>` : "";
  return personTileHtml({ ...t, ruolo: "" }).replace('<div class="cp">', `<div class="cp">${mine}`);
}

function personChipHtml(p) {
  const img = p.foto ? `<img src="${esc(p.foto)}" alt="" loading="lazy">` : `<span class="pc-ini">${esc(initials(p.nome))}</span>`;
  return `<button type="button" class="person-chip" data-person="${p.id}">${img}
    <span><b>${esc(p.nome)}</b><small>${esc(p.noto_per.join(", ") || (p.mestiere === "Acting" ? "Attore" : "Regia"))}</small></span></button>`;
}

function renderSearchMore() {
  content.querySelector("#search-more")?.remove();
  const r = state.searchRes;
  if (!r || r.q !== state.q || state.view === "richieste" || !state.data) return;
  const shown = new Set([...content.querySelectorAll(".card[data-id]")].map((c) => c.dataset.id));
  const titoli = (r.titoli || []).filter((t) => (state.tipo === "all" || t.type === state.tipo) &&
    !shown.has(t.id) && !state.liste.ignorati.has(t.id));
  const persone = state.tipo === "all" ? r.persone || [] : [];
  let body = "";
  if (r.loading) body = `<p class="cast-load">Ricerca in corso…</p>`;
  else if (titoli.length || persone.length) {
    body = (persone.length ? `<div class="person-chips">${persone.map(personChipHtml).join("")}</div>` : "") +
      (titoli.length ? `<div class="p-grid">${titoli.map(searchTileHtml).join("")}</div>` : "");
  } else if (r.errore) body = `<p class="cast-load">Ricerca non disponibile. Riprova tra poco.</p>`;
  else return;
  content.insertAdjacentHTML("beforeend", `<section class="block search-more" id="search-more">
    <div class="block-head"><h2>Altri titoli</h2>${titoli.length ? `<span class="count">${plural(titoli.length, "risultato", "risultati")}</span>` : ""}</div>
    ${body}</section>`);
  $("#empty").hidden = true;
}

content.addEventListener("click", (e) => {
  const sm = e.target.closest("#search-more");
  if (!sm) return;
  const c = e.target.closest(".credit[data-kind]");
  if (c) { state.person = null; openTitle(c.dataset.kind, Number(c.dataset.tmdb)); return; }
  const p = e.target.closest(".person-chip[data-person]");
  if (p) openPerson(Number(p.dataset.person));
});

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


// Suggerimenti con l'AI: Claude oppure un servizio con API compatibile OpenAI (OpenRouter...)
const AI_NAMES = { anthropic: "Claude", openai: "OpenRouter / OpenAI" };

function aiHtml(o) {
  const sel = admin.aiTab || o.servizio;
  const c = o[sel];
  const active = sel === o.servizio;
  const pill = !o.attivo ? "Non collegato" : o.errore ? "Errore" : `Attivo: ${AI_NAMES[o.servizio]}`;
  const envName = sel === "anthropic" ? "ANTHROPIC_API_KEY" : "OPENAI_API_KEY";
  const intro = `<p class="a-note">I suggerimenti li sceglie un modello AI leggendo i tuoi mi piace, i voti e le note (e i titoli già visti),
      tra film e serie degli ultimi 50 anni. Ogni aggiornamento è una sola richiesta e costa pochi centesimi. Senza chiave si usano i titoli consigliati da TMDB.</p>`;
  const tabs = `<div class="seg a-seg" id="a-ai-tabs">${Object.entries(AI_NAMES).map(([k, n]) =>
    `<button type="button" data-p="${k}" class="${k === sel ? "active" : ""}">${esc(n)}${k === o.servizio && o.attivo ? " ✓" : ""}</button>`).join("")}</div>`;
  let fields;
  if (sel === "anthropic") {
    const models = o.modelli.some((m) => m.id === c.modello) ? o.modelli : [...o.modelli, { id: c.modello, nome: c.modello }];
    fields = `<p class="a-note">Crea la chiave su <a href="https://platform.claude.com/settings/keys" target="_blank" rel="noopener">platform.claude.com</a> (servono crediti prepagati).</p>
      <div class="a-grid">
        <label class="a-field"><span class="ticket-label">Modello</span>
          <select id="a-ai-model">${models.map((m) => `<option value="${esc(m.id)}"${m.id === c.modello ? " selected" : ""}>${esc(m.nome)}</option>`).join("")}</select></label>
      </div>`;
  } else {
    fields = `<p class="a-note">Funziona con qualsiasi servizio che offre l'API <code>/chat/completions</code> di OpenAI: per esempio
        <a href="https://openrouter.ai/keys" target="_blank" rel="noopener">OpenRouter</a> (un'unica chiave per i modelli di OpenAI, Google, Anthropic, Meta…),
        OpenAI (<code>https://api.openai.com/v1</code>) o un server locale come Ollama (<code>http://IP:11434/v1</code>).</p>
      <div class="a-grid">
        <label class="a-field"><span class="ticket-label">Indirizzo dell'API</span>
          <input type="url" id="a-ai-url" value="${esc(c.url || o.url_predefinito)}" autocomplete="off" spellcheck="false"></label>
        <label class="a-field"><span class="ticket-label">Modello</span>
          <input type="text" id="a-ai-model" list="a-ai-models" value="${esc(c.modello || "")}" placeholder="Scrivi o scegli dall'elenco" autocomplete="off" spellcheck="false">
          <datalist id="a-ai-models"></datalist></label>
      </div>
      <button type="button" class="a-link" id="a-ai-list">Carica l'elenco dei modelli</button>`;
  }
  return `<div class="a-sec-head"><h3>Suggeriti per te · AI</h3><span class="a-pill ${o.attivo && !o.errore ? "on" : ""}">${esc(pill)}</span></div>
    ${intro}${tabs}${fields}
    ${o.errore && active ? `<p class="a-note err">${esc(o.errore)}</p>` : ""}
    ${c.configurata ? `<p class="a-note">Chiave attuale: <code>${esc(c.chiave)}</code>${c.da_env ? ` (da <code>${envName}</code>; se ne inserisci una qui, vale questa)` : ""}</p>` : ""}
    <form class="a-newtag a-wide" id="a-ai-form">
      <input type="password" id="a-ai-key" placeholder="${c.configurata ? "Nuova chiave (lascia vuoto per tenere quella attuale)" : sel === "anthropic" ? "Chiave API (sk-ant-…)" : "Chiave API (es. sk-or-…)"}" aria-label="Chiave API" autocomplete="off" spellcheck="false">
      <button type="submit" class="a-link">${active ? "Salva" : "Salva e usa questo servizio"}</button>
      ${c.configurata && !c.da_env ? `<button type="button" class="a-link" id="a-ai-remove">Rimuovi chiave</button>` : ""}
    </form>
    <p class="a-msg" id="a-ai-msg" role="status"></p>`;
}

async function loadAi() {
  try {
    admin.ai = await adminFetch("/api/admin/ai");
    admin.aiTab = admin.ai.servizio;
    renderAi();
  } catch (err) {
    if (err.status === 401) { setAdminToken(null); return showLock(true); }
    $("#a-ai").innerHTML = `<p class="a-note err">${esc(err.message)}</p>`;
  }
}

function renderAi() {
  $("#a-ai").innerHTML = aiHtml(admin.ai);
  if (admin.aiTab === "openai" && admin.ai.openai.configurata) loadAiModels(true);
}

async function loadAiModels(quiet = false) {
  const url = $("#a-ai-url")?.value.trim();
  if (!url) return;
  if (!quiet) adminMsg("#a-ai-msg", "Leggo i modelli disponibili…");
  try {
    const key = $("#a-ai-key").value.trim();
    const r = await adminFetch("/api/admin/ai/modelli", { method: "POST", body: JSON.stringify({ url, ...(key ? { chiave: key } : {}) }) });
    $("#a-ai-models").innerHTML = r.modelli.map((m) => `<option value="${esc(m)}"></option>`).join("");
    if (!quiet) adminMsg("#a-ai-msg", `${plural(r.modelli.length, "modello disponibile", "modelli disponibili")}: scrivi nel campo Modello per cercarlo.`, false);
  } catch (err) {
    if (err.status === 401) { setAdminToken(null); return showLock(true); }
    if (!quiet) adminMsg("#a-ai-msg", err.message, true);
  }
}

async function saveAi(body) {
  adminMsg("#a-ai-msg", "Verifico chiave e modello…");
  try {
    const o = await adminFetch("/api/admin/ai", { method: "PUT", body: JSON.stringify(body) });
    admin.ai = o;
    admin.aiTab = o.servizio;
    renderAi();
    adminMsg("#a-ai-msg", body.chiave === "" ? "Chiave rimossa."
      : o.attivo ? `Salvato. I prossimi suggerimenti li sceglie ${o.servizio === "anthropic" ? "Claude" : o.openai.modello}.`
        : "Salvato. Manca la chiave: intanto si usano i consigliati di TMDB.", false);
    state.servizi.ai = o.attivo;
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
  const body = { servizio: admin.aiTab, modello: $("#a-ai-model").value.trim(), ...(key ? { chiave: key } : {}) };
  if (admin.aiTab === "openai") {
    body.url = $("#a-ai-url").value.trim();
    if (!body.modello) return adminMsg("#a-ai-msg", "Scegli il modello da usare.", true);
  }
  saveAi(body);
});
$("#a-ai").addEventListener("click", (e) => {
  if (e.target.id === "a-ai-remove") saveAi({ servizio: admin.aiTab, chiave: "" });
  if (e.target.id === "a-ai-list") loadAiModels();
  const tab = e.target.closest("#a-ai-tabs button[data-p]");
  if (tab && tab.dataset.p !== admin.aiTab) {
    admin.aiTab = tab.dataset.p;
    renderAi();
  }
});

// Da dove arrivano i titoli gia' visti
const VISTI_NAMES = { yamtrack: "Yamtrack", plex: "Plex" };

function vistiHtml(v) {
  const rows = Object.entries(VISTI_NAMES).map(([k, n]) => {
    const linked = v.collegate[k];
    const on = linked && v.fonti.includes(k);
    return `<button type="button" class="a-tag${on ? " on" : ""}" data-src="${k}" aria-pressed="${on}" ${linked ? "" : "disabled"}
      title="${linked ? "" : `${n} non è collegato`}"><i aria-hidden="true">✓</i>${esc(n)}${linked ? "" : " · non collegato"}</button>`;
  }).join("");
  return `<div class="a-sec-head"><h3>Già visti</h3><span class="a-pill ${v.attive.length ? "on" : ""}">${
    v.attive.length ? esc(v.attive.map((k) => VISTI_NAMES[k]).join(" + ")) : "Nessuna fonte"}</span></div>
    <p class="a-note">Da dove leggere i film e le serie che hai già visto: vengono segnati sulle locandine, non ti vengono suggeriti
      e i tuoi voti (da 0 a 10) aiutano l'AI a capire i tuoi gusti. Puoi usarne una sola o tutte e due: se un titolo è in entrambe, vale Yamtrack.
      Da Plex si leggono i titoli guardati dall'account del token e i voti dati in Plex.</p>
    <div class="a-tags" id="a-visti-tags">${rows}</div>
    <p class="a-msg" id="a-visti-msg" role="status"></p>`;
}

async function loadVistiAdmin() {
  try {
    admin.visti = await adminFetch("/api/admin/visti");
    $("#a-visti").innerHTML = vistiHtml(admin.visti);
  } catch (err) {
    if (err.status === 401) { setAdminToken(null); return showLock(true); }
    $("#a-visti").innerHTML = `<p class="a-note err">${esc(err.message)}</p>`;
  }
}

$("#a-visti").addEventListener("click", async (e) => {
  const b = e.target.closest(".a-tag[data-src]");
  if (!b || b.disabled) return;
  const fonti = new Set(admin.visti.fonti);
  fonti.has(b.dataset.src) ? fonti.delete(b.dataset.src) : fonti.add(b.dataset.src);
  try {
    const r = await adminFetch("/api/admin/visti", { method: "PUT", body: JSON.stringify({ fonti: [...fonti] }) });
    admin.visti = r;
    $("#a-visti").innerHTML = vistiHtml(r);
    adminMsg("#a-visti-msg", r.attive.length ? `Salvato: ${plural(r.titoli, "titolo visto", "titoli visti")}.` : "Salvato: nessun titolo segnato come visto.", false);
    state.servizi.visti = r.attive.length > 0;
    state.visti = {};
    if (state.servizi.visti) loadVisti(); else if (state.view !== "richieste") render();
  } catch (err) {
    if (err.status === 401) { setAdminToken(null); return showLock(true); }
    adminMsg("#a-visti-msg", err.message, true);
  }
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
    await loadVistiAdmin();
    state.servizi.visti = (admin.visti?.attive || []).length > 0;
    if (state.servizi.visti) loadVisti(); else render();
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
  loadVistiAdmin();
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

$("#search").addEventListener("input", (e) => { state.q = e.target.value.trim(); scheduleSearch(); renderGenres(); renderChips(); render(); });

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
  if (b.matches("[data-kids]")) {
    state.bestKids = !state.bestKids;
    localSet("bestKids", state.bestKids ? "1" : "0");
    renderFilters();
    renderHero();
    render();
    return;
  }
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
$("#d-cast-row").addEventListener("click", (e) => {
  const a = e.target.closest(".actor[data-person]");
  if (a) openPerson(Number(a.dataset.person));
});
$("#d-back").addEventListener("click", () => { if (state.person) openPerson(state.person.id); });
$("#person").addEventListener("click", (e) => {
  if (e.target.matches("[data-close]") || e.target === e.currentTarget) { e.currentTarget.close(); return; }
  const c = e.target.closest(".credit[data-kind]");
  if (c) { openTitle(c.dataset.kind, Number(c.dataset.tmdb)); return; }
  const f = e.target.closest("#p-filter [data-f]");
  if (f && state.person) { state.person.filtro = f.dataset.f; renderPersonGrid(); }
});
$("#p-bio-more").addEventListener("click", (e) => {
  $("#p-bio").classList.remove("short");
  e.currentTarget.hidden = true;
});

setView(state.view);
loadServizi();
loadListe();
loadGusti().then(() => { if (state.view === "per_te") loadSuggeriti(); });
skeleton();
load().catch(() => { $("#error").hidden = false; $("#error").textContent = "Impossibile contattare il server."; });
setInterval(poll, POLL_MS);
