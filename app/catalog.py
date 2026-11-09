"""Raccolta delle ultime uscite e dei titoli di tendenza da TMDB.

TMDB riceve i dati sulla disponibilita' nelle piattaforme da JustWatch, quindi
le piattaforme indicate sono quelle del catalogo italiano (watch_region=IT).
"""

import asyncio
import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import httpx

from .config import PLATFORMS, Settings
from .imdb import ImdbRatings
from .rotten import RottenTomatoes

log = logging.getLogger("catalog")

# Reti TMDB delle produzioni originali delle piattaforme (per le serie in arrivo)
NETWORKS = {213: "netflix", 1024: "prime", 2739: "disney", 453: "disney", 2552: "apple", 4330: "paramount"}
# Case di produzione che indicano la piattaforma di un film in arrivo
COMPANY_HINTS = {"netflix": "netflix", "amazon": "prime", "apple original": "apple", "apple studios": "apple",
                 "disney+": "disney", "paramount+": "paramount", "sky original": "sky", "sky studios": "sky"}
IMG = os.getenv("TMDB_IMAGE_URL", "https://image.tmdb.org/t/p")
MONETIZATION = "flatrate|free|ads"

# Stato di una serie su TMDB
TV_STATUS = {
    "Returning Series": "in_corso", "In Production": "in_produzione", "Planned": "in_produzione",
    "Pilot": "in_produzione", "Ended": "terminata", "Canceled": "cancellata",
}

# Versione del formato dei dati salvati: se cambia, il catalogo si ricalcola all'avvio
SCHEMA = 4

# Categorie mostrate nei filtri: chiave -> (nome, generi TMDB dei film, generi TMDB delle serie).
# Le serie hanno generi accorpati (es. "Action & Adventure"), quindi un genere puo' finire in due categorie.
CATEGORIES: dict[str, tuple[str, set[int], set[int]]] = {
    "azione": ("Azione", {28}, {10759}),
    "avventura": ("Avventura", {12}, {10759}),
    "commedia": ("Commedia", {35}, {35}),
    "dramma": ("Dramma", {18}, {18}),
    "thriller": ("Thriller", {53}, set()),
    "crime": ("Crime", {80}, {80}),
    "mistero": ("Mistero", {9648}, {9648}),
    "horror": ("Horror", {27}, set()),
    "fantascienza": ("Fantascienza", {878}, {10765}),
    "fantasy": ("Fantasy", {14}, {10765}),
    "animazione": ("Animazione", {16}, {16}),
    "famiglia": ("Famiglia", {10751}, {10751, 10762}),
    "romantico": ("Romantico", {10749}, set()),
    "documentario": ("Documentario", {99}, {99}),
    "storia": ("Storia e guerra", {36, 10752}, {10768}),
}

# "I migliori"
BEST_MIN_RATING = 7.0         # voto IMDb (o TMDB) minimo
BEST_MIN_RT = 70              # Tomatometer minimo
BEST_PRIOR = 6.8              # voto medio di partenza per la media pesata
BEST_VOTES = {                # voti minimi per fidarsi del punteggio
    "movie": {"imdb": 5000, "tmdb": 300},
    "tv": {"imdb": 2000, "tmdb": 100},
}
# Generi TV esclusi: notiziari, reality, soap, talk show (le serie per bambini restano,
# segnate con "bambini": l'interfaccia lascia scegliere se mostrarle)
TV_EXCLUDED_GENRES = {10763, 10764, 10766, 10767}

# "Bambini" (fino a 12 anni): classificazioni USA ammesse, le piu' diffuse su TMDB
KIDS_MOVIE_CERTS = {"G", "PG"}
KIDS_TV_RATINGS = {"TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG"}
KIDS_MOVIE_EXCLUDED = {27, 53, 80, 10752}           # horror, thriller, crime, guerra
KIDS_TV_EXCLUDED = {80, 10763, 10764, 10766, 10767, 10768}

# Filmografia di un attore: niente talk show, notiziari, reality e premiazioni
CREDITS_EXCLUDED_GENRES = {10763, 10764, 10767}
SELF_ROLES = ("self", "himself", "herself", "se stess", "lui stesso", "lei stessa", "narrator (voice)")
CAST_SIZE = 15


class TMDBError(RuntimeError):
    pass


class Catalog:
    def __init__(self, settings: Settings):
        self.s = settings
        self.data: dict = {"novita": [], "tendenze": [], "piattaforme": {}, "aggiornato": None}
        self.error: str | None = None
        self.refreshing = False
        self._provider_map: dict[int, str] = {}
        self._details: dict[str, dict] = {}
        self._trailers: dict[str, dict | None] = {}
        self._tv_info: dict[int, tuple[float, dict]] = {}
        self._schede: dict[str, tuple[float, dict]] = {}
        self._persone: dict[int, tuple[float, dict]] = {}
        self._sem = asyncio.Semaphore(8)
        self.imdb = ImdbRatings(self.s.data_dir, self.s.imdb_ratings_url, self.s.imdb_ratings)
        self.rt = RottenTomatoes(self.s.data_dir, self.s.omdb_api_key, self.s.omdb_daily_limit)
        self._best_force = False
        self._cache_file = os.path.join(self.s.data_dir, "catalogo.json")
        self._key_file = os.path.join(self.s.data_dir, "chiave_tmdb")
        self.key_from_env = bool(self.s.tmdb_api_key)
        if not self.key_from_env:
            self._load_key()

    # ---------------------------------------------------------- chiave TMDB

    def _load_key(self) -> None:
        try:
            with open(self._key_file, encoding="utf-8") as f:
                self.s.tmdb_api_key = f.read().strip()
        except OSError:
            pass

    @property
    def needs_key(self) -> bool:
        """True se la chiave va inserita dalla pagina (assente o non valida)."""
        if self.key_from_env:
            return False
        return not self.s.tmdb_api_key or (self.error or "").startswith("Chiave TMDB non valida")

    async def set_key(self, key: str) -> None:
        """Verifica la chiave su TMDB e la salva in /data."""
        old = self.s.tmdb_api_key
        self.s.tmdb_api_key = key.strip()
        try:
            async with self._client() as client:
                await self._get(client, "/configuration")
        except TMDBError:
            self.s.tmdb_api_key = old
            raise
        except Exception as e:  # noqa: BLE001
            self.s.tmdb_api_key = old
            raise TMDBError(f"Verifica della chiave non riuscita: {e}") from e
        os.makedirs(self.s.data_dir, exist_ok=True)
        with open(self._key_file, "w", encoding="utf-8") as f:
            f.write(self.s.tmdb_api_key)
        os.chmod(self._key_file, 0o600)
        self.error = None

    # ------------------------------------------------------------------ HTTP

    def _client(self) -> httpx.AsyncClient:
        key = self.s.tmdb_api_key
        headers, params = {"accept": "application/json"}, {}
        # Supporta sia la "API Read Access Token" (v4, JWT) sia la "API Key" (v3)
        if key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {key}"
        else:
            params["api_key"] = key
        return httpx.AsyncClient(base_url=self.s.tmdb_base_url, headers=headers, params=params, timeout=20)

    async def _get(self, client: httpx.AsyncClient, path: str, **params) -> dict:
        params.setdefault("language", self.s.language)
        async with self._sem:
            for attempt in range(4):
                try:
                    r = await client.get(path, params=params)
                except httpx.HTTPError as e:
                    if attempt == 3:
                        raise TMDBError(f"Errore di rete verso TMDB: {e}") from e
                    await asyncio.sleep(2**attempt)
                    continue
                if r.status_code == 429:
                    await asyncio.sleep(float(r.headers.get("Retry-After", 2)))
                    continue
                if r.status_code == 401:
                    raise TMDBError("Chiave TMDB non valida: controlla di averla copiata per intero.")
                if r.status_code == 404:
                    return {}
                r.raise_for_status()
                return r.json()
        raise TMDBError(f"TMDB non risponde: {path}")

    # ------------------------------------------------------------ piattaforme

    async def _load_providers(self, client) -> dict:
        """Associa gli id dei provider TMDB italiani alle piattaforme configurate."""
        pmap: dict[int, str] = {}
        logos: dict[str, tuple[int, str]] = {}
        for kind in ("movie", "tv"):
            res = await self._get(client, f"/watch/providers/{kind}", watch_region=self.s.region)
            for p in res.get("results", []):
                key = match_platform(p.get("provider_name", ""), self.s.enabled_platforms)
                if not key:
                    continue
                pmap[p["provider_id"]] = key
                prio = p.get("display_priorities", {}).get(self.s.region, p.get("display_priority", 999))
                if p.get("logo_path") and (key not in logos or prio < logos[key][0]):
                    logos[key] = (prio, p["logo_path"])
        self._provider_map = pmap
        return {
            k: {
                "name": PLATFORMS[k]["name"],
                "color": PLATFORMS[k]["color"],
                "logo": f"{IMG}/w92{logos[k][1]}" if k in logos else None,
            }
            for k in self.s.enabled_platforms
            if k in pmap.values()
        }

    def _platforms_of(self, details: dict) -> tuple[list[str], str | None]:
        it = details.get("watch/providers", {}).get("results", {}).get(self.s.region, {})
        found: list[str] = []
        for group in ("flatrate", "free", "ads"):
            for p in it.get(group, []):
                key = self._provider_map.get(p["provider_id"])
                if key and key not in found:
                    found.append(key)
        return found, it.get("link")

    # ---------------------------------------------------------------- discover

    async def _discover(self, client, kind: str, pages: int, providers: bool = True, **params) -> list[int]:
        base = {"include_adult": "false", **params}
        if providers:
            # "|" = disponibile su almeno una piattaforma (la "," di TMDB significa "su tutte")
            base |= {
                "watch_region": self.s.region,
                "with_watch_providers": "|".join(str(i) for i in self._provider_map) or "0",
                "with_watch_monetization_types": MONETIZATION,
            }
        out: list[int] = []
        page, total = 1, 1
        while page <= min(pages, total):
            res = await self._get(client, f"/discover/{kind}", page=page, **base)
            total = res.get("total_pages", 0)
            for r in res.get("results", []):
                if r["id"] not in out:
                    out.append(r["id"])
            page += 1
        return out

    async def _details_for(self, client, kind: str, tmdb_id: int) -> dict:
        key = f"{kind}-{tmdb_id}"
        if key not in self._details:
            extra = "watch/providers,release_dates" if kind == "movie" else "watch/providers,external_ids,content_ratings"
            d = await self._get(client, f"/{kind}/{tmdb_id}", append_to_response=extra)
            if d:
                d["_tradotto"] = bool(d.get("overview"))
            if d and not d.get("overview"):
                # Trama non tradotta: ripiego sull'inglese
                en = await self._get(client, f"/{kind}/{tmdb_id}", language="en-US")
                d["overview"] = en.get("overview", "")
            self._details[key] = d
        return self._details[key]

    # ------------------------------------------------------------------ items

    def _movie_item(self, d: dict, today: date) -> dict | None:
        platforms, link = self._platforms_of(d)
        if not platforms:
            return None
        # Data di uscita digitale/TV in Italia, se nota; altrimenti uscita principale
        out_date = None
        for rd in d.get("release_dates", {}).get("results", []):
            if rd.get("iso_3166_1") != self.s.region:
                continue
            dates = [x["release_date"][:10] for x in rd.get("release_dates", []) if x.get("type") in (4, 6)]
            dates = [x for x in dates if x <= today.isoformat()]
            if dates:
                out_date = max(dates)
        out_date = out_date or d.get("release_date") or None
        return self._base_item(d, "movie", platforms, link) | {
            "title": d.get("title") or d.get("original_title"),
            "original_title": d.get("original_title"),
            "date": out_date,
            "year": (d.get("release_date") or "")[:4],
            "badge": "Film",
            "runtime": d.get("runtime"),
        }

    def _tv_item(self, d: dict, today: date) -> dict | None:
        platforms, link = self._platforms_of(d)
        if not platforms:
            return None
        last = d.get("last_episode_to_air") or {}
        first = d.get("first_air_date") or ""
        since = (today - timedelta(days=self.s.tv_days)).isoformat()
        season = last.get("season_number")
        season_start = next(
            (s.get("air_date") for s in d.get("seasons", []) if s.get("season_number") == season), None
        )
        if first and first >= since:
            badge = "Nuova serie"
        elif season_start and season_start >= since:
            badge = f"Nuova stagione {season}"
        else:
            badge = f"Nuovi episodi · Stagione {season}" if season else "Nuovi episodi"
        return self._base_item(d, "tv", platforms, link) | {
            "title": d.get("name") or d.get("original_name"),
            "original_title": d.get("original_name"),
            "date": last.get("air_date") or first or None,
            "year": first[:4],
            "badge": badge,
            "seasons": d.get("number_of_seasons"),
        }

    def _base_item(self, d: dict, kind: str, platforms: list[str], link: str | None) -> dict:
        genre_ids = {g["id"] for g in d.get("genres", [])}
        return {
            "id": f"{kind}-{d['id']}",
            "tmdb_id": d["id"],
            "type": kind,
            "overview": d.get("overview") or "",
            "poster": f"{IMG}/w342{d['poster_path']}" if d.get("poster_path") else None,
            "backdrop": f"{IMG}/w780{d['backdrop_path']}" if d.get("backdrop_path") else None,
            "genres": [g["name"] for g in d.get("genres", [])],
            "categorie": categories_of(kind, genre_ids),
            "rating": round(d.get("vote_average") or 0, 1),
            "votes": d.get("vote_count") or 0,
            "popularity": d.get("popularity") or 0,
            "platforms": platforms,
            "watch_link": link,
            "tmdb_url": f"https://www.themoviedb.org/{kind}/{d['id']}",
            "imdb_id": d.get("imdb_id") or (d.get("external_ids") or {}).get("imdb_id"),
        }

    async def _build(self, client, kind: str, ids: list[int], today: date) -> list[dict]:
        details = await asyncio.gather(*(self._details_for(client, kind, i) for i in ids))
        make = self._movie_item if kind == "movie" else self._tv_item
        return [it for d in details if d and (it := make(d, today))]

    # ---------------------------------------------------------------- refresh

    async def refresh(self) -> None:
        if not self.s.tmdb_api_key:
            self.error = "Manca la chiave TMDB: inseriscila nella pagina o imposta TMDB_API_KEY."
            log.error(self.error)
            return
        if self.refreshing:
            return
        self.refreshing = True
        self._details = {}
        try:
            async with self._client() as client:
                data = await self._collect(client)
            self.data = data
            self.error = None
            self._save()
            log.info("Catalogo aggiornato: %d novita', %d tendenze", len(data["novita"]), len(data["tendenze"]))
        except Exception as e:  # noqa: BLE001 - l'errore viene mostrato nella UI
            self.error = str(e) if isinstance(e, TMDBError) else f"Aggiornamento non riuscito: {e}"
            log.exception("Aggiornamento non riuscito")
        finally:
            self.refreshing = False

    async def _collect(self, client) -> dict:
        today = datetime.now(timezone.utc).date()
        piattaforme = await self._load_providers(client)
        if not self._provider_map:
            raise TMDBError("Nessuna piattaforma trovata per la regione configurata.")
        p, t = self.s.pages, today.isoformat()
        m_since = (today - timedelta(days=self.s.movie_days)).isoformat()
        tv_since = (today - timedelta(days=self.s.tv_days)).isoformat()

        # Ordinate per popolarita': dentro la finestra temporale si prendono i titoli
        # piu' rilevanti, poi l'interfaccia li ordina per data di uscita.
        movie_lists, tv_lists = await asyncio.gather(
            asyncio.gather(
                # Film usciti di recente...
                self._discover(client, "movie", p, sort_by="popularity.desc",
                               **{"primary_release_date.gte": m_since, "primary_release_date.lte": t}),
                # ...e film arrivati in digitale/TV in Italia di recente
                self._discover(client, "movie", p, sort_by="popularity.desc", region=self.s.region,
                               with_release_type="4|6",
                               **{"release_date.gte": m_since, "release_date.lte": t}),
            ),
            asyncio.gather(
                # Serie nuove...
                self._discover(client, "tv", p, sort_by="popularity.desc",
                               **{"first_air_date.gte": tv_since, "first_air_date.lte": t}),
                # ...e serie con episodi/stagioni nuove
                self._discover(client, "tv", p, sort_by="popularity.desc",
                               **{"air_date.gte": tv_since, "air_date.lte": t}),
            ),
        )
        movie_ids = dedupe(sum(movie_lists, []))
        tv_ids = dedupe(sum(tv_lists, []))

        movies, shows = await asyncio.gather(
            self._build(client, "movie", movie_ids, today),
            self._build(client, "tv", tv_ids, today),
        )
        novita = [x for x in movies + shows if x["date"] and x["date"] <= t]
        novita = [x for x in novita if x["date"] >= (m_since if x["type"] == "movie" else tv_since)]
        novita.sort(key=lambda x: (x["date"], x["popularity"]), reverse=True)

        tendenze, prossimamente, bambini = await asyncio.gather(
            self._trending(client, today), self._upcoming(client, today), self._kids(client, today),
        )
        # "I migliori" guarda molti anni indietro: si ricalcola una volta al giorno
        migliori, migliori_ts = self.data.get("migliori"), self.data.get("migliori_aggiornato")
        if self._best_due():
            migliori = await self._best(client, today)
            migliori_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._best_force = False

        novita = unique_items(novita)
        await self._add_ratings(tendenze, novita, migliori or [], bambini)

        return {
            "schema": SCHEMA,
            "novita": novita,
            "tendenze": tendenze,
            "prossimamente": prossimamente,
            "migliori": migliori or [],
            "migliori_aggiornato": migliori_ts,
            "bambini": bambini,
            "piattaforme": piattaforme,
            "aggiornato": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    async def _add_ratings(self, *groups: list[dict]) -> None:
        """Voti IMDb e Rotten Tomatoes (con la media) su tutti i titoli gia' usciti.

        I gruppi vanno in ordine di priorita': se la quota giornaliera di OMDb finisce,
        i Tomatometer mancanti arrivano ai primi titoli.
        """
        items = [it for g in groups for it in g if it.get("imdb_id")]
        if not items:
            return
        imdb = await self.imdb.lookup({it["imdb_id"] for it in items})
        wanted = list(dict.fromkeys((it["imdb_id"], int(it["year"] or 0)) for it in items))
        tomatoes = await self.rt.lookup(wanted) if self.rt.configured else {
            i: v for i, _ in wanted if (v := self.rt.cached(i)) is not None}
        for it in items:
            it["voti"] = combined_ratings(it, imdb.get(it["imdb_id"]), tomatoes.get(it["imdb_id"]))

    async def ratings(self, kind: str, tmdb_id: int, imdb_id: str | None = None,
                      title: str = "", year: int = 0) -> dict | None:
        """Voti IMDb e Rotten Tomatoes di un singolo titolo (per la scheda)."""
        if not imdb_id:
            async with self._client() as client:
                ext = await self._get(client, f"/{kind}/{tmdb_id}/external_ids")
            imdb_id = ext.get("imdb_id")
        if not imdb_id:
            return None
        imdb = await self.imdb.lookup({imdb_id})
        rt = (await self.rt.lookup([(imdb_id, year)])).get(imdb_id) if self.rt.configured else self.rt.cached(imdb_id)
        return combined_ratings({"imdb_id": imdb_id, "title": title, "original_title": title},
                                imdb.get(imdb_id), rt)

    def _best_due(self) -> bool:
        ts = self.data.get("migliori_aggiornato")
        if self._best_force or not ts or self.data.get("schema") != SCHEMA or not self.data.get("migliori"):
            return True
        return datetime.now(timezone.utc) - datetime.fromisoformat(ts) > timedelta(hours=max(self.s.best_hours, 1))

    def force_best(self) -> None:
        """Al prossimo aggiornamento ricalcola "I migliori" (es. dopo aver inserito la chiave OMDb)."""
        self._best_force = True

    async def _trending(self, client, today: date) -> list[dict]:
        """Titoli di tendenza disponibili sulle piattaforme italiane.

        Combina le tendenze della settimana di TMDB con i titoli piu' popolari
        del catalogo italiano, tenendo solo quelli disponibili in Italia.
        """
        ranked: list[tuple[str, int]] = []
        for page in (1, 2, 3):
            res = await self._get(client, "/trending/all/week", page=page)
            for r in res.get("results", []):
                if r.get("media_type") in ("movie", "tv"):
                    ranked.append((r["media_type"], r["id"]))
        pop_movie, pop_tv = await asyncio.gather(
            self._discover(client, "movie", 2, sort_by="popularity.desc"),
            self._discover(client, "tv", 2, sort_by="popularity.desc"),
        )
        # Alterna film e serie popolari in Italia dopo le tendenze globali
        for m, s in zip(pop_movie, pop_tv):
            ranked += [("movie", m), ("tv", s)]

        details = await asyncio.gather(*(self._details_for(client, k, i) for k, i in ranked))
        items = []
        for (kind, _), d in zip(ranked, details):
            if not d:
                continue
            it = self._movie_item(d, today) if kind == "movie" else self._tv_item(d, today)
            if it:
                items.append(it)
        items = unique_items(items)[:40]
        for n, it in enumerate(items, 1):
            it["rank"] = n
        return items

    # ------------------------------------------------------- prossimamente

    def _hint_platforms(self, *texts: str) -> list[str]:
        found: list[str] = []
        for t in texts:
            if not t:
                continue
            key = match_platform(t, self.s.enabled_platforms)
            if not key:
                low = t.lower()
                key = next((v for k, v in COMPANY_HINTS.items() if k in low and v in self.s.enabled_platforms), None)
            if key and key not in found:
                found.append(key)
        return found

    async def _upcoming(self, client, today: date) -> list[dict]:
        """Film e serie in arrivo sulle piattaforme italiane, con la data di uscita."""
        days = self.s.upcoming_days
        start = (today + timedelta(days=1)).isoformat()
        end = (today + timedelta(days=days)).isoformat()
        networks = "|".join(str(n) for n, k in NETWORKS.items() if k in self.s.enabled_platforms)
        movie_ids, tv_new, tv_seasons, tv_originals = await asyncio.gather(
            # Film in uscita in digitale o in TV in Italia
            self._discover(client, "movie", 3, providers=False, region=self.s.region, with_release_type="4|6",
                           sort_by="popularity.desc", **{"release_date.gte": start, "release_date.lte": end}),
            # Serie nuove delle piattaforme
            self._discover(client, "tv", 2, providers=False, with_networks=networks or "0", sort_by="popularity.desc",
                           **{"first_air_date.gte": start, "first_air_date.lte": end}),
            # Nuove stagioni di serie gia' presenti sulle piattaforme italiane
            self._discover(client, "tv", 3, sort_by="popularity.desc", **{"air_date.gte": start, "air_date.lte": end}),
            # Nuove stagioni delle produzioni originali
            self._discover(client, "tv", 2, providers=False, with_networks=networks or "0", sort_by="popularity.desc",
                           **{"air_date.gte": start, "air_date.lte": end}),
        )
        movies, shows = await asyncio.gather(
            asyncio.gather(*(self._details_for(client, "movie", i) for i in movie_ids)),
            asyncio.gather(*(self._details_for(client, "tv", i) for i in dedupe(tv_new + tv_seasons + tv_originals))),
        )
        t = today.isoformat()
        items: list[dict] = []
        for d in movies:
            if not d:
                continue
            out_date, notes = None, []
            for rd in d.get("release_dates", {}).get("results", []):
                if rd.get("iso_3166_1") != self.s.region:
                    continue
                for x in rd.get("release_dates", []):
                    day = (x.get("release_date") or "")[:10]
                    if x.get("type") in (4, 6) and start <= day <= end:
                        out_date = min(out_date or day, day)
                        notes.append(x.get("note") or "")
            if not out_date:
                continue
            platforms, link = self._platforms_of(d)
            platforms = dedupe(platforms + self._hint_platforms(
                *notes, *(c.get("name", "") for c in d.get("production_companies", []))))
            if not platforms:
                continue
            items.append(self._base_item(d, "movie", platforms, link) | {
                "title": d.get("title") or d.get("original_title"),
                "original_title": d.get("original_title"),
                "date": out_date,
                "year": out_date[:4],
                "badge": "Film",
                "runtime": d.get("runtime"),
            })
        for d in shows:
            if not d:
                continue
            first = d.get("first_air_date") or ""
            nxt = d.get("next_episode_to_air") or {}
            if first > t:
                out_date, badge = first, "Nuova serie"
            elif nxt.get("episode_number") == 1 and (nxt.get("air_date") or "") > t:
                out_date, badge = nxt["air_date"], f"Stagione {nxt.get('season_number')}"
            else:
                continue  # solo episodi settimanali di una stagione gia' iniziata
            if out_date > end:
                continue
            platforms, link = self._platforms_of(d)
            platforms = dedupe(platforms + [NETWORKS[n["id"]] for n in d.get("networks", [])
                                            if NETWORKS.get(n.get("id")) in self.s.enabled_platforms])
            if not platforms:
                continue
            items.append(self._base_item(d, "tv", platforms, link) | {
                "title": d.get("name") or d.get("original_name"),
                "original_title": d.get("original_name"),
                "date": out_date,
                "year": out_date[:4],
                "badge": badge,
                "seasons": d.get("number_of_seasons"),
            })
        items = unique_items(items)
        items.sort(key=lambda x: (x["date"], -x["popularity"]))
        return items

    async def _best(self, client, today: date) -> list[dict]:
        """I film e le serie migliori degli ultimi anni, disponibili in Italia e con la scheda in italiano.

        Per ogni anno prende i titoli piu' votati e quelli piu' visti; tiene quelli con abbastanza
        voti su IMDb e li ordina per Tomatometer di Rotten Tomatoes (via OMDb). I titoli senza
        Tomatometer (quasi tutte le serie) usano il voto IMDb riportato in centesimi.
        L'anno di una serie e' quello del debutto.
        """
        years = range(today.year - max(self.s.best_years, 1) + 1, today.year + 1)
        tv_without = ",".join(map(str, sorted(TV_EXCLUDED_GENRES)))
        jobs = []
        for y in years:
            jobs += [
                self._discover(client, "movie", 1, sort_by="vote_average.desc", primary_release_year=y,
                               without_genres="10770", **{"vote_count.gte": 300}),
                self._discover(client, "movie", 1, sort_by="vote_count.desc", primary_release_year=y,
                               without_genres="10770"),
                self._discover(client, "tv", 1, sort_by="vote_average.desc", first_air_date_year=y,
                               without_genres=tv_without, **{"vote_count.gte": 100}),
                self._discover(client, "tv", 1, sort_by="vote_count.desc", first_air_date_year=y,
                               without_genres=tv_without),
            ]
        found = await asyncio.gather(*jobs)
        movie_ids = dedupe(sum(found[0::4], []) + sum(found[1::4], []))
        tv_ids = dedupe(sum(found[2::4], []) + sum(found[3::4], []))
        movies, shows = await asyncio.gather(
            self._build(client, "movie", movie_ids, today), self._build(client, "tv", tv_ids, today),
        )
        candidates = []
        for it in unique_items(movies + shows):
            d = self._details.get(it["id"]) or {}
            if not d.get("_tradotto"):
                continue  # niente scheda in italiano
            if it["type"] == "tv" and any(g.get("id") in TV_EXCLUDED_GENRES for g in d.get("genres", [])):
                continue
            if not it["year"] or int(it["year"]) not in years or (it["date"] or "") > today.isoformat():
                continue
            imdb_id = d.get("imdb_id") or (d.get("external_ids") or {}).get("imdb_id")
            it["bambini"] = is_kids(d, it["type"])
            candidates.append((it, imdb_id))

        imdb = await self.imdb.lookup({i for _, i in candidates if i})
        good = []
        for it, imdb_id in candidates:
            mins = BEST_VOTES[it["type"]]
            if imdb_id in imdb and imdb[imdb_id][1] >= mins["imdb"]:
                voto, voti = imdb[imdb_id]
                it["score"] = {"fonte": "IMDb", "voto": voto, "voti": voti,
                               "url": f"https://www.imdb.com/title/{imdb_id}/"}
                m = mins["imdb"]
            elif imdb_id not in imdb and it["votes"] >= mins["tmdb"]:
                # Titolo non su IMDb (o IMDb non raggiungibile): voto TMDB
                voto, voti = it["rating"], it["votes"]
                it["score"] = {"fonte": "TMDB", "voto": voto, "voti": voti, "url": it["tmdb_url"]}
                m = mins["tmdb"]
            else:
                continue  # troppo pochi voti per fidarsi
            if voto < BEST_MIN_RATING:
                continue
            it["rating"], it["votes"] = voto, voti
            it["_peso"] = (voti * voto + m * BEST_PRIOR) / (voti + m)
            good.append((it, imdb_id))

        # Tomatometer: prima ai titoli con la media IMDb piu' alta, cosi' la quota giornaliera di OMDb
        # va a quelli che possono finire in cima
        good.sort(key=lambda x: x[0]["_peso"], reverse=True)
        tomatoes = await self.rt.lookup([(i, int(it["year"])) for it, i in good if i])
        items = []
        for it, imdb_id in good:
            rt = tomatoes.get(imdb_id)
            if rt is not None:
                if rt < BEST_MIN_RT:
                    continue
                it["rt"] = {"voto": rt, "url": f"https://www.rottentomatoes.com/search?search={quote(it['original_title'] or it['title'] or '')}"}
            it["_ordine"] = (rt if rt is not None else it["_peso"] * 10, it["_peso"])
            items.append(it)
        items.sort(key=lambda x: x["_ordine"], reverse=True)
        for it in items:
            it.pop("_peso", None)
            it.pop("_ordine", None)
        return items

    async def _kids(self, client, today: date) -> list[dict]:
        """I film e le serie piu' popolari adatti ai bambini fino a 12 anni, disponibili in Italia.

        Film: genere famiglia o animazione e classificazione USA G o PG.
        Serie: genere bambini, famiglia o animazione e classificazione USA da TV-Y a TV-PG
        (senza classificazione solo se TMDB le mette tra le serie per bambini).
        """
        movie_ids, tv_ids = await asyncio.gather(
            self._discover(client, "movie", 4, sort_by="popularity.desc", with_genres="10751|16",
                           certification_country="US", certification="|".join(sorted(KIDS_MOVIE_CERTS)),
                           without_genres=",".join(map(str, sorted(KIDS_MOVIE_EXCLUDED)))),
            self._discover(client, "tv", 4, sort_by="popularity.desc", with_genres="10762|10751|16",
                           without_genres=",".join(map(str, sorted(KIDS_TV_EXCLUDED)))),
        )
        movies, shows = await asyncio.gather(
            self._build(client, "movie", movie_ids, today), self._build(client, "tv", tv_ids, today),
        )
        out = []
        for it in movies + shows:
            d = self._details.get(it["id"]) or {}
            if not d.get("_tradotto") or not it["date"] or it["date"] > today.isoformat():
                continue
            genres = {g.get("id") for g in d.get("genres", [])}
            if it["type"] == "movie":
                cert = us_certification(d)
                if genres & KIDS_MOVIE_EXCLUDED or (cert is not None and cert not in KIDS_MOVIE_CERTS):
                    continue
            else:
                rating = us_tv_rating(d)
                if genres & KIDS_TV_EXCLUDED:
                    continue
                if rating is None and 10762 not in genres:
                    continue  # senza classificazione: solo le serie che TMDB indica per bambini
                if rating is not None and rating not in KIDS_TV_RATINGS:
                    continue
            out.append(it)
        out = unique_items(out)
        out.sort(key=lambda x: x["popularity"], reverse=True)
        return out[:120]

    @staticmethod
    def _season_start(d: dict) -> str | None:
        """Data di inizio della stagione andata in onda per ultima (o della serie)."""
        season = (d.get("last_episode_to_air") or {}).get("season_number")
        start = next((s.get("air_date") for s in d.get("seasons", []) if s.get("season_number") == season), None)
        return start or d.get("first_air_date")

    async def tv_info(self, tmdb_id: int) -> dict:
        """Stagioni, stato della serie (in corso, terminata...) e prossima uscita. Cache di 30 minuti."""
        cached = self._tv_info.get(tmdb_id)
        if cached and time.monotonic() - cached[0] < 1800:
            return cached[1]
        async with self._client() as client:
            d = await self._get(client, f"/tv/{tmdb_id}")
        today = datetime.now(timezone.utc).date().isoformat()
        seasons = [
            {"numero": s["season_number"], "nome": s.get("name"), "episodi": s.get("episode_count") or 0,
             "data": s.get("air_date")}
            for s in d.get("seasons", []) if s.get("season_number", 0) > 0
        ]
        last = d.get("last_episode_to_air") or {}
        nxt = d.get("next_episode_to_air") or {}
        prossimo = None
        if nxt.get("air_date"):
            prossimo = {"data": nxt["air_date"], "stagione": nxt.get("season_number"),
                        "episodio": nxt.get("episode_number"),
                        "nuova_stagione": nxt.get("episode_number") == 1}
        else:
            # Stagione annunciata ma senza episodi in calendario
            future = [s for s in seasons if s["numero"] > (last.get("season_number") or 0)
                      and (not s["data"] or s["data"] > today)]
            if future:
                s = future[0]
                prossimo = {"data": s["data"], "stagione": s["numero"], "episodio": 1, "nuova_stagione": True}
        info = {
            "stato": TV_STATUS.get(d.get("status"), "in_corso"),
            "prossimo": prossimo,
            "ultimo": {"data": last.get("air_date"), "stagione": last.get("season_number"),
                       "episodio": last.get("episode_number")} if last else None,
            "stagioni": seasons,
        }
        self._tv_info[tmdb_id] = (time.monotonic(), info)
        return info

    async def tv_seasons(self, tmdb_id: int) -> list[dict]:
        return (await self.tv_info(tmdb_id))["stagioni"]

    # ------------------------------------------------------- trailer e id

    async def trailer(self, kind: str, tmdb_id: int) -> dict | None:
        """Il trailer YouTube migliore: in italiano se c'e', altrimenti in inglese."""
        key = f"{kind}-{tmdb_id}"
        if key in self._trailers:
            return self._trailers[key]
        async with self._client() as client:
            res = await self._get(client, f"/{kind}/{tmdb_id}/videos", include_video_language="it,en,null")
        videos = [v for v in res.get("results", []) if v.get("site") == "YouTube" and v.get("key")]

        def score(v: dict) -> tuple:
            return (
                v.get("iso_639_1") == "it",
                v.get("type") == "Trailer",
                v.get("type") == "Teaser",
                bool(v.get("official")),
                v.get("published_at") or "",
            )

        best = max(videos, key=score, default=None)
        found = {"key": best["key"], "name": best.get("name"), "lingua": best.get("iso_639_1")} if best else None
        self._trailers[key] = found
        return found

    async def tvdb_id(self, tmdb_id: int) -> int | None:
        async with self._client() as client:
            res = await self._get(client, f"/tv/{tmdb_id}/external_ids")
        return res.get("tvdb_id")

    # ------------------------------------------------------ cast e attori

    async def scheda(self, kind: str, tmdb_id: int) -> dict:
        """Generi e attori principali di un titolo (per le serie, il cast di tutte le stagioni)."""
        key = f"{kind}-{tmdb_id}"
        cached = self._schede.get(key)
        if cached and time.monotonic() - cached[0] < 6 * 3600:
            return cached[1]
        credits = "credits" if kind == "movie" else "aggregate_credits"
        async with self._client() as client:
            d = await self._get(client, f"/{kind}/{tmdb_id}", append_to_response=credits)
        cast = []
        for c in (d.get(credits) or {}).get("cast", []):
            if not c.get("id"):
                continue
            roles = c.get("roles") or []
            character = c.get("character") or (roles[0].get("character") if roles else "") or ""
            cast.append({"id": c["id"], "nome": c.get("name") or "", "personaggio": character,
                         "foto": _photo(c.get("profile_path")),
                         "episodi": c.get("total_episode_count"), "ordine": c.get("order", 999)})
        cast.sort(key=lambda c: c["ordine"])
        for c in cast:
            c.pop("ordine")
        out = {"generi": [g["name"] for g in d.get("genres", []) if g.get("name")], "cast": cast[:CAST_SIZE]}
        if d:
            self._schede[key] = (time.monotonic(), out)
        return out

    async def persona(self, person_id: int) -> dict | None:
        """Un attore (o regista) con tutti i film e le serie in cui ha lavorato, dal piu' recente."""
        cached = self._persone.get(person_id)
        if cached and time.monotonic() - cached[0] < 6 * 3600:
            return cached[1]
        async with self._client() as client:
            d = await self._get(client, f"/person/{person_id}", append_to_response="combined_credits")
            if d and not d.get("biography"):
                en = await self._get(client, f"/person/{person_id}", language="en-US")
                d["biography"] = en.get("biography", "")
        if not d or not d.get("id"):
            return None
        credits = d.get("combined_credits") or {}
        cast = [dict(c, _ruolo=c.get("character") or "") for c in credits.get("cast", [])]
        crew = [dict(c, _ruolo=c.get("job") or "") for c in credits.get("crew", [])
                if c.get("job") in ("Director", "Creator", "Screenplay", "Writer")]
        acting = d.get("known_for_department") in (None, "", "Acting") or len(cast) >= len(crew)
        titoli: dict[str, dict] = {}
        for c in cast if acting else crew:
            kind = c.get("media_type")
            if kind not in ("movie", "tv") or not c.get("id"):
                continue
            if set(c.get("genre_ids") or []) & CREDITS_EXCLUDED_GENRES:
                continue
            ruolo = c["_ruolo"]
            if acting and ruolo.lower().startswith(SELF_ROLES):
                continue
            key = f"{kind}-{c['id']}"
            date_ = (c.get("release_date") if kind == "movie" else c.get("first_air_date")) or None
            if key in titoli:
                prev = titoli[key]
                if ruolo and ruolo not in prev["ruolo"].split(" / "):
                    prev["ruolo"] = " / ".join(filter(None, [prev["ruolo"], ruolo]))
                continue
            titoli[key] = {
                "id": key, "tmdb_id": c["id"], "type": kind,
                "title": c.get("title") or c.get("name") or c.get("original_title") or c.get("original_name"),
                "original_title": c.get("original_title") or c.get("original_name"),
                "date": date_, "year": (date_ or "")[:4],
                "ruolo": ruolo, "episodi": c.get("episode_count") if kind == "tv" else None,
                "poster": _photo(c.get("poster_path"), "w342"),
                "rating": round(c.get("vote_average") or 0, 1), "votes": c.get("vote_count") or 0,
                "popularity": c.get("popularity") or 0,
            }
        items = list(titoli.values())
        # Prima i titoli annunciati senza data, poi dal piu' recente al piu' vecchio
        items.sort(key=lambda x: x["date"] or "9999", reverse=True)
        out = {
            "id": d["id"], "nome": d.get("name") or "", "foto": _photo(d.get("profile_path"), "h632"),
            "biografia": d.get("biography") or "", "nascita": d.get("birthday"), "morte": d.get("deathday"),
            "luogo": d.get("place_of_birth"), "sesso": d.get("gender") or 0, "mestiere": "attore" if acting else "regia",
            "tmdb_url": f"https://www.themoviedb.org/person/{d['id']}",
            "imdb_id": d.get("imdb_id"),
            "titoli": items,
        }
        self._persone[person_id] = (time.monotonic(), out)
        return out

    # ------------------------------------------------- titoli di qualsiasi anno

    async def item_for(self, kind: str, tmdb_id: int) -> dict | None:
        """Scheda di un titolo qualsiasi (anche vecchio o non in streaming), per i suggerimenti."""
        async with self._client() as client:
            if not self._provider_map:
                await self._load_providers(client)
            d = await self._details_for(client, kind, tmdb_id)
        if not d or not d.get("id"):
            return None
        platforms, link = self._platforms_of(d)
        if kind == "movie":
            extra = {"title": d.get("title") or d.get("original_title"), "original_title": d.get("original_title"),
                     "date": d.get("release_date") or None, "year": (d.get("release_date") or "")[:4],
                     "badge": "Film", "runtime": d.get("runtime")}
        else:
            first = d.get("first_air_date") or ""
            extra = {"title": d.get("name") or d.get("original_name"), "original_title": d.get("original_name"),
                     "date": first or None, "year": first[:4], "badge": "Serie TV",
                     "seasons": d.get("number_of_seasons")}
        return self._base_item(d, kind, platforms, link) | extra

    async def search(self, kind: str, title: str, year: int | None = None) -> int | None:
        """Id TMDB del titolo piu' rilevante con quel nome (e anno, se noto)."""
        if not title:
            return None
        year_key = "year" if kind == "movie" else "first_air_date_year"
        async with self._client() as client:
            for params in ({year_key: year} if year else None, {}):
                if params is None:
                    continue
                res = await self._get(client, f"/search/{kind}", query=title, include_adult="false", **params)
                results = res.get("results") or []
                if results:
                    return results[0]["id"]
        return None

    async def recommendations(self, kind: str, tmdb_id: int) -> list[tuple[str, int]]:
        """Titoli consigliati da TMDB per un titolo: [(tipo, id), ...]."""
        async with self._client() as client:
            res = await self._get(client, f"/{kind}/{tmdb_id}/recommendations")
        return [(kind, r["id"]) for r in res.get("results", []) if r.get("id")]

    def find_item(self, item_id: str) -> dict | None:
        for group in ("novita", "tendenze", "prossimamente", "migliori", "bambini"):
            for it in self.data.get(group, []):
                if it["id"] == item_id:
                    return it
        return None

    # ------------------------------------------------------------ persistenza

    def _save(self) -> None:
        try:
            os.makedirs(self.s.data_dir, exist_ok=True)
            tmp = self._cache_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self._cache_file)
        except OSError as e:
            log.warning("Impossibile salvare la cache: %s", e)

    def load_cache(self) -> None:
        try:
            with open(self._cache_file, encoding="utf-8") as f:
                self.data = json.load(f)
            log.info("Cache caricata (%s)", self.data.get("aggiornato"))
        except (OSError, ValueError):
            pass

    def is_stale(self) -> bool:
        ts = self.data.get("aggiornato")
        if not ts or self.data.get("schema") != SCHEMA:
            return True
        age = datetime.now(timezone.utc) - datetime.fromisoformat(ts)
        return age > timedelta(minutes=self.s.refresh_minutes)


def combined_ratings(it: dict, imdb: tuple[float, int] | None, rt: int | None) -> dict | None:
    """{"imdb": {...}, "rt": {...}, "media": 8.4}: la media e' in decimi (IMDb e Tomatometer/10)."""
    out: dict = {}
    parts: list[float] = []
    if imdb:
        out["imdb"] = {"voto": imdb[0], "voti": imdb[1], "url": f"https://www.imdb.com/title/{it['imdb_id']}/"}
        parts.append(imdb[0])
    if rt is not None:
        name = it.get("original_title") or it.get("title") or ""
        out["rt"] = {"voto": rt, "url": f"https://www.rottentomatoes.com/search?search={quote(name)}"}
        parts.append(rt / 10)
    if not parts:
        return None
    out["media"] = round(sum(parts) / len(parts), 1)
    return out


def match_platform(provider_name: str, enabled: list[str]) -> str | None:
    name = provider_name.lower() + " "
    for key in enabled:
        if any(m in name for m in PLATFORMS[key]["match"]):
            return key
    return None


def categories_of(kind: str, genre_ids: set[int]) -> list[str]:
    idx = 1 if kind == "movie" else 2
    return [k for k, v in CATEGORIES.items() if v[idx] & genre_ids]


def us_certification(d: dict) -> str | None:
    """Classificazione USA di un film (G, PG, PG-13, R...), None se non c'e'."""
    for rd in d.get("release_dates", {}).get("results", []):
        if rd.get("iso_3166_1") == "US":
            certs = [x.get("certification") for x in rd.get("release_dates", []) if x.get("certification")]
            if certs:
                return certs[0]
    return None


def us_tv_rating(d: dict) -> str | None:
    for r in (d.get("content_ratings") or {}).get("results", []):
        if r.get("iso_3166_1") == "US" and r.get("rating"):
            return r["rating"]
    return None


def is_kids(d: dict, kind: str) -> bool:
    """Titolo per bambini: stessi criteri della sezione "Bambini"."""
    genres = {g.get("id") for g in d.get("genres", [])}
    if kind == "movie":
        if not genres & {10751, 16} or genres & KIDS_MOVIE_EXCLUDED:
            return False
        cert = us_certification(d)
        return cert in KIDS_MOVIE_CERTS if cert else 10751 in genres
    if not genres & {10762, 10751, 16} or genres & KIDS_TV_EXCLUDED:
        return False
    rating = us_tv_rating(d)
    return rating in KIDS_TV_RATINGS if rating else 10762 in genres


def _photo(path: str | None, size: str = "w185") -> str | None:
    return f"{IMG}/{size}{path}" if path else None


def dedupe(ids: list[int]) -> list[int]:
    return list(dict.fromkeys(ids))


def unique_items(items: list[dict]) -> list[dict]:
    """Un titolo compare una sola volta, con tutte le sue piattaforme."""
    seen: dict[str, dict] = {}
    for it in items:
        if it["id"] in seen:
            for p in it["platforms"]:
                if p not in seen[it["id"]]["platforms"]:
                    seen[it["id"]]["platforms"].append(p)
        else:
            seen[it["id"]] = it
    return list(seen.values())
