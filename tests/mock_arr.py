"""Finti Radarr e Sonarr (API v3) per provare le richieste.

Avvio:  uvicorn tests.mock_arr:radarr --port 7878  e  uvicorn tests.mock_arr:sonarr --port 8989
Chiave API: "test".
"""

import os

from fastapi import FastAPI, Header, HTTPException, Request

KEY = "test"


def _check(key: str | None):
    if key != KEY:
        raise HTTPException(401)


def _common(app: FastAPI):
    @app.get("/api/v3/qualityprofile")
    def profiles(x_api_key: str = Header(None)):
        _check(x_api_key)
        return [{"id": 1, "name": "Any"}, {"id": 4, "name": "HD-1080p"}]

    @app.get("/api/v3/queue")
    def queue(x_api_key: str = Header(None)):
        _check(x_api_key)
        records = list(app.state.queue)
        if os.getenv("MOCK_DEMO"):
            # Modalita' dimostrativa: tutto cio' che e' stato aggiunto risulta in download
            items = getattr(app.state, "movies", None) or getattr(app.state, "series", None) or []
            field = "movieId" if hasattr(app.state, "movies") else "seriesId"
            for n, it in enumerate(items):
                records.append({field: it["id"], "size": 4_000_000_000, "sizeleft": 4_000_000_000 * (0.2 + 0.25 * (n % 3)),
                                "timeleft": f"00:{12 + 9 * n:02d}:00", "status": "downloading",
                                "trackedDownloadState": "downloading"})
        return {"page": 1, "totalRecords": len(records), "records": records}

    @app.get("/api/v3/rootfolder")
    def roots(x_api_key: str = Header(None)):
        _check(x_api_key)
        return [{"id": 1, "path": "/media/principale"}, {"id": 2, "path": "/media/altro"}]


radarr = FastAPI()
_common(radarr)
radarr.state.movies = []
radarr.state.queue = []


@radarr.get("/api/v3/movie")
def movies(tmdbId: int | None = None, x_api_key: str = Header(None)):
    _check(x_api_key)
    return [m for m in radarr.state.movies if tmdbId is None or m["tmdbId"] == tmdbId]


@radarr.get("/api/v3/movie/lookup/tmdb")
def movie_lookup(tmdbId: int, x_api_key: str = Header(None)):
    _check(x_api_key)
    return {"title": f"Film {tmdbId}", "tmdbId": tmdbId, "year": 2026, "titleSlug": f"film-{tmdbId}", "images": []}


@radarr.post("/api/v3/movie", status_code=201)
async def add_movie(request: Request, x_api_key: str = Header(None)):
    _check(x_api_key)
    body = await request.json()
    for f in ("qualityProfileId", "rootFolderPath", "tmdbId", "title"):
        if f not in body:
            raise HTTPException(400, f"manca {f}")
    if any(m["tmdbId"] == body["tmdbId"] for m in radarr.state.movies):
        raise HTTPException(400, "gia' presente")
    body |= {"id": len(radarr.state.movies) + 1, "hasFile": False}
    radarr.state.movies.append(body)
    return body


sonarr = FastAPI()
_common(sonarr)
sonarr.state.series = []
sonarr.state.queue = []
sonarr.state.commands = []


@sonarr.get("/api/v3/languageprofile")
def language_profiles(x_api_key: str = Header(None)):
    _check(x_api_key)
    return [{"id": 1, "name": "Italian"}]


@sonarr.get("/api/v3/series")
def series(tvdbId: int | None = None, x_api_key: str = Header(None)):
    _check(x_api_key)
    return [s for s in sonarr.state.series if tvdbId is None or s["tvdbId"] == tvdbId]


@sonarr.get("/api/v3/series/lookup")
def series_lookup(term: str, x_api_key: str = Header(None)):
    _check(x_api_key)
    tvdb = int(term.split(":")[1])
    seasons = [{"seasonNumber": n, "monitored": False} for n in range(0, 4)]
    return [{"title": f"Serie {tvdb}", "tvdbId": tvdb, "year": 2026, "titleSlug": f"serie-{tvdb}", "seasons": seasons}]


@sonarr.post("/api/v3/series", status_code=201)
async def add_series(request: Request, x_api_key: str = Header(None)):
    _check(x_api_key)
    body = await request.json()
    for f in ("qualityProfileId", "rootFolderPath", "tvdbId", "languageProfileId"):
        if f not in body:
            raise HTTPException(400, f"manca {f}")
    if "monitor" in body.get("addOptions", {}):
        raise HTTPException(400, "con monitor le stagioni scelte verrebbero ignorate")
    for season in body["seasons"]:
        season["statistics"] = {"episodeFileCount": 0, "episodeCount": 8 if season["monitored"] else 0,
                                "totalEpisodeCount": 8}
    body |= {"id": len(sonarr.state.series) + 1,
             "statistics": {"episodeFileCount": 0, "episodeCount": 8, "percentOfEpisodes": 0}}
    sonarr.state.series.append(body)
    return body


@sonarr.put("/api/v3/series/{sid}")
async def update_series(sid: int, request: Request, x_api_key: str = Header(None)):
    _check(x_api_key)
    body = await request.json()
    sonarr.state.series = [body if s["id"] == sid else s for s in sonarr.state.series]
    return body


@sonarr.post("/api/v3/command", status_code=201)
async def command(request: Request, x_api_key: str = Header(None)):
    _check(x_api_key)
    sonarr.state.commands.append(await request.json())
    return {"id": len(sonarr.state.commands)}


# ---------------------------------------------------------------- Plex

plex = FastAPI()
PLEX_TOKEN = "plex-test"
plex.state.library = {
    "movie": [{"ratingKey": "11", "title": "La notte di Roma", "Guid": [{"id": "tmdb://1002"}], "viewCount": 1}],
    "show": [{"ratingKey": "21", "title": "Serie su Plex", "childCount": 2, "leafCount": 14, "viewedLeafCount": 3,
              "Guid": [{"id": "tvdb://902003"}, {"id": "tmdb://2003"}]}],
}


def _plex(token: str | None):
    if token != PLEX_TOKEN:
        raise HTTPException(401)


@plex.get("/identity")
def identity(x_plex_token: str = Header(None)):
    _plex(x_plex_token)
    return {"MediaContainer": {"machineIdentifier": "abc123"}}


@plex.get("/library/sections")
def sections(x_plex_token: str = Header(None)):
    _plex(x_plex_token)
    return {"MediaContainer": {"Directory": [{"key": "1", "type": "movie"}, {"key": "2", "type": "show"},
                                             {"key": "3", "type": "artist"}]}}


@plex.get("/library/sections/{key}/all")
def section_all(key: str, x_plex_token: str = Header(None)):
    _plex(x_plex_token)
    return {"MediaContainer": {"Metadata": plex.state.library["movie" if key == "1" else "show"]}}


@plex.get("/library/metadata/{rk}/children")
def children(rk: str, x_plex_token: str = Header(None)):
    _plex(x_plex_token)
    return {"MediaContainer": {"Metadata": [{"index": 1, "leafCount": 8}, {"index": 2, "leafCount": 6}]}}
