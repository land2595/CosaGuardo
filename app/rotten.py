"""Voti Rotten Tomatoes (Tomatometer della critica) presi da OMDb.

TMDB non ha i voti di Rotten Tomatoes. OMDb (omdbapi.com) li restituisce cercando per id IMDb;
la chiave gratuita permette 1000 richieste al giorno, quindi i voti vengono salvati in
/data/rotten-tomatoes.json e richiesti di nuovo solo dopo qualche giorno.

OMDb ha il Tomatometer quasi sempre per i film e solo di rado per le serie: per i titoli
senza voto Rotten Tomatoes l'app usa il voto IMDb.
"""

import asyncio
import json
import logging
import os
import time
from datetime import date

import httpx

log = logging.getLogger("rotten")

OMDB_URL = "https://www.omdbapi.com/"
DAY = 86400


def parse_tomatometer(data: dict) -> int | None:
    """93 da {"Ratings": [{"Source": "Rotten Tomatoes", "Value": "93%"}]}."""
    for r in data.get("Ratings") or []:
        if r.get("Source") == "Rotten Tomatoes":
            try:
                return int(str(r.get("Value", "")).strip().rstrip("%"))
            except ValueError:
                return None
    return None


class OmdbError(RuntimeError):
    pass


class RottenTomatoes:
    def __init__(self, data_dir: str, api_key: str = "", daily_limit: int = 900, url: str | None = None):
        self.file = os.path.join(data_dir, "rotten-tomatoes.json")
        self.api_key = api_key.strip()
        self.daily_limit = daily_limit
        self.url = url or os.getenv("OMDB_URL", OMDB_URL)
        self.error: str | None = None
        self.data: dict = {"voti": {}, "giorno": "", "usate": 0}
        try:
            with open(self.file, encoding="utf-8") as f:
                self.data |= json.load(f)
        except (OSError, ValueError):
            pass

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def used_today(self) -> int:
        return self.data["usate"] if self.data.get("giorno") == date.today().isoformat() else 0

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=15)

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.file), exist_ok=True)
            tmp = self.file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
            os.replace(tmp, self.file)
        except OSError as e:
            log.warning("Impossibile salvare i voti Rotten Tomatoes: %s", e)

    def _count(self) -> None:
        today = date.today().isoformat()
        if self.data.get("giorno") != today:
            self.data["giorno"], self.data["usate"] = today, 0
        self.data["usate"] += 1

    @staticmethod
    def _ttl(rt: int | None, year: int) -> int:
        # Titoli recenti: il voto cambia ancora, si ricontrolla piu' spesso
        recent = year >= date.today().year - 1
        if rt is None:
            return (3 if recent else 20) * DAY
        return (7 if recent else 45) * DAY

    def cached(self, imdb_id: str) -> int | None:
        return (self.data["voti"].get(imdb_id) or {}).get("rt")

    async def _fetch(self, client: httpx.AsyncClient, imdb_id: str, key: str | None = None) -> int | None:
        r = await client.get(self.url, params={"apikey": key or self.api_key, "i": imdb_id, "tomatoes": "true"})
        if r.status_code == 401:
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            raise OmdbError(body.get("Error") or "Chiave OMDb non valida.")
        r.raise_for_status()
        body = r.json()
        if body.get("Response") == "False":
            err = body.get("Error", "")
            if "limit" in err.lower() or "invalid api key" in err.lower() or "no api key" in err.lower():
                raise OmdbError(err)
            return None  # titolo non trovato su OMDb
        return parse_tomatometer(body)

    async def verify(self, key: str) -> None:
        """Prova la chiave con un titolo noto (Le ali della liberta')."""
        async with self._client() as client:
            try:
                await self._fetch(client, "tt0111161", key)
            except OmdbError as e:
                msg = str(e)
                if "invalid" in msg.lower() or "no api key" in msg.lower() or "non valida" in msg.lower():
                    raise OmdbError("Chiave OMDb non valida: controlla di averla attivata dal link nella mail.") from e
                raise
            except httpx.HTTPError as e:
                raise OmdbError(f"OMDb non raggiungibile: {e}") from e

    async def lookup(self, wanted: list[tuple[str, int]]) -> dict[str, int]:
        """{"tt123": 93} per gli id (in ordine di priorita', con l'anno di uscita) che hanno il Tomatometer.

        Prima usa i voti salvati; quelli mancanti o vecchi li chiede a OMDb finche' resta
        budget giornaliero, partendo dai primi della lista.
        """
        now = time.time()
        votes = self.data["voti"]
        todo = []
        for imdb_id, year in wanted:
            v = votes.get(imdb_id)
            if not v or now - v.get("ts", 0) > self._ttl(v.get("rt"), year):
                todo.append(imdb_id)
        if self.configured and todo:
            budget = max(0, self.daily_limit - self.used_today)
            todo = todo[:budget]
            sem = asyncio.Semaphore(5)
            stop = False

            async def one(client, imdb_id):
                nonlocal stop
                if stop:
                    return
                async with sem:
                    if stop:
                        return
                    try:
                        rt = await self._fetch(client, imdb_id)
                    except OmdbError as e:
                        stop = True
                        self.error = str(e)
                        if "limit" in str(e).lower():
                            # Quota del giorno finita: si riprova domani
                            self.data["giorno"], self.data["usate"] = date.today().isoformat(), self.daily_limit
                        log.warning("OMDb: %s", e)
                        return
                    except (httpx.HTTPError, ValueError) as e:
                        log.warning("OMDb non risponde per %s: %s", imdb_id, e)
                        return
                    self._count()
                    votes[imdb_id] = {"rt": rt, "ts": now}

            async with self._client() as client:
                await asyncio.gather(*(one(client, i) for i in todo))
            if not stop:
                self.error = None
            self._save()
            if todo:
                log.info("Voti Rotten Tomatoes aggiornati: %d richieste a OMDb oggi", self.used_today)
        return {i: votes[i]["rt"] for i, _ in wanted if (votes.get(i) or {}).get("rt") is not None}
