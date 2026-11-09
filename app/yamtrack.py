"""Film e serie gia' visti, letti da Yamtrack (tracker self-hosted).

Yamtrack non ha un'API pubblica per leggere la propria libreria: CosaGuardo entra con utente e
password (come dal browser) e scarica l'esportazione CSV di Impostazioni > Esporta.
Si usano solo le righe con fonte TMDB (film, serie, stagioni ed episodi).
"""

import csv
import io
import logging
import re
import time

import httpx

log = logging.getLogger("yamtrack")

CACHE_SECONDS = 3600
CSRF_RE = re.compile(r'name=["\']csrfmiddlewaretoken["\']\s+value=["\']([^"\']+)')


class YamtrackError(RuntimeError):
    pass


def parse_export(text: str) -> dict[str, dict]:
    """{"movie-603": {"titolo", "stato", "voto"}, "tv-1396": {...}} dal CSV di Yamtrack.

    Stagioni ed episodi contano come la serie; lo stato della serie vince su quello delle stagioni.
    Il voto di Yamtrack e' da 0 a 10.
    """
    out: dict[str, dict] = {}
    for row in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        if (row.get("source") or "").strip() != "tmdb":
            continue
        mtype = (row.get("media_type") or "").strip()
        media_id = (row.get("media_id") or "").strip()
        if mtype not in ("movie", "tv", "season", "episode") or not media_id.isdigit():
            continue
        kind = "movie" if mtype == "movie" else "tv"
        key = f"{kind}-{media_id}"
        try:
            score = float(row.get("score") or "")
        except ValueError:
            score = None
        status = (row.get("status") or "").strip() or "Completed"
        title = (row.get("title") or "").strip()
        if mtype in ("movie", "tv"):
            out[key] = {"titolo": title, "stato": status, "voto": score}
        elif key not in out:
            # Solo stagioni o episodi: la serie e' almeno iniziata
            out[key] = {"titolo": title, "stato": "In progress", "voto": None}
    return out


class Yamtrack:
    def __init__(self, url: str = "", user: str = "", password: str = ""):
        self.url, self.user, self.password = url.strip().rstrip("/"), user.strip(), password
        self.error: str | None = None
        self._cache: dict[str, dict] | None = None
        self._ts = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.url and self.user and self.password)

    def configure(self, url: str, user: str, password: str) -> None:
        self.url, self.user, self.password = url.strip().rstrip("/"), user.strip(), password
        self._cache, self._ts, self.error = None, 0.0, None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.url, follow_redirects=True, timeout=httpx.Timeout(60, connect=10))

    async def _download(self) -> str:
        try:
            async with self._client() as client:
                page = await client.get("/accounts/login/")
                page.raise_for_status()
                m = CSRF_RE.search(page.text)
                token = m.group(1) if m else client.cookies.get("csrftoken", "")
                await client.post("/accounts/login/", headers={"Referer": str(page.url)}, data={
                    "csrfmiddlewaretoken": token, "login": self.user, "password": self.password,
                })
                r = await client.get("/export/csv")
        except httpx.HTTPError as e:
            raise YamtrackError(f"Yamtrack non raggiungibile ({self.url}): {e}") from e
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or "text/html" in ctype or "media_id" not in r.text[:500]:
            raise YamtrackError("Accesso a Yamtrack non riuscito: controlla indirizzo, utente e password.")
        return r.text

    async def watched(self, force: bool = False) -> dict[str, dict]:
        """I titoli presenti in Yamtrack (letti al massimo una volta l'ora)."""
        if not self.configured:
            return {}
        if not force and self._cache is not None and time.monotonic() - self._ts < CACHE_SECONDS:
            return self._cache
        try:
            self._cache = parse_export(await self._download())
            self.error = None
            log.info("Yamtrack: %d film e serie letti", len(self._cache))
        except YamtrackError as e:
            self.error = str(e)
            log.warning("%s", e)
            if self._cache is None:
                raise
        self._ts = time.monotonic()
        return self._cache
