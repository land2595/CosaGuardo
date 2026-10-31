"""Richieste: i film vanno a Radarr, le serie a Sonarr (API v3, compatibile con Sonarr v4)."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import httpx

log = logging.getLogger("arr")

# Stati di un titolo rispetto alla libreria
ASSENTE = "assente"          # non ancora richiesto
RICHIESTO = "richiesto"      # aggiunto, in attesa del download
PARZIALE = "parziale"        # serie con solo alcuni episodi scaricati
DISPONIBILE = "disponibile"  # scaricato
SCONOSCIUTO = "sconosciuto"  # servizio non raggiungibile


class ArrError(RuntimeError):
    pass


class ArrClient:
    name = ""

    def __init__(self, url: str, api_key: str, quality_profile: str = "", root_folder: str = ""):
        self.url = url
        self.api_key = api_key
        self.quality_profile = quality_profile
        self.root_folder = root_folder

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.url, headers={"X-Api-Key": self.api_key}, timeout=20)

    async def _req(self, method: str, path: str, **kw):
        if not self.configured:
            raise ArrError(f"{self.name} non e' configurato.")
        try:
            async with self._client() as client:
                r = await client.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise ArrError(f"Impossibile contattare {self.name} su {self.url}.") from e
        if r.status_code == 401:
            raise ArrError(f"Chiave API di {self.name} non valida.")
        if r.status_code >= 400:
            try:
                body = r.json()
                detail = body[0].get("errorMessage") if isinstance(body, list) else body.get("message")
            except ValueError:
                detail = None
            raise ArrError(f"{self.name} ha risposto con un errore ({r.status_code}){': ' + detail if detail else ''}.")
        return r.json() if r.content else None

    async def _pick(self, path: str, wanted: str, label: str, key: str = "name") -> dict:
        """Sceglie per nome o id l'elemento configurato, altrimenti il primo disponibile."""
        items = await self._req("GET", path) or []
        if not items:
            raise ArrError(f"In {self.name} non c'e' nessun {label}.")
        if wanted:
            for it in items:
                if str(it.get("id")) == wanted or str(it.get(key, "")).lower() == wanted.lower():
                    return it
            raise ArrError(f"{label.capitalize()} \"{wanted}\" non trovato in {self.name}.")
        return items[0]

    async def _defaults(self) -> dict:
        profile, root = await asyncio.gather(
            self._pick("/api/v3/qualityprofile", self.quality_profile, "profilo di qualita'"),
            self._pick("/api/v3/rootfolder", self.root_folder, "cartella principale", key="path"),
        )
        return {"qualityProfileId": profile["id"], "rootFolderPath": root["path"]}


class Radarr(ArrClient):
    name = "Radarr"

    @staticmethod
    def _state(movie: dict) -> str:
        return DISPONIBILE if movie.get("hasFile") else RICHIESTO

    async def status(self, tmdb_id: int) -> str:
        found = await self._req("GET", "/api/v3/movie", params={"tmdbId": tmdb_id})
        return self._state(found[0]) if found else ASSENTE

    async def library(self) -> dict[int, str]:
        return {m["tmdbId"]: self._state(m) for m in await self._req("GET", "/api/v3/movie") or [] if m.get("tmdbId")}

    async def add(self, tmdb_id: int) -> str:
        current = await self.status(tmdb_id)
        if current != ASSENTE:
            return current
        movie = await self._req("GET", "/api/v3/movie/lookup/tmdb", params={"tmdbId": tmdb_id})
        if not movie:
            raise ArrError("Radarr non trova questo film.")
        movie.update(await self._defaults())
        movie.update({
            "monitored": True,
            "minimumAvailability": "released",
            "addOptions": {"searchForMovie": True},
        })
        await self._req("POST", "/api/v3/movie", json=movie)
        log.info("Film aggiunto a Radarr: %s (tmdb %s)", movie.get("title"), tmdb_id)
        return RICHIESTO


class Sonarr(ArrClient):
    name = "Sonarr"

    def __init__(self, *args, language_profile: str = "", **kw):
        super().__init__(*args, **kw)
        self.language_profile = language_profile

    @staticmethod
    def _state(series: dict) -> str:
        stats = series.get("statistics") or {}
        if not stats.get("episodeFileCount"):
            return RICHIESTO
        return DISPONIBILE if stats.get("percentOfEpisodes", 0) >= 100 else PARZIALE

    async def status(self, tvdb_id: int) -> str:
        found = await self._req("GET", "/api/v3/series", params={"tvdbId": tvdb_id})
        return self._state(found[0]) if found else ASSENTE

    async def library(self) -> dict[int, str]:
        return {s["tvdbId"]: self._state(s) for s in await self._req("GET", "/api/v3/series") or [] if s.get("tvdbId")}

    async def add(self, tvdb_id: int) -> str:
        current = await self.status(tvdb_id)
        if current != ASSENTE:
            return current
        found = await self._req("GET", "/api/v3/series/lookup", params={"term": f"tvdb:{tvdb_id}"})
        if not found:
            raise ArrError("Sonarr non trova questa serie.")
        series = found[0]
        series.update(await self._defaults())
        # Sonarr v3 vuole anche il profilo lingua; Sonarr v4 non lo usa piu'
        try:
            lang = await self._pick("/api/v3/languageprofile", self.language_profile, "profilo lingua")
            series["languageProfileId"] = lang["id"]
        except ArrError:
            pass
        series.update({
            "monitored": True,
            "seasonFolder": True,
            "addOptions": {"monitor": "all", "searchForMissingEpisodes": True, "searchForCutoffUnmetEpisodes": False},
        })
        await self._req("POST", "/api/v3/series", json=series)
        log.info("Serie aggiunta a Sonarr: %s (tvdb %s)", series.get("title"), tvdb_id)
        return RICHIESTO


class Requests:
    """Elenco delle richieste fatte dall'app, salvato in /data/richieste.json."""

    def __init__(self, data_dir: str):
        self.file = os.path.join(data_dir, "richieste.json")
        self.items: list[dict] = []
        try:
            with open(self.file, encoding="utf-8") as f:
                self.items = json.load(f)
        except (OSError, ValueError):
            pass

    def get(self, item_id: str) -> dict | None:
        return next((r for r in self.items if r["id"] == item_id), None)

    def add(self, item: dict, tvdb_id: int | None = None) -> dict:
        existing = self.get(item["id"])
        if existing:
            return existing
        req = {
            "id": item["id"],
            "type": item["type"],
            "tmdb_id": item["tmdb_id"],
            "tvdb_id": tvdb_id,
            "title": item["title"],
            "year": item.get("year"),
            "poster": item.get("poster"),
            "backdrop": item.get("backdrop"),
            "platforms": item.get("platforms", []),
            "richiesto_il": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.items.insert(0, req)
        self._save()
        return req

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.file), exist_ok=True)
            tmp = self.file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.items, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.file)
        except OSError as e:
            log.warning("Impossibile salvare le richieste: %s", e)
