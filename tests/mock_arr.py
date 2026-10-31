"""Finti Radarr e Sonarr (API v3) per provare le richieste.

Avvio:  uvicorn tests.mock_arr:radarr --port 7878  e  uvicorn tests.mock_arr:sonarr --port 8989
Chiave API: "test".
"""

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

    @app.get("/api/v3/rootfolder")
    def roots(x_api_key: str = Header(None)):
        _check(x_api_key)
        return [{"id": 1, "path": "/media/principale"}, {"id": 2, "path": "/media/altro"}]


radarr = FastAPI()
_common(radarr)
radarr.state.movies = []


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
    return [{"title": f"Serie {tvdb}", "tvdbId": tvdb, "year": 2026, "titleSlug": f"serie-{tvdb}", "seasons": []}]


@sonarr.post("/api/v3/series", status_code=201)
async def add_series(request: Request, x_api_key: str = Header(None)):
    _check(x_api_key)
    body = await request.json()
    for f in ("qualityProfileId", "rootFolderPath", "tvdbId", "languageProfileId"):
        if f not in body:
            raise HTTPException(400, f"manca {f}")
    body |= {"id": len(sonarr.state.series) + 1, "statistics": {"episodeFileCount": 0, "percentOfEpisodes": 0}}
    sonarr.state.series.append(body)
    return body
