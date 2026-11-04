"""Plex: controlla quali film e serie sono gia' nella libreria."""

import logging
import time
from urllib.parse import quote

import httpx

from .arr import describe_network_error

log = logging.getLogger("plex")

INDEX_TTL = 600  # secondi tra una lettura completa della libreria e l'altra


class PlexError(RuntimeError):
    pass


class Plex:
    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token
        self._index: dict | None = None
        self._index_at = 0.0
        self._machine: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.url, timeout=30,
            headers={"X-Plex-Token": self.token, "Accept": "application/json",
                     "X-Plex-Client-Identifier": "cosaguardo", "X-Plex-Product": "CosaGuardo"},
        )

    async def _get(self, client: httpx.AsyncClient, path: str, **params) -> dict:
        try:
            r = await client.get(path, params=params)
        except httpx.HTTPError as e:
            log.warning("GET %s%s non raggiungibile: %r", self.url, path, e)
            raise PlexError(f"Impossibile contattare Plex su {self.url}: {describe_network_error(e)}.") from e
        if r.status_code == 401:
            raise PlexError("Token di Plex non valido.")
        if r.status_code >= 400:
            raise PlexError(f"Plex ha risposto con un errore ({r.status_code}).")
        return r.json().get("MediaContainer", {})

    def _web_url(self, rating_key: str) -> str | None:
        if not self._machine:
            return None
        key = quote(f"/library/metadata/{rating_key}", safe="")
        return f"https://app.plex.tv/desktop/#!/server/{self._machine}/details?key={key}"

    async def index(self, force: bool = False) -> dict:
        """Libreria indicizzata per id TMDB e TVDB: {"movie": {...}, "tv": {...}, "tvdb": {...}}."""
        if self._index is not None and not force and time.monotonic() - self._index_at < INDEX_TTL:
            return self._index
        idx: dict = {"movie": {}, "tv": {}, "tvdb": {}}
        async with self._client() as client:
            self._machine = (await self._get(client, "/identity")).get("machineIdentifier")
            sections = (await self._get(client, "/library/sections")).get("Directory", [])
            for sec in sections:
                kind = {"movie": "movie", "show": "tv"}.get(sec.get("type"))
                if not kind:
                    continue
                items = (await self._get(client, f"/library/sections/{sec['key']}/all", includeGuids=1)).get("Metadata", [])
                for m in items:
                    entry = {
                        "rating_key": m.get("ratingKey"),
                        "titolo": m.get("title"),
                        "stagioni": m.get("childCount") if kind == "tv" else None,
                        "episodi": m.get("leafCount") if kind == "tv" else None,
                        "visti": m.get("viewedLeafCount") if kind == "tv" else (m.get("viewCount") or 0) > 0,
                        "url": self._web_url(m.get("ratingKey")),
                    }
                    for g in m.get("Guid", []):
                        scheme, _, value = str(g.get("id", "")).partition("://")
                        if not value.isdigit():
                            continue
                        if scheme == "tmdb":
                            idx[kind][int(value)] = entry
                        elif scheme == "tvdb" and kind == "tv":
                            idx["tvdb"][int(value)] = entry
        self._index, self._index_at = idx, time.monotonic()
        log.info("Libreria Plex letta: %d film, %d serie", len(idx["movie"]), len(idx["tv"]))
        return idx

    async def find(self, kind: str, tmdb_id: int, tvdb_id: int | None = None) -> dict | None:
        idx = await self.index()
        found = idx[kind].get(tmdb_id)
        if not found and kind == "tv" and tvdb_id:
            found = idx["tvdb"].get(tvdb_id)
        return found

    async def seasons(self, rating_key: str) -> dict[int, int]:
        """Episodi presenti su Plex per ogni stagione di una serie."""
        async with self._client() as client:
            items = (await self._get(client, f"/library/metadata/{rating_key}/children")).get("Metadata", [])
        return {int(s["index"]): s.get("leafCount", 0) for s in items if s.get("index") is not None}
