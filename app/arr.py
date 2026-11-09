"""Richieste: i film vanno a Radarr, le serie a Sonarr (API v3, compatibile con Sonarr v4)."""

import asyncio
import json
import logging
import os
import re
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


def describe_network_error(e: httpx.HTTPError) -> str:
    """Spiega in italiano perche' un servizio in rete locale non risponde."""
    if isinstance(e, httpx.ConnectTimeout):
        return ("nessuna risposta entro il tempo limite: l'indirizzo non e' raggiungibile dal container "
                "(firewall del NAS o rete Docker diversa)")
    if isinstance(e, httpx.ConnectError):
        # httpx riassume spesso con "All connection attempts failed": il motivo vero e' nella catena
        chain, cur = [], e
        while cur is not None and len(chain) < 8:
            chain.append(cur)
            chain.extend(getattr(cur, "exceptions", ()) or ())
            cur = cur.__cause__ or cur.__context__
        if any(isinstance(x, ConnectionRefusedError) for x in chain):
            return "connessione rifiutata: controlla porta e che il servizio sia avviato"
        if any(isinstance(x, TimeoutError) for x in chain):
            return ("nessuna risposta entro il tempo limite: l'indirizzo non e' raggiungibile dal container "
                    "(firewall del NAS o rete Docker diversa)")
        text = " ".join(str(x) for x in chain).lower()
        if "name or service" in text or "nodename" in text or "resolve" in text or "getaddrinfo" in text:
            return "nome host non trovato"
        if "refused" in text:
            return "connessione rifiutata: controlla porta e che il servizio sia avviato"
        if "unreachable" in text or "no route" in text:
            return "rete non raggiungibile dal container"
        return f"connessione non riuscita ({e or type(e).__name__})"
    if isinstance(e, httpx.TimeoutException):
        return "il servizio ha risposto troppo lentamente"
    if isinstance(e, httpx.UnsupportedProtocol):
        return "indirizzo non valido: deve iniziare con http:// o https://"
    if isinstance(e, httpx.ProxyError):
        return "errore del proxy (variabili HTTP_PROXY/HTTPS_PROXY nel container)"
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__


def _progress(records: list[dict]) -> dict:
    """Progresso complessivo di uno o piu' download in coda."""
    size = sum(r.get("size") or 0 for r in records)
    left = sum(r.get("sizeleft") or 0 for r in records)
    lefts = [r.get("timeleft") for r in records if r.get("timeleft")]
    states = {r.get("trackedDownloadState") or r.get("status") for r in records}
    return {
        "percentuale": round((size - left) / size * 100, 1) if size else 0,
        "download": len(records),
        "tempo_rimanente": max(lefts) if lefts else None,
        "in_pausa": states <= {"paused"},
        "in_attesa": states <= {"queued", "delay", "importPending", "downloadClientUnavailable"},
    }


class ArrClient:
    name = ""
    id_field = ""

    def __init__(self, url: str, api_key: str, quality_profile: str = "", root_folder: str = ""):
        self.url = url
        self.api_key = api_key
        self.quality_profile = quality_profile
        self.root_folder = root_folder
        # Valori della configurazione (variabili d'ambiente), usati se il menu admin non ne sceglie altri
        self.default_quality_profile = quality_profile
        self.default_root_folder = root_folder
        self.tags: list[int] = []  # id dei tag da applicare ai titoli aggiunti

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key)

    def _client(self) -> httpx.AsyncClient:
        # Connessione rapida da stabilire, ma risposte lente tollerate: /rootfolder calcola lo spazio
        # libero dei dischi e su un NAS puo' richiedere parecchi secondi.
        return httpx.AsyncClient(base_url=self.url, headers={"X-Api-Key": self.api_key},
                                 timeout=httpx.Timeout(60, connect=10))

    async def _req(self, method: str, path: str, **kw):
        if not self.configured:
            raise ArrError(f"{self.name} non e' configurato.")
        try:
            async with self._client() as client:
                r = await client.request(method, path, **kw)
        except httpx.HTTPError as e:
            reason = describe_network_error(e)
            log.warning("%s %s%s non raggiungibile: %r", method, self.url, path, e)
            raise ArrError(f"Impossibile contattare {self.name} su {self.url}: {reason}.") from e
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

    async def tag_list(self) -> list[dict]:
        return [{"id": t["id"], "nome": t.get("label", "")} for t in await self._req("GET", "/api/v3/tag") or []]

    async def create_tag(self, label: str) -> dict:
        """Crea un tag (o restituisce quello che esiste gia' con lo stesso nome)."""
        # Sonarr e Radarr accettano solo lettere minuscole, numeri e trattini
        label = re.sub(r"[^a-z0-9-]+", "-", label.strip().lower()).strip("-")
        if not label:
            raise ValueError("Scrivi un nome per il tag (lettere, numeri o trattini).")
        for t in await self.tag_list():
            if t["nome"].lower() == label:
                return t
        t = await self._req("POST", "/api/v3/tag", json={"label": label})
        log.info("Creato il tag \"%s\" in %s", label, self.name)
        return {"id": t["id"], "nome": t.get("label", label)}

    async def _tags_to_apply(self) -> list[int]:
        """I tag scelti nel menu admin che esistono ancora."""
        if not self.tags:
            return []
        existing = {t["id"] for t in await self.tag_list()}
        missing = set(self.tags) - existing
        if missing:
            log.warning("%s: tag %s non trovati, ignorati.", self.name, sorted(missing))
        return [t for t in self.tags if t in existing]

    async def options(self) -> dict:
        """Profili di qualita', cartelle e tag disponibili, per il menu admin."""
        profiles, roots, tags = await asyncio.gather(
            self._req("GET", "/api/v3/qualityprofile"), self._req("GET", "/api/v3/rootfolder"), self.tag_list(),
        )
        return {
            "profili_qualita": [{"id": p["id"], "nome": p.get("name", "")} for p in profiles or []],
            "cartelle": [r["path"] for r in roots or [] if r.get("path")],
            "tag": tags,
        }

    async def queue(self) -> dict[int, dict]:
        """Download in corso, raggruppati per film/serie (id interno di Radarr/Sonarr)."""
        res = await self._req("GET", "/api/v3/queue", params={"pageSize": 1000})
        records = res.get("records", []) if isinstance(res, dict) else res or []
        grouped: dict[int, list] = {}
        for r in records:
            if r.get(self.id_field):
                grouped.setdefault(r[self.id_field], []).append(r)
        return {k: _progress(v) for k, v in grouped.items()}


class Radarr(ArrClient):
    name = "Radarr"
    id_field = "movieId"

    @staticmethod
    def info(movie: dict) -> dict:
        file = movie.get("movieFile") or {}
        return {
            "stato": DISPONIBILE if movie.get("hasFile") else RICHIESTO,
            "arr_id": movie.get("id"),
            "dimensione": movie.get("sizeOnDisk") or file.get("size") or 0,
            "qualita": ((file.get("quality") or {}).get("quality") or {}).get("name"),
        }

    async def get(self, tmdb_id: int) -> dict | None:
        found = await self._req("GET", "/api/v3/movie", params={"tmdbId": tmdb_id})
        return self.info(found[0]) if found else None

    async def status(self, tmdb_id: int) -> str:
        found = await self.get(tmdb_id)
        return found["stato"] if found else ASSENTE

    async def library(self) -> dict[int, dict]:
        return {m["tmdbId"]: self.info(m) for m in await self._req("GET", "/api/v3/movie") or [] if m.get("tmdbId")}

    async def add(self, tmdb_id: int) -> str:
        current = await self.status(tmdb_id)
        if current != ASSENTE:
            return current
        movie = await self._req("GET", "/api/v3/movie/lookup/tmdb", params={"tmdbId": tmdb_id})
        if not movie:
            raise ArrError("Radarr non trova questo film.")
        movie.update(await self._defaults())
        movie.update({
            "tags": await self._tags_to_apply(),
            "monitored": True,
            "minimumAvailability": "released",
            "addOptions": {"searchForMovie": True},
        })
        await self._req("POST", "/api/v3/movie", json=movie)
        log.info("Film aggiunto a Radarr: %s (tmdb %s)", movie.get("title"), tmdb_id)
        return RICHIESTO


class Sonarr(ArrClient):
    name = "Sonarr"
    id_field = "seriesId"

    def __init__(self, *args, language_profile: str = "", **kw):
        super().__init__(*args, **kw)
        self.language_profile = language_profile

    @staticmethod
    def info(series: dict) -> dict:
        stats = series.get("statistics") or {}
        if not stats.get("episodeFileCount"):
            stato = RICHIESTO
        else:
            stato = DISPONIBILE if stats.get("percentOfEpisodes", 0) >= 100 else PARZIALE
        seasons = []
        for s in series.get("seasons", []):
            if s.get("seasonNumber", 0) == 0:
                continue  # speciali
            st = s.get("statistics") or {}
            seasons.append({
                "numero": s["seasonNumber"],
                "monitorata": bool(s.get("monitored")),
                "scaricati": st.get("episodeFileCount", 0),
                "previsti": st.get("episodeCount", 0),
                "totali": st.get("totalEpisodeCount", 0),
            })
        return {
            "stato": stato,
            "arr_id": series.get("id"),
            "stagioni": seasons,
            "episodi_scaricati": stats.get("episodeFileCount", 0),
            "episodi_previsti": stats.get("episodeCount", 0),
            "dimensione": stats.get("sizeOnDisk", 0),
        }

    async def _find(self, tvdb_id: int) -> dict | None:
        found = await self._req("GET", "/api/v3/series", params={"tvdbId": tvdb_id})
        return found[0] if found else None

    async def get(self, tvdb_id: int) -> dict | None:
        found = await self._find(tvdb_id)
        return self.info(found) if found else None

    async def status(self, tvdb_id: int) -> str:
        found = await self.get(tvdb_id)
        return found["stato"] if found else ASSENTE

    async def library(self) -> dict[int, dict]:
        return {s["tvdbId"]: self.info(s) for s in await self._req("GET", "/api/v3/series") or [] if s.get("tvdbId")}

    async def add(self, tvdb_id: int, seasons: list[int] | None = None) -> str:
        """Aggiunge la serie con le stagioni scelte (tutte se None).

        Se la serie c'e' gia', monitora le stagioni in piu' e ne avvia la ricerca.
        """
        existing = await self._find(tvdb_id)
        if existing:
            return await self._add_seasons(existing, seasons)

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
        for s in series.get("seasons", []):
            n = s.get("seasonNumber", 0)
            s["monitored"] = n > 0 and (seasons is None or n in seasons)
        series.update({
            "tags": await self._tags_to_apply(),
            "monitored": True,
            "seasonFolder": True,
            # Senza "monitor" Sonarr rispetta le stagioni monitorate scelte qui sopra
            "addOptions": {"searchForMissingEpisodes": True, "searchForCutoffUnmetEpisodes": False},
        })
        await self._req("POST", "/api/v3/series", json=series)
        log.info("Serie aggiunta a Sonarr: %s (tvdb %s, stagioni %s)", series.get("title"), tvdb_id, seasons or "tutte")
        return RICHIESTO

    async def _add_seasons(self, series: dict, seasons: list[int] | None) -> str:
        new = []
        for s in series.get("seasons", []):
            n = s.get("seasonNumber", 0)
            if n > 0 and (seasons is None or n in seasons) and not s.get("monitored"):
                s["monitored"] = True
                new.append(n)
        if not new:
            return self.info(series)["stato"]
        series["monitored"] = True
        await self._req("PUT", f"/api/v3/series/{series['id']}", json=series)
        for n in new:
            await self._req("POST", "/api/v3/command",
                            json={"name": "SeasonSearch", "seriesId": series["id"], "seasonNumber": n})
        log.info("Sonarr: aggiunte le stagioni %s a %s", new, series.get("title"))
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

    def add(self, item: dict, tvdb_id: int | None = None, seasons: list[int] | None = None) -> dict:
        existing = self.get(item["id"])
        if existing:
            # Nuove stagioni richieste per una serie gia' in elenco
            if item["type"] == "tv":
                old = existing.get("stagioni")
                existing["stagioni"] = None if seasons is None or old is None else sorted(set(old) | set(seasons))
                self._save()
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
            "stagioni": seasons if item["type"] == "tv" else None,
            "richiesto_il": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.items.insert(0, req)
        self._save()
        return req

    def remove(self, item_id: str) -> bool:
        """Toglie un titolo dall'elenco di CosaGuardo. Radarr, Sonarr e Plex non vengono toccati."""
        before = len(self.items)
        self.items = [r for r in self.items if r["id"] != item_id]
        if len(self.items) == before:
            return False
        self._save()
        return True

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.file), exist_ok=True)
            tmp = self.file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.items, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.file)
        except OSError as e:
            log.warning("Impossibile salvare le richieste: %s", e)
