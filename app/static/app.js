const state = {
  view: ["tendenze", "prossimamente", "da_non_perdere"].includes(localGet("view")) ? localGet("view") : "novita",
  tipo: "all",
  q: "",
  platforms: new Set(JSON.parse(localGet("platforms") || "[]")),
  data: null,
  hero: { items: [], index: 0, timer: null },
  servizi: { radarr: false, sonarr: false, plex: false },
  plex: { movie: new Set(), tv: new Set() },
  seasons: null,
  requests: [],
  requestsLoaded: false,
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
  const meta = state.view === "novita" ? `<b>${esc(relDate(i.date))}</b>${genre}`
    : upcoming ? `<b>${esc(untilText(i.date))}</b>${genre}`
    : `${esc(i.year)}${genre}`;
  const corner = [
    upcoming && i.date ? calHtml(i.date) : "",
    state.view === "da_non_perdere" && i.rating ? `<span class="medal" title="Voto ${esc(i.score?.fonte || "TMDB")} su ${(i.votes || 0).toLocaleString("it-IT")} voti"><i>${esc(i.score?.fonte || "★")}</i><b>${i.rating.toFixed(1)}</b></span>` : "",
    onPlex(i) ? `<span class="on-plex">Su Plex</span>` : "",
  ].join("");
  let posterBox = `
    <div class="poster">
      ${poster}
      <span class="pill ${fresh ? "new" : upcoming ? "soon" : ""}">${esc(pill)}</span>
      ${corner ? `<div class="corner">${corner}</div>` : ""}
      <div class="logos">${i.platforms.map(logoHtml).join("")}</div>
      ${i.rating && state.view !== "da_non_perdere" && !upcoming ? `<span class="rating">★ ${i.rating}</span>` : ""}
      ${i.overview ? `<div class="hover-info"><p>${esc(i.overview)}</p><span class="go">▶ Dettagli</span></div>` : ""}
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
  if (state.view === "tendenze") {
    content.innerHTML = items.length ? blockHtml("I più visti questa settimana", items, true) : "";
    return;
  }
  if (state.view === "da_non_perdere") {
    content.innerHTML = items.length ? blockHtml("I più apprezzati del momento", items) : "";
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
  // Quanti titoli ha ogni piattaforma con i filtri attuali (tipo e ricerca)
  const saved = state.platforms;
  state.platforms = new Set();
  const items = filtered();
  state.platforms = saved;
  const counts = { all: items.length };
  for (const i of items) for (const p of i.platforms) counts[p] = (counts[p] || 0) + 1;
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
    const where = i.platforms.map((p) => platform(p)?.name).filter(Boolean).join(", ");
    $("#hero-eyebrow").textContent = {
      tendenze: `N° ${i.rank} di tendenza in Italia`,
      prossimamente: `${i.type === "movie" ? "Film" : i.badge} · dal ${fmtLong.format(toDate(i.date))}${where ? ` su ${where}` : ""}`,
      da_non_perdere: `${i.score?.fonte || "★"} ${i.rating?.toFixed(1)} · Da non perdere`,
    }[state.view] || `${i.type === "movie" ? "Film" : i.badge} · ${relDate(i.date)}`;
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
  // Prima la sezione aperta (es. in "Da non perdere" l'elemento ha il voto IMDb), poi le altre
  const d = state.data || {};
  const lists = [d[state.view], d.novita, d.tendenze, d.prossimamente, d.da_non_perdere, state.requests];
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

function statusPanelHtml(i, r) {
  const rows = [];
  if (r.plex) {
    const what = i.type === "tv" ? `${r.plex.stagioni} stagion${r.plex.stagioni === 1 ? "e" : "i"} · ${r.plex.episodi} episodi` : "Pronto da guardare";
    rows.push(`<div class="st-row plex"><span class="st-icon">▶</span><div><b>Già su Plex</b><span>${esc(what)}</span></div>
      ${r.plex.url ? `<a class="st-link" href="${esc(r.plex.url)}" target="_blank" rel="noopener">Apri in Plex</a>` : ""}</div>`);
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
      rows.push(`<div class="season-bars">${det.stagioni.map((s) => {
        const pct = s.totali ? Math.round(s.scaricati / s.totali * 100) : 0;
        return `<div class="sb ${s.monitorata ? "" : "off"}" title="Stagione ${s.numero}: ${s.scaricati}/${s.totali} episodi">
          <span class="sb-n">S${s.numero}</span><span class="sb-bar"><i style="width:${pct}%"></i></span><span class="sb-c">${s.scaricati}/${s.totali}</span></div>`;
      }).join("")}</div>`);
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
  if (!svc && !state.servizi.plex) return;
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
  const list = state.requests;
  const active = list.filter((r) => r.download).length;
  content.innerHTML = list.length
    ? `<section class="block">
        <div class="block-head"><h2>Le mie richieste</h2><span class="count">${list.length} ${list.length === 1 ? "titolo" : "titoli"}${active ? ` · ${active} in download` : ""}</span></div>
        <div class="grid">${list.map(requestCardHtml).join("")}</div>
      </section>`
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
    i.rating ? `${i.score ? `${i.score.fonte} ` : ""}★ ${i.rating} (${(i.votes || 0).toLocaleString("it-IT")} voti)` : null,
  ].filter(Boolean).join("  ·  ");
  $("#d-overview").textContent = i.overview || "Trama non ancora disponibile.";
  $("#d-platforms").innerHTML = (i.platforms || []).map(badgeHtml).join("");
  $("#d-links").innerHTML =
    (i.watch_link ? `<a href="${esc(i.watch_link)}" target="_blank" rel="noopener">▶ Dove guardarlo</a>` : "") +
    (i.score?.fonte === "IMDb" ? `<a href="${esc(i.score.url)}" target="_blank" rel="noopener">Scheda su IMDb</a>` : "") +
    `<a href="${esc(i.tmdb_url || `https://www.themoviedb.org/${i.type}/${i.tmdb_id}`)}" target="_blank" rel="noopener">Scheda completa su TMDB</a>`;
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
  renderChips();
  renderHero();
  render();
}));

$("#tipo").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  state.tipo = b.dataset.tipo;
  $("#tipo").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
  renderChips();
  renderHero();
  render();
});

$("#search").addEventListener("input", (e) => { state.q = e.target.value.trim(); renderChips(); render(); });

$("#platforms").addEventListener("click", (e) => {
  const b = e.target.closest(".chip");
  if (!b) return;
  const k = b.dataset.p;
  if (k === "all") state.platforms.clear();
  else state.platforms.has(k) ? state.platforms.delete(k) : state.platforms.add(k);
  localSet("platforms", JSON.stringify([...state.platforms]));
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
skeleton();
load().catch(() => { $("#error").hidden = false; $("#error").textContent = "Impossibile contattare il server."; });
setInterval(poll, POLL_MS);
