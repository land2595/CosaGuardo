import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.arr import ArrError, Radarr, Requests, Sonarr
from app.catalog import Catalog
from app.config import Settings
from tests import mock_arr, mock_tmdb


def _mock_client(app, **kw):
    return lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mock", **kw)


@pytest.fixture
def services(tmp_path, monkeypatch):
    mock_arr.radarr.state.movies = []
    mock_arr.sonarr.state.series = []
    s = Settings()
    s.tmdb_api_key = "test"
    s.data_dir = str(tmp_path)
    cat = Catalog(s)
    cat._client = _mock_client(mock_tmdb.app)
    asyncio.run(cat.refresh())

    radarr = Radarr("http://radarr", "test", quality_profile="HD-1080p")
    radarr._client = _mock_client(mock_arr.radarr, headers={"X-Api-Key": "test"})
    sonarr = Sonarr("http://sonarr", "test")
    sonarr._client = _mock_client(mock_arr.sonarr, headers={"X-Api-Key": "test"})

    monkeypatch.setattr(main, "catalog", cat)
    monkeypatch.setattr(main, "radarr", radarr)
    monkeypatch.setattr(main, "sonarr", sonarr)
    monkeypatch.setattr(main, "requests", Requests(str(tmp_path)))
    return TestClient(main.app)


def test_trailer_prefers_italian(services):
    r = services.get("/api/trailer/movie/1001").json()
    assert r["trailer"]["key"] == "aqz-KE-bpKQ" and r["trailer"]["lingua"] == "it"
    assert services.get("/api/trailer/movie/1005").json()["trailer"] is None


def test_request_movie_goes_to_radarr(services):
    assert services.get("/api/servizi").json() == {"radarr": True, "sonarr": True}
    assert services.get("/api/richieste/movie-1001").json()["stato"] == "assente"

    r = services.post("/api/richieste", json={"id": "movie-1001"})
    assert r.status_code == 200 and r.json() == {"stato": "richiesto", "servizio": "Radarr"}
    added = mock_arr.radarr.state.movies[0]
    assert added["tmdbId"] == 1001 and added["qualityProfileId"] == 4
    assert added["rootFolderPath"] == "/media/principale" and added["addOptions"]["searchForMovie"]

    # Una seconda richiesta non duplica il film
    assert services.post("/api/richieste", json={"id": "movie-1001"}).json()["stato"] == "richiesto"
    assert len(mock_arr.radarr.state.movies) == 1

    added["hasFile"] = True
    lista = services.get("/api/richieste").json()["richieste"]
    assert [(x["id"], x["stato"]) for x in lista] == [("movie-1001", "disponibile")]


def test_request_series_goes_to_sonarr(services):
    r = services.post("/api/richieste", json={"id": "tv-2001"})
    assert r.json() == {"stato": "richiesto", "servizio": "Sonarr"}
    added = mock_arr.sonarr.state.series[0]
    assert added["tvdbId"] == 902001 and added["languageProfileId"] == 1
    assert added["addOptions"]["searchForMissingEpisodes"]

    added["statistics"] = {"episodeFileCount": 3, "percentOfEpisodes": 50}
    assert services.get("/api/richieste/tv-2001").json()["stato"] == "parziale"


def test_errors(services, monkeypatch):
    assert services.post("/api/richieste", json={"id": "movie-999"}).status_code == 404
    bad = Radarr("http://radarr", "sbagliata")
    bad._client = _mock_client(mock_arr.radarr, headers={"X-Api-Key": "sbagliata"})
    monkeypatch.setattr(main, "radarr", bad)
    r = services.post("/api/richieste", json={"id": "movie-1001"})
    assert r.status_code == 502 and "non valida" in r.json()["detail"]
    monkeypatch.setattr(main, "sonarr", Sonarr("", ""))
    r = services.post("/api/richieste", json={"id": "tv-2001"})
    assert r.status_code == 400 and "non e' configurato" in r.json()["detail"]
    with pytest.raises(ArrError):
        asyncio.run(Radarr("http://127.0.0.1:1", "x").status(1))
