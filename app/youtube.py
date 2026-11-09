"""Ricerca su YouTube del trailer doppiato in italiano.

TMDB raramente ha i trailer italiani (gli utenti caricano quasi solo quelli
originali), mentre su YouTube i distributori italiani li pubblicano quasi
sempre. La pagina dei risultati di YouTube contiene i dati in JSON
(ytInitialData), quindi non serve una chiave API.
"""

import json
import logging
import os
import re
import unicodedata

import httpx

log = logging.getLogger("youtube")

YOUTUBE_URL = os.getenv("YOUTUBE_URL", "https://www.youtube.com").rstrip("/")
_DATA = re.compile(r"(?:var ytInitialData|window\[\"ytInitialData\"\])\s*=\s*(\{.+?\});\s*</script>", re.S)
_SUB = re.compile(r"\bsub(?:titled|bed|s)?\b[\s\-.]*(?:ita|italiano)\b|\bsottotitol")
_ITA = re.compile(r"\b(?:ita|italiano|italiana|doppiato)\b")
_TRAILER = re.compile(r"\b(?:trailer|teaser)\b")
_NOT = re.compile(r"\b(?:reaction|recensione|review|analisi|spiegazione|fan ?made|parodia|scena|clip|making of)\b")


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _seconds(length: str) -> int:
    total = 0
    for part in (length or "").split(":"):
        total = total * 60 + (int(part) if part.isdigit() else 0)
    return total


def _videos(data) -> list[dict]:
    """Tutti i videoRenderer della pagina dei risultati, in ordine."""
    out: list[dict] = []
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            v = node.get("videoRenderer")
            if isinstance(v, dict) and v.get("videoId"):
                title = "".join(r.get("text", "") for r in (v.get("title") or {}).get("runs", []))
                channel = "".join(r.get("text", "") for r in (v.get("ownerText") or {}).get("runs", []))
                length = (v.get("lengthText") or {}).get("simpleText", "")
                out.append({"key": v["videoId"], "name": title, "canale": channel, "durata": _seconds(length)})
                continue
            stack.extend(reversed(list(node.values())))
        elif isinstance(node, list):
            stack.extend(reversed(node))
    return out


def pick(videos: list[dict], titles: list[str]) -> dict | None:
    """Il primo risultato che e' davvero il trailer italiano di quel titolo.

    Doppiato in italiano prima dei sottotitolati; scarta recensioni, clip e
    video lunghi, e i risultati che non nominano il titolo cercato.
    """
    names = [n for n in {norm(t) for t in titles if t} if n]
    best, best_score = None, 0
    for v in videos:
        name = norm(v["name"])
        if not _TRAILER.search(name) or _NOT.search(name):
            continue
        if not v["durata"] or v["durata"] > 300:
            continue
        if not any(f" {n} " in f" {name} " for n in names):
            continue
        if _SUB.search(name):
            score = 1
        elif _ITA.search(name) or "trailer ufficiale" in name or "teaser ufficiale" in name:
            score = 2
        else:
            continue
        if score > best_score:
            best, best_score = v, score
            if score == 2:
                break
    if not best:
        return None
    return {"key": best["key"], "name": best["name"], "lingua": "it", "sottotitoli": best_score == 1}


class YouTube:
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=YOUTUBE_URL, timeout=15, follow_redirects=True,
            headers={"Accept-Language": "it-IT,it;q=0.9",
                     "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/124.0 Safari/537.36"},
            # Evita la pagina di consenso ai cookie servita in Europa
            cookies={"CONSENT": "YES+cb", "SOCS": "CAI"},
        )

    async def trailer_ita(self, titles: list[str], kind: str, year: str | None = None) -> dict | None:
        main = next((t for t in titles if t), "")
        if not main:
            return None
        query = f"{main} {'serie' if kind == 'tv' else ''} trailer italiano {year or ''}"
        try:
            async with self._client() as client:
                r = await client.get("/results", params={"search_query": " ".join(query.split()), "hl": "it", "gl": "IT"})
                r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("Ricerca YouTube non riuscita: %s", e)
            return None
        m = _DATA.search(r.text)
        if not m:
            return None
        try:
            data = json.loads(m.group(1))
        except ValueError:
            return None
        return pick(_videos(data), titles)
