import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.arr import ArrError, Radarr, Requests, Sonarr
from app.catalog import Catalog
from app.plex import Plex
from app.config import Settings
from tests import mock_arr, mock_tmdb


def _mock_client(app, **kw):
    return lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mock", **kw)


@pytest.fixture
def services(tmp_path, monkeypatch):
    mock_arr.radarr.state.movies = []
    mock_arr.radarr.state.queue = []
    mock_arr.sonarr.state.series = []
    mock_arr.sonarr.state.queue = []
    mock_arr.sonarr.state.commands = []
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
    plex = Plex("http://plex", mock_arr.PLEX_TOKEN)
    plex._client = _mock_client(mock_arr.plex, headers={"X-Plex-Token": mock_arr.PLEX_TOKEN})
    monkeypatch.setattr(main, "plex", plex)
    monkeypatch.setattr(main, "requests", Requests(str(tmp_path)))
    return TestClient(main.app)


def test_trailer_prefers_italian(services):
    r = services.get("/api/trailer/movie/1001").json()
    assert r["trailer"]["key"] == "aqz-KE-bpKQ" and r["trailer"]["lingua"] == "it"
    assert services.get("/api/trailer/movie/1005").json()["trailer"] is None


def test_request_movie_goes_to_radarr(services):
    assert services.get("/api/servizi").json() == {"radarr": True, "sonarr": True, "plex": True}
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
    assert [x["monitored"] for x in added["seasons"]] == [False, True, True, True]  # speciali escluse

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


def test_index_busts_cache(services):
    r = services.get("/")
    assert r.headers["cache-control"] == "no-cache"
    v = main.ASSET_VERSION
    assert f'/static/app.js?v={v}"' in r.text and f'/static/style.css?v={v}"' in r.text
    assert "immutable" in services.get(f"/static/app.js?v={v}").headers["cache-control"]
    assert services.get("/static/app.js").headers["cache-control"] == "no-cache"
    assert "versione" in services.get("/api/stato").json()


def test_request_selected_seasons_then_more(services):
    r = services.post("/api/richieste", json={"id": "tv-2002", "stagioni": [2]})
    assert r.status_code == 200
    series = mock_arr.sonarr.state.series[0]
    assert {x["seasonNumber"]: x["monitored"] for x in series["seasons"]} == {0: False, 1: False, 2: True, 3: False}

    # Seconda richiesta della stessa serie: aggiunge la stagione 3 e la cerca
    assert services.post("/api/richieste", json={"id": "tv-2002", "stagioni": [3]}).status_code == 200
    series = mock_arr.sonarr.state.series[0]
    assert [x["monitored"] for x in series["seasons"]] == [False, False, True, True]
    assert mock_arr.sonarr.state.commands == [{"name": "SeasonSearch", "seriesId": 1, "seasonNumber": 3}]
    assert len(mock_arr.sonarr.state.series) == 1
    req = services.get("/api/richieste").json()["richieste"][0]
    assert req["stagioni"] == [2, 3]
    assert [(x["numero"], x["monitorata"]) for x in req["dettagli"]["stagioni"]] == [(1, False), (2, True), (3, True)]

    assert services.post("/api/richieste", json={"id": "tv-2002", "stagioni": []}).status_code == 400


def test_download_progress(services):
    services.post("/api/richieste", json={"id": "movie-1001"})
    services.post("/api/richieste", json={"id": "tv-2001"})
    mock_arr.radarr.state.queue = [{"movieId": 1, "size": 1000, "sizeleft": 250, "timeleft": "00:10:00",
                                    "status": "downloading", "trackedDownloadState": "downloading"}]
    mock_arr.sonarr.state.queue = [
        {"seriesId": 1, "episodeId": 1, "size": 100, "sizeleft": 100, "timeleft": "00:20:00", "status": "queued"},
        {"seriesId": 1, "episodeId": 2, "size": 300, "sizeleft": 0, "timeleft": "00:00:00", "status": "downloading"},
    ]
    by_id = {r["id"]: r for r in services.get("/api/richieste").json()["richieste"]}
    assert by_id["movie-1001"]["download"]["percentuale"] == 75.0
    assert by_id["tv-2001"]["download"] == {"percentuale": 75.0, "download": 2, "tempo_rimanente": "00:20:00",
                                            "in_pausa": False, "in_attesa": False}
    assert services.get("/api/richieste/movie-1001").json()["download"]["percentuale"] == 75.0


def test_plex(services):
    assert services.get("/api/plex").json() == {"movie": [1002], "tv": [2003]}
    r = services.get("/api/richieste/tv-2003").json()
    assert r["plex"]["stagioni"] == 2 and r["plex"]["episodi"] == 14
    assert r["plex"]["url"].startswith("https://app.plex.tv/desktop/#!/server/abc123/")
    stagioni = services.get("/api/serie/2003/stagioni").json()["stagioni"]
    assert [(x["numero"], x["episodi"], x["plex"]) for x in stagioni][:2] == [(1, 8, 8), (2, 8, 6)]
    assert services.get("/api/richieste/movie-1002").json()["plex"]["titolo"] == "La notte di Roma"


def test_prossimamente_and_da_non_perdere(services):
    d = services.get("/api/catalogo").json()
    upcoming = {i["id"]: i for i in d["prossimamente"]}
    assert set(upcoming) == {"movie-1101", "movie-1102", "movie-1103", "movie-1104", "tv-2101", "tv-2102", "tv-2104"}
    assert upcoming["movie-1103"]["platforms"] == ["apple"]  # dalla casa di produzione
    assert upcoming["tv-2102"]["badge"] == "Stagione 3"
    dates = [i["date"] for i in d["prossimamente"]]
    assert dates == sorted(dates)


def test_da_non_perdere_reliable(services, tmp_path):
    must = {i["id"]: i for i in services.get("/api/catalogo").json()["da_non_perdere"]}
    assert must
    for it in must.values():
        assert it["score"]["fonte"] == "IMDb" and it["rating"] == it["score"]["voto"] >= 7
        assert it["votes"] >= (3000 if it["type"] == "movie" else 1000)
        assert it["score"]["url"].startswith("https://www.imdb.com/title/tt")
        assert it["platforms"]
    assert "movie-1003" not in must  # voto alto ma pochi voti su IMDb
    assert "movie-1007" not in must  # senza scheda in italiano
    assert "tv-2105" not in must     # stagione iniziata piu' di 6 mesi fa
    assert "tv-2106" not in must     # reality
    assert (tmp_path / "imdb-ratings.tsv.gz").exists()


def test_da_non_perdere_without_imdb(tmp_path):
    from app.imdb import ImdbRatings
    s = Settings()
    s.tmdb_api_key = "test"
    s.data_dir = str(tmp_path)
    s.imdb_ratings = False
    cat = Catalog(s)
    cat._client = _mock_client(mock_tmdb.app)
    asyncio.run(cat.refresh())
    must = cat.data["da_non_perdere"]
    assert must and all(i["score"]["fonte"] == "TMDB" and i["rating"] >= 7 for i in must)
    assert isinstance(cat.imdb, ImdbRatings) and not (tmp_path / "imdb-ratings.tsv.gz").exists()
