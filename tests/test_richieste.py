import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.admin import Admin
from app.arr import ArrError, Radarr, Requests, Sonarr
from app.catalog import Catalog
from app.consigli import Consigli
from app.gusti import Gusti
from app.liste import Liste
from app.profilo import Profilo
from app.yamtrack import Yamtrack
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
    mock_arr.radarr.state.tags = [{"id": 1, "label": "film-famiglia"}]
    mock_arr.sonarr.state.tags = [{"id": 1, "label": "anime"}, {"id": 2, "label": "cosaguardo"}]
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
    monkeypatch.setattr(main, "admin", Admin(str(tmp_path)))
    monkeypatch.setattr(main, "gusti", Gusti(str(tmp_path)))
    monkeypatch.setattr(main, "liste", Liste(str(tmp_path)))
    monkeypatch.setattr(main, "profilo", Profilo(str(tmp_path)))
    monkeypatch.setattr(main, "consigli", Consigli(str(tmp_path)))
    monkeypatch.setattr(main, "yamtrack", Yamtrack())
    main.apply_admin_settings()
    return TestClient(main.app)


def test_trailer_prefers_italian(services):
    r = services.get("/api/trailer/movie/1001").json()
    assert r["trailer"]["key"] == "aqz-KE-bpKQ" and r["trailer"]["lingua"] == "it"
    assert services.get("/api/trailer/movie/1005").json()["trailer"] is None


def test_request_movie_goes_to_radarr(services):
    assert services.get("/api/servizi").json() == {"radarr": True, "sonarr": True, "plex": True,
                                                   "yamtrack": False, "ai": False, "visti": True}
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


def test_prossimamente(services):
    d = services.get("/api/catalogo").json()
    upcoming = {i["id"]: i for i in d["prossimamente"]}
    assert set(upcoming) == {"movie-1101", "movie-1102", "movie-1103", "movie-1104", "tv-2101", "tv-2102", "tv-2104"}
    assert upcoming["movie-1103"]["platforms"] == ["apple"]  # dalla casa di produzione
    assert upcoming["tv-2102"]["badge"] == "Stagione 3"
    dates = [i["date"] for i in d["prossimamente"]]
    assert dates == sorted(dates)


def test_migliori_without_omdb(services, tmp_path):
    d = services.get("/api/catalogo").json()
    assert "da_non_perdere" not in d and d["rotten_tomatoes"] is False
    best = {i["id"]: i for i in d["migliori"]}
    assert best
    for it in best.values():
        assert it["score"]["fonte"] == "IMDb" and it["rating"] == it["score"]["voto"] >= 7
        assert it["votes"] >= (5000 if it["type"] == "movie" else 2000)
        assert it["score"]["url"].startswith("https://www.imdb.com/title/tt")
        assert it["platforms"] and it["year"] and "rt" not in it
    assert "movie-1003" not in best  # voto alto ma pochi voti su IMDb
    assert "movie-1007" not in best  # senza scheda in italiano
    assert "tv-2106" not in best     # reality
    assert "movie-1101" not in best  # non ancora uscito
    assert (tmp_path / "imdb-ratings.tsv.gz").exists()


def test_migliori_rotten_tomatoes(tmp_path):
    s = Settings()
    s.tmdb_api_key = "test"
    s.data_dir = str(tmp_path)
    s.omdb_api_key = "omdb-test"
    cat = Catalog(s)
    cat._client = _mock_client(mock_tmdb.app)
    asyncio.run(cat.refresh())
    best = cat.data["migliori"]
    with_rt = [i for i in best if "rt" in i]
    assert with_rt and all(i["type"] == "movie" and i["rt"]["voto"] >= 70 for i in with_rt)
    assert "movie-1005" not in {i["id"] for i in best}  # Tomatometer 55%: escluso
    # Ordine per Tomatometer (senza Tomatometer conta la media IMDb pesata, in centesimi)
    rts = [i["rt"]["voto"] for i in with_rt]
    assert rts == sorted(rts, reverse=True) and best[0]["rt"]["voto"] == 100
    assert any(i["type"] == "tv" and "rt" not in i for i in best)  # le serie restano, con il voto IMDb
    # I voti sono salvati: un secondo aggiornamento non chiede niente a OMDb
    used = cat.rt.used_today
    assert used > 0 and (tmp_path / "rotten-tomatoes.json").exists()
    cat.force_best()
    asyncio.run(cat.refresh())
    assert cat.rt.used_today == used


def test_migliori_without_imdb(tmp_path):
    from app.imdb import ImdbRatings
    s = Settings()
    s.tmdb_api_key = "test"
    s.data_dir = str(tmp_path)
    s.imdb_ratings = False
    cat = Catalog(s)
    cat._client = _mock_client(mock_tmdb.app)
    asyncio.run(cat.refresh())
    best = cat.data["migliori"]
    assert best and all(i["score"]["fonte"] == "TMDB" and i["rating"] >= 7 for i in best)
    assert isinstance(cat.imdb, ImdbRatings) and not (tmp_path / "imdb-ratings.tsv.gz").exists()


def test_bambini(services):
    kids = {i["id"]: i for i in services.get("/api/catalogo").json()["bambini"]}
    assert {"movie-1004", "movie-1010", "tv-2007"} <= set(kids)
    assert "movie-1012" not in kids  # animazione PG-13
    assert "tv-2009" not in kids     # animazione TV-MA
    assert "movie-1001" not in kids  # vietato ai minori (R)
    assert "famiglia" in kids["movie-1004"]["categorie"]
    pop = [i["popularity"] for i in kids.values()]
    assert pop == sorted(pop, reverse=True)


def test_categorie_filter(services):
    d = services.get("/api/catalogo").json()
    assert {"id": "commedia", "nome": "Commedia"} in d["categorie"]
    comedies = services.get("/api/catalogo", params={"categoria": "commedia", "tipo": "movie"}).json()["novita"]
    assert comedies and all("commedia" in i["categorie"] and i["type"] == "movie" for i in comedies)
    assert len(comedies) < len(d["novita"])


def test_series_status_and_plex_seasons(services):
    r = services.get("/api/richieste/tv-2003").json()
    assert r["serie"]["stato"] == "terminata"
    per = {s["numero"]: s for s in r["plex"]["per_stagione"]}
    assert per[1]["episodi"] == 8 and per[1]["totali"] == 8
    assert per[2]["episodi"] == 6 and per[2]["totali"] == 8
    nxt = services.get("/api/richieste/tv-2102").json()["serie"]
    assert nxt["stato"] == "in_corso" and nxt["prossimo"]["nuova_stagione"] and nxt["prossimo"]["stagione"] == 3
    assert services.get("/api/richieste/movie-1001").json()["serie"] is None


# ---------------------------------------------------------------- menu admin

def _login(client, pin="2468"):
    r = client.post("/api/admin/pin", json={"pin": pin})
    assert r.status_code == 200
    return {"X-Admin-Token": r.json()["token"]}


def test_admin_pin_first_setup_and_login(services, tmp_path):
    assert services.get("/api/admin").json() == {"pin_impostato": False, "pin_da_env": False, "sbloccato": False}
    assert services.get("/api/admin/impostazioni").status_code == 401
    assert services.post("/api/admin/pin", json={"pin": "12"}).status_code == 400

    h = _login(services)
    assert services.get("/api/admin", headers=h).json()["sbloccato"]
    # Il PIN non si puo' reimpostare senza conoscere quello attuale
    assert services.post("/api/admin/pin", json={"pin": "0000"}).status_code == 403
    assert "2468" not in (tmp_path / "impostazioni.json").read_text()

    assert services.post("/api/admin/accesso", json={"pin": "1111"}).status_code == 401
    assert services.post("/api/admin/accesso", json={"pin": "2468"}).status_code == 200

    services.post("/api/admin/esci", headers=h)
    assert services.get("/api/admin/impostazioni", headers=h).status_code == 401

    # Cambio PIN
    h = {"X-Admin-Token": services.post("/api/admin/accesso", json={"pin": "2468"}).json()["token"]}
    assert services.put("/api/admin/pin", headers=h, json={"attuale": "0000", "nuovo": "1357"}).status_code == 403
    assert services.put("/api/admin/pin", headers=h, json={"attuale": "2468", "nuovo": "1357"}).status_code == 200
    assert services.post("/api/admin/accesso", json={"pin": "1357"}).status_code == 200
    # Il PIN resta dopo un riavvio
    assert Admin(str(tmp_path)).login("1357")


def test_admin_lockout_after_wrong_pins(services):
    _login(services)
    for _ in range(4):
        assert services.post("/api/admin/accesso", json={"pin": "0000"}).status_code == 401
    assert services.post("/api/admin/accesso", json={"pin": "0000"}).status_code == 429
    # Bloccato anche con il PIN giusto
    assert services.post("/api/admin/accesso", json={"pin": "2468"}).status_code == 429


def test_admin_pin_from_env(tmp_path):
    a = Admin(str(tmp_path), env_pin="9876")
    assert a.pin_set and a.pin_from_env
    assert a.login("9876")
    with pytest.raises(Exception):
        a.create_pin("1234")


def test_admin_sonarr_tags_applied_to_new_series(services):
    h = _login(services)
    conf = services.get("/api/admin/impostazioni", headers=h).json()
    assert conf["sonarr"]["opzioni"]["tag"] == [{"id": 1, "nome": "anime"}, {"id": 2, "nome": "cosaguardo"}]
    assert conf["sonarr"]["opzioni"]["cartelle"] == ["/media/principale", "/media/altro"]
    assert conf["sonarr"]["valori"] == {"tag": [], "profilo_qualita": "", "cartella": ""}

    # Nuovo tag creato da CosaGuardo (il nome viene reso valido per Sonarr)
    t = services.post("/api/admin/tag", headers=h, json={"servizio": "sonarr", "nome": "Da Vedere"}).json()
    assert t == {"id": 3, "nome": "da-vedere"}
    assert services.post("/api/admin/tag", headers=h, json={"servizio": "sonarr", "nome": "da vedere"}).json()["id"] == 3

    r = services.put("/api/admin/impostazioni", headers=h, json={
        "sonarr": {"tag": [2, 3], "profilo_qualita": "HD-1080p", "cartella": "/media/altro"}})
    assert r.json()["sonarr"] == {"tag": [2, 3], "profilo_qualita": "HD-1080p", "cartella": "/media/altro"}
    assert services.put("/api/admin/impostazioni", json={"sonarr": {"tag": []}}).status_code == 401

    # Un tag cancellato in Sonarr viene ignorato
    mock_arr.sonarr.state.tags = [t for t in mock_arr.sonarr.state.tags if t["id"] != 3]
    services.post("/api/richieste", json={"id": "tv-2001"})
    added = mock_arr.sonarr.state.series[0]
    assert added["tags"] == [2]
    assert added["qualityProfileId"] == 4 and added["rootFolderPath"] == "/media/altro"

    # I film non prendono i tag di Sonarr
    services.post("/api/richieste", json={"id": "movie-1001"})
    assert mock_arr.radarr.state.movies[0]["tags"] == []


def test_unreachable_service_explains_why():
    # Porta chiusa in locale: la connessione viene rifiutata subito
    sonarr = Sonarr("http://127.0.0.1:9", "test")
    with pytest.raises(ArrError) as e:
        asyncio.run(sonarr.tag_list())
    assert "Impossibile contattare Sonarr su http://127.0.0.1:9:" in str(e.value)
    assert "rifiutata" in str(e.value)


def test_admin_omdb_key(services, monkeypatch):
    h = _login(services)
    assert services.get("/api/admin/omdb").status_code == 401
    st = services.get("/api/admin/omdb", headers=h).json()
    assert st["configurata"] is False
    r = services.put("/api/admin/omdb", json={"chiave": "sbagliata1"}, headers=h)
    assert r.status_code == 400 and "non valida" in r.json()["detail"]
    st = services.put("/api/admin/omdb", json={"chiave": "abcd1234"}, headers=h).json()
    assert st["configurata"] and st["chiave"] == "ab••••34"
    assert main.catalog.rt.api_key == "abcd1234" and main.admin.data["omdb_chiave"] == "abcd1234"
    assert services.get("/api/catalogo").json()["rotten_tomatoes"] is True
    services.put("/api/admin/omdb", json={"chiave": ""}, headers=h)
    assert main.catalog.rt.api_key == ""


# ------------------------------------------------ watchlist, ignorati e cancella

def test_watchlist_ignore_and_request(services):
    assert services.get("/api/liste").json() == {"watchlist": [], "ignorati": []}
    r = services.put("/api/liste/watchlist/movie-1001")
    assert r.status_code == 200 and r.json()["voce"]["title"] and r.json()["voce"]["aggiunto_il"]
    services.put("/api/liste/watchlist/tv-2001")
    assert {v["id"] for v in services.get("/api/liste").json()["watchlist"]} == {"tv-2001", "movie-1001"}

    # "Non mi interessa": esce dalla watchlist e finisce tra gli ignorati
    services.put("/api/liste/ignorati/tv-2001")
    d = services.get("/api/liste").json()
    assert [v["id"] for v in d["watchlist"]] == ["movie-1001"] and [v["id"] for v in d["ignorati"]] == ["tv-2001"]
    # Ripristino
    assert services.delete("/api/liste/ignorati/tv-2001").json() == {"ok": True}
    assert services.get("/api/liste").json()["ignorati"] == []

    # Titolo fuori catalogo: serve la scheda mandata dalla pagina
    assert services.put("/api/liste/watchlist/movie-999999").status_code == 400
    ok = services.put("/api/liste/watchlist/movie-999999", json={"scheda": {"title": "Vecchio film", "year": "1980"}})
    assert ok.status_code == 200 and ok.json()["voce"]["tmdb_id"] == 999999
    assert services.put("/api/liste/preferiti/movie-1001").status_code == 404
    assert services.put("/api/liste/watchlist/nonvalido").status_code == 400

    # Richiederlo lo toglie dalla watchlist
    services.post("/api/richieste", json={"id": "movie-1001"})
    assert [v["id"] for v in services.get("/api/liste").json()["watchlist"]] == ["movie-999999"]


def test_delete_request_only_from_cosaguardo(services):
    services.post("/api/richieste", json={"id": "movie-1001"})
    assert [r["id"] for r in services.get("/api/richieste").json()["richieste"]] == ["movie-1001"]
    assert services.delete("/api/richieste/movie-1001").json() == {"ok": True}
    assert services.get("/api/richieste").json()["richieste"] == []
    # Radarr non viene toccato: il film resta li'
    assert [m["tmdbId"] for m in mock_arr.radarr.state.movies] == [1001]
    assert services.get("/api/richieste/movie-1001").json()["stato"] == "richiesto"
    assert services.delete("/api/richieste/movie-1001").status_code == 404


def test_scheda_cast_and_genres(services):
    film = services.get("/api/scheda/movie/1001").json()
    assert film["generi"] and all(isinstance(g, str) for g in film["generi"])
    assert [c["nome"] for c in film["cast"]] == ["Giulia Neri", "Marco Bianchi"]
    assert film["cast"][0]["personaggio"] == "Ruolo 1001" and film["cast"][0]["foto"].endswith("/a501.svg")
    serie = services.get("/api/scheda/tv/2001").json()
    assert serie["cast"][0] == {"id": 501, "nome": "Giulia Neri", "personaggio": "Ruolo 2001",
                                "foto": serie["cast"][0]["foto"], "episodi": 8}
    assert services.get("/api/scheda/tv/2002").json()["cast"] == []


def test_persona_films_and_series(services):
    p = services.get("/api/persona/501").json()
    assert p["nome"] == "Giulia Neri" and p["mestiere"] == "attore" and p["biografia"]
    ids = [t["id"] for t in p["titoli"]]
    assert {"movie-1001", "movie-1002", "movie-1201", "tv-2001"} <= set(ids)
    assert len(ids) == len(set(ids)), "un titolo una volta sola"
    assert "tv-2106" not in ids   # reality
    assert "tv-2003" not in ids   # comparsa nel ruolo di se' stessa
    assert next(t for t in p["titoli"] if t["id"] == "movie-1001")["ruolo"] == "Ruolo 1001 / Ruolo doppio"
    dates = [t["date"] for t in p["titoli"]]
    assert dates == sorted(dates, reverse=True)
    # Biografia non tradotta: in inglese
    assert "en-US" in services.get("/api/persona/502").json()["biografia"]
    # Un regista: i film che ha diretto
    reg = services.get("/api/persona/503").json()
    assert reg["mestiere"] == "regia" and {t["id"] for t in reg["titoli"]} == {"movie-1001", "movie-1005"}
    assert services.get("/api/persona/999").status_code == 404


def test_titolo_from_filmography(services):
    # Nel catalogo: lo stesso elemento; vecchio: scheda costruita al volo
    assert services.get("/api/titolo/movie/1001").json()["id"] == "movie-1001"
    old = services.get("/api/titolo/movie/1201").json()
    assert old["title"] == "Il mare d'inverno" and old["year"] == "1986" and old["genres"]


def test_migliori_marks_kids(services):
    best = services.get("/api/catalogo").json()["migliori"]
    assert all(isinstance(i["bambini"], bool) for i in best)
    kids = {i["id"] for i in best if i["bambini"]}
    assert kids and kids <= {"movie-1004", "movie-1010", "tv-2007"}
