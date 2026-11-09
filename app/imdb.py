"""Voti IMDb dal dataset pubblico ufficiale (title.ratings.tsv.gz, aggiornato ogni giorno).

Il voto di IMDb e' piu' affidabile di quello di TMDB perche' si basa su molti piu' voti.
Il file (circa 8 MB) viene scaricato in /data una volta al giorno e letto solo per gli id che servono.
"""

import asyncio
import gzip
import logging
import os
import time

import httpx

log = logging.getLogger("imdb")

RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
MAX_AGE = 20 * 3600  # secondi


class ImdbRatings:
    def __init__(self, data_dir: str, url: str = RATINGS_URL, enabled: bool = True):
        self.file = os.path.join(data_dir, "imdb-ratings.tsv.gz")
        self.url = url
        self.enabled = enabled
        # Voti gia' letti dal file (None = titolo non presente), validi finche' il file non cambia
        self._mem: dict[str, tuple[float, int] | None] = {}
        self._mem_mtime = 0.0

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=120, follow_redirects=True)

    def _fresh(self) -> bool:
        try:
            return time.time() - os.path.getmtime(self.file) < MAX_AGE
        except OSError:
            return False

    async def _download(self) -> None:
        tmp = self.file + ".tmp"
        os.makedirs(os.path.dirname(self.file), exist_ok=True)
        async with self._client() as client, client.stream("GET", self.url) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                async for chunk in r.aiter_bytes(1 << 16):
                    f.write(chunk)
        os.replace(tmp, self.file)
        log.info("Voti IMDb aggiornati (%d KB)", os.path.getsize(self.file) // 1024)

    def _read(self, wanted: set[str]) -> dict[str, tuple[float, int]]:
        out: dict[str, tuple[float, int]] = {}
        with gzip.open(self.file, "rt", encoding="utf-8") as f:
            next(f, None)  # intestazione
            for line in f:
                tconst, _, rest = line.partition("\t")
                if tconst in wanted:
                    rating, _, votes = rest.partition("\t")
                    out[tconst] = (float(rating), int(votes))
                    if len(out) == len(wanted):
                        break
        return out

    async def lookup(self, ids: set[str]) -> dict[str, tuple[float, int]]:
        """{"tt123": (voto, numero di voti)} per gli id richiesti; vuoto se IMDb non e' raggiungibile."""
        ids = {i for i in ids if i}
        if not self.enabled or not ids:
            return {}
        if not self._fresh():
            try:
                await self._download()
            except (httpx.HTTPError, OSError) as e:
                log.warning("Impossibile scaricare i voti IMDb: %s", e)
        if not os.path.exists(self.file):
            return {}
        mtime = os.path.getmtime(self.file)
        if mtime != self._mem_mtime:
            self._mem, self._mem_mtime = {}, mtime
        missing = ids - self._mem.keys()
        if missing:
            try:
                found = await asyncio.to_thread(self._read, missing)
            except (OSError, ValueError, EOFError) as e:
                log.warning("File dei voti IMDb non leggibile: %s", e)
                return {}
            if len(self._mem) > 50_000:
                self._mem = {}
            for i in missing:
                self._mem[i] = found.get(i)
        return {i: v for i in ids if (v := self._mem.get(i))}
