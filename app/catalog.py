"""Raccolta delle ultime uscite e dei titoli di tendenza da TMDB.

TMDB riceve i dati sulla disponibilita' nelle piattaforme da JustWatch, quindi
le piattaforme indicate sono quelle del catalogo italiano (watch_region=IT).
"""

import asyncio
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

import httpx

from .config import PLATFORMS, Settings

log = logging.getLogger("catalog")

IMG = os.getenv("TMDB_IMAGE_URL", "https://image.tmdb.org/t/p")
MONETIZATION = "flatrate|free|ads"


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
        self._sem = asyncio.Semaphore(8)
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

    async def _discover(self, client, kind: str, pages: int, **params) -> list[int]:
        ids = ",".join(str(i) for i in self._provider_map) or "0"
        base = {
            "watch_region": self.s.region,
            "with_watch_providers": ids,
            "with_watch_monetization_types": MONETIZATION,
            "include_adult": "false",
            **params,
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
            extra = "watch/providers,release_dates" if kind == "movie" else "watch/providers"
            d = await self._get(client, f"/{kind}/{tmdb_id}", append_to_response=extra)
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
        return {
            "id": f"{kind}-{d['id']}",
            "tmdb_id": d["id"],
            "type": kind,
            "overview": d.get("overview") or "",
            "poster": f"{IMG}/w342{d['poster_path']}" if d.get("poster_path") else None,
            "backdrop": f"{IMG}/w780{d['backdrop_path']}" if d.get("backdrop_path") else None,
            "genres": [g["name"] for g in d.get("genres", [])],
            "rating": round(d.get("vote_average") or 0, 1),
            "votes": d.get("vote_count") or 0,
            "popularity": d.get("popularity") or 0,
            "platforms": platforms,
            "watch_link": link,
            "tmdb_url": f"https://www.themoviedb.org/{kind}/{d['id']}",
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

        tendenze = await self._trending(client, today)

        return {
            "novita": unique_items(novita),
            "tendenze": tendenze,
            "piattaforme": piattaforme,
            "aggiornato": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

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
        if not ts:
            return True
        age = datetime.now(timezone.utc) - datetime.fromisoformat(ts)
        return age > timedelta(minutes=self.s.refresh_minutes)


def match_platform(provider_name: str, enabled: list[str]) -> str | None:
    name = provider_name.lower() + " "
    for key in enabled:
        if any(m in name for m in PLATFORMS[key]["match"]):
            return key
    return None


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
