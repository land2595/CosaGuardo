import asyncio
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.admin import Admin
from app.arr import Requests
from app.catalog import Catalog, combined_ratings
from app.config import Settings
from app.consigli import Consigli, build_prompt
from app.gusti import Gusti
from app.plex import Plex
from app.yamtrack import Yamtrack, YamtrackError, parse_export
from tests import mock_arr, mock_tmdb, mock_yamtrack


def _mock_client(app, **kw):
    return lambda *a: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mock", **kw)


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    s = Settings()
    s.tmdb_api_key = "test"
    s.data_dir = str(tmp_path)
    cat = Catalog(s)
    cat._client = _mock_client(mock_tmdb.app)
    cat.rt.api_key = "test"
    asyncio.run(cat.refresh())
    monkeypatch.setattr(Yamtrack, "_client", lambda self: httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock_yamtrack.app), base_url=self.url, follow_redirects=True))
    monkeypatch.setattr(main, "catalog", cat)
    monkeypatch.setattr(main, "admin", Admin(str(tmp_path)))
    monkeypatch.setattr(main, "requests", Requests(str(tmp_path)))
    monkeypatch.setattr(main, "gusti", Gusti(str(tmp_path)))
    monkeypatch.setattr(main, "consigli", Consigli(str(tmp_path)))
    monkeypatch.setattr(main, "yamtrack", Yamtrack())
    plex = Plex("http://plex", mock_arr.PLEX_TOKEN)
    plex._client = _mock_client(mock_arr.plex, headers={"X-Plex-Token": mock_arr.PLEX_TOKEN})
    monkeypatch.setattr(main, "plex", plex)
    main.apply_ai_settings()
    return TestClient(main.app)


def _login(client) -> dict:
    token = client.post("/api/admin/pin", json={"pin": "1234"}).json()["token"]
    return {"X-Admin-Token": token}


# ---------------------------------------------------------------- voti

def test_combined_ratings_average():
    it = {"imdb_id": "tt1", "title": "Titolo", "original_title": "Title"}
    assert combined_ratings(it, (8.0, 1000), 90)["media"] == 8.5
    assert combined_ratings(it, (7.3, 10), None) == {
        "imdb": {"voto": 7.3, "voti": 10, "url": "https://www.imdb.com/title/tt1/"}, "media": 7.3}
    assert combined_ratings(it, None, 64)["media"] == 6.4
    assert combined_ratings(it, None, None) is None


def test_catalog_items_have_imdb_and_rotten(app_client):
    m = main.catalog.find_item("movie-1001")
    assert m["imdb_id"] == "tt0001001"
    assert m["voti"]["rt"]["voto"] == 97
    imdb = m["voti"]["imdb"]["voto"]
    assert m["voti"]["media"] == round((imdb + 9.7) / 2, 1)

    # Titolo fuori dal catalogo: i voti arrivano dalla scheda
    assert app_client.get("/api/voti/movie/1201").json()["voti"] is None  # non e' su IMDb
    assert app_client.get("/api/voti/movie/1006").json()["voti"]["rt"]["voto"] == 88


# --------------------------------------------------------------- gusti

def test_like_rate_and_notes(app_client):
    r = app_client.put("/api/gusti/movie-1001", json={"mi_piace": True})
    assert r.status_code == 200 and r.json()["gusto"]["mi_piace"] and r.json()["gusto"]["title"] == "La notte di Roma"
    r = app_client.put("/api/gusti/movie-1001", json={"voto": 4, "note": "  Fotografia stupenda e finale aperto  "})
    g = r.json()["gusto"]
    assert (g["mi_piace"], g["voto"], g["note"]) == (True, 4, "Fotografia stupenda e finale aperto")

    # Zero stelle e' un voto valido; 6 no
    assert app_client.put("/api/gusti/tv-2001", json={"voto": 0}).json()["gusto"]["voto"] == 0
    assert app_client.put("/api/gusti/tv-2001", json={"voto": 6}).status_code == 400
    assert app_client.put("/api/gusti/film-1", json={"voto": 3}).status_code == 400

    # Un titolo non in catalogo serve la scheda mandata dalla pagina
    assert app_client.put("/api/gusti/movie-555", json={"mi_piace": True}).status_code == 400
    r = app_client.put("/api/gusti/movie-555", json={"mi_piace": True, "scheda": {"title": "Vecchio film", "year": "1980",
                                                                                   "pericoloso": "x"}})
    assert r.json()["gusto"]["title"] == "Vecchio film" and "pericoloso" not in r.json()["gusto"]

    ids = [g["id"] for g in app_client.get("/api/gusti").json()["titoli"]]
    assert set(ids) == {"movie-1001", "tv-2001", "movie-555"}

    # Senza piu' giudizi il titolo sparisce
    assert app_client.put("/api/gusti/tv-2001", json={"voto": None}).json()["gusto"] is None
    assert "tv-2001" not in [g["id"] for g in app_client.get("/api/gusti").json()["titoli"]]


# ------------------------------------------------------------ yamtrack

def test_parse_yamtrack_export():
    seen = parse_export(mock_yamtrack.CSV)
    assert seen["tv-1396"] == {"titolo": "Breaking Bad", "stato": "Completed", "voto": 9.5}
    assert seen["tv-2001"]["stato"] == "In progress"
    assert seen["movie-1002"]["voto"] == 4.0
    assert "anime-21" not in seen and len(seen) == 4


def test_yamtrack_login_and_admin(app_client):
    y = Yamtrack("http://yamtrack:8000", "federico", "sbagliata")
    with pytest.raises(YamtrackError, match="Accesso a Yamtrack non riuscito"):
        asyncio.run(y.watched())
    y.configure("http://yamtrack:8000", "federico", "segreta")
    assert "movie-1002" in asyncio.run(y.watched())

    h = _login(app_client)
    r = app_client.put("/api/admin/yamtrack", headers=h, json={"url": "http://yamtrack:8000", "utente": "federico",
                                                                 "password": "no"})
    assert r.status_code == 400
    r = app_client.put("/api/admin/yamtrack", headers=h, json={"url": "http://yamtrack:8000", "utente": "federico",
                                                                 "password": "segreta"})
    assert r.status_code == 200 and r.json()["titoli"] == 4
    assert "password" not in r.json()
    # La password resta salvata se non la si riscrive
    assert app_client.put("/api/admin/yamtrack", headers=h, json={"url": "http://yamtrack:8000",
                                                                    "utente": "federico"}).status_code == 200
    assert app_client.get("/api/servizi").json()["yamtrack"] is True
    assert app_client.get("/api/visti").json()["titoli"]["tv-1396"]["voto"] == 9.5


# ------------------------------------------------------- suggeriti per te

def test_prompt_contains_tastes():
    gusti = [{"id": "movie-1", "type": "movie", "title": "Il sorpasso", "year": "1962", "mi_piace": True, "voto": 5,
              "note": "Il ritmo e l'amarezza del finale"},
             {"id": "tv-2", "type": "tv", "title": "Lost", "year": "2004", "voto": 1, "note": ""}]
    p = build_prompt(gusti, {"tv-1396": {"titolo": "Breaking Bad", "voto": 9.5, "stato": "Completed"},
                             "movie-9": {"titolo": "Heat", "voto": None, "stato": "Completed"}}, ["Netflix"])
    assert "Il sorpasso [film, 1962]: mi piace, voto 5/5. Note: \"Il ritmo e l'amarezza del finale\"" in p
    assert "Lost [serie, 2004]: voto 1/5" in p
    assert "Breaking Bad: 9.5/10" in p and "Heat" in p and "Netflix" in p
    assert f"dal {date.today().year - 50}" in p


def test_suggestions_without_ai_use_tmdb(app_client):
    assert app_client.get("/api/suggeriti").json()["titoli"] == []  # nessun gusto: niente da suggerire
    app_client.put("/api/gusti/movie-1001", json={"mi_piace": True, "voto": 5})
    main.gusti.update("movie-1002", main.catalog.find_item("movie-1002"), {"voto": 2})
    asyncio.run(main.consigli.generate(main.catalog, main.gusti, {}, ["Netflix"]))

    d = app_client.get("/api/suggeriti").json()
    assert d["fonte"] == "tmdb" and not d["ai"] and d["errore"] is None
    ids = [it["id"] for it in d["titoli"]]
    # I titoli gia' giudicati non vengono suggeriti; quelli vecchi si'
    assert "movie-1001" not in ids and "movie-1002" not in ids
    assert ids[:2] == ["movie-1201", "movie-1202"]
    old = d["titoli"][0]
    assert old["year"] == "1986" and old["platforms"] == ["netflix"] and "La notte di Roma" in old["motivo"]
    assert d["titoli"][1]["platforms"] == []
    # Un titolo suggerito si puo' richiedere e giudicare come gli altri
    assert main._find("movie-1202")["title"] == "Notte a Bisanzio"


def test_suggestions_with_ai(app_client, monkeypatch):
    seen = {}

    async def fake_ask(self, prompt):
        seen["prompt"] = prompt
        return [
            {"titolo": "Il mare d'inverno", "titolo_originale": "Il mare d'inverno", "anno": 1986, "tipo": "film",
             "motivo": "Stessa malinconia di cui parli nelle note."},
            {"titolo": "Oltre il confine", "titolo_originale": "x", "anno": 2026, "tipo": "film", "motivo": "visto"},
            {"titolo": "Titolo inventato", "titolo_originale": "Made up", "anno": 1999, "tipo": "film", "motivo": "?"},
            {"titolo": "Codice zero", "titolo_originale": "Codice zero", "anno": 2026, "tipo": "serie", "motivo": "Ok"},
        ]

    monkeypatch.setattr(Consigli, "ask", fake_ask)

    async def fake_verify(self, key, model, provider=None, base_url=None):
        if key != "sk-ant-buona-0123456789":
            from app.consigli import AiError
            raise AiError("Chiave API di Anthropic non valida.")

    monkeypatch.setattr(Consigli, "verify", fake_verify)
    h = _login(app_client)
    assert app_client.put("/api/admin/ai", headers=h, json={"chiave": "sbagliata"}).status_code == 400
    r = app_client.put("/api/admin/ai", headers=h, json={"chiave": "sk-ant-buona-0123456789",
                                                          "modello": "claude-sonnet-5-5"}).json()
    a = r["anthropic"]
    assert r["servizio"] == "anthropic" and r["attivo"]
    assert a["configurata"] and a["modello"] == "claude-sonnet-5-5" and "0123456789" not in a["chiave"]
    assert main.consigli.configured

    app_client.put("/api/gusti/movie-1001", json={"mi_piace": True, "note": "Malinconia e mare"})
    visti = {"movie-1002": {"titolo": "Oltre il confine", "voto": 8.0, "stato": "Completed"}}
    asyncio.run(main.consigli.generate(main.catalog, main.gusti, visti, ["Netflix"]))
    d = app_client.get("/api/suggeriti").json()
    assert d["fonte"] == "ai" and d["errore"] is None
    assert [it["id"] for it in d["titoli"]] == ["movie-1201", "tv-2101"]  # gia' visto e inventato esclusi
    assert d["titoli"][0]["motivo"].startswith("Stessa malinconia")
    assert "Malinconia e mare" in seen["prompt"] and "Oltre il confine: 8/10" in seen["prompt"]
    assert not d["da_aggiornare"]
    app_client.put("/api/gusti/movie-1001", json={"voto": 5})
    assert app_client.get("/api/suggeriti").json()["da_aggiornare"]


def test_ask_sends_structured_request(tmp_path, monkeypatch):
    """La richiesta a Claude: modello scelto, risposta in JSON con schema, ripiego in caso di rifiuto."""
    import json

    import anthropic
    import httpx2

    sent = {}

    def handler(request):
        sent["url"] = str(request.url)
        sent["beta"] = request.headers.get("anthropic-beta")
        sent["body"] = json.loads(request.content)
        text = json.dumps({"suggerimenti": [{"titolo": "Heat - La sfida", "titolo_originale": "Heat", "anno": 1995,
                                             "tipo": "film", "motivo": "Tensione e personaggi ambigui."}]})
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 20}})

    c = Consigli(str(tmp_path), api_key="sk-ant-test")
    monkeypatch.setattr(c, "_client", lambda key=None: anthropic.AsyncAnthropic(
        api_key="sk-ant-test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))))
    out = asyncio.run(c.ask("I miei gusti"))
    assert out[0]["titolo_originale"] == "Heat"
    body = sent["body"]
    assert "/v1/messages" in sent["url"]
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in sent["beta"]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["messages"] == [{"role": "user", "content": "I miei gusti"}]


# ------------------------------------------------- visti da Plex e Yamtrack

def test_watched_from_plex_yamtrack_or_both(app_client):
    mock_arr.plex.state.library["movie"][0]["userRating"] = 8.0
    h = _login(app_client)
    # Solo Plex (Yamtrack non collegato): film visto con il voto di Plex, serie iniziata
    d = app_client.get("/api/visti").json()
    assert d["fonti"] == ["plex"]
    assert d["titoli"]["movie-1002"] == {"titolo": "La notte di Roma", "stato": "Completed", "voto": 8.0, "fonti": ["plex"]}
    assert d["titoli"]["tv-2003"]["stato"] == "In progress"
    assert app_client.get("/api/servizi").json()["visti"] is True

    # Plex + Yamtrack: lo stato e il voto di Yamtrack prevalgono, le fonti si sommano
    assert app_client.put("/api/admin/yamtrack", headers=h, json={"url": "http://yamtrack:8000", "utente": "federico",
                                                                    "password": "segreta"}).status_code == 200
    t = app_client.get("/api/visti").json()["titoli"]
    assert t["movie-1002"] == {"titolo": "Oltre il confine", "stato": "Completed", "voto": 4.0, "fonti": ["yamtrack", "plex"]}
    assert t["tv-2003"]["fonti"] == ["plex"] and t["tv-1396"]["fonti"] == ["yamtrack"]

    # Solo Yamtrack
    r = app_client.put("/api/admin/visti", headers=h, json={"fonti": ["yamtrack"]}).json()
    assert r["attive"] == ["yamtrack"] and r["collegate"] == {"yamtrack": True, "plex": True}
    t = app_client.get("/api/visti").json()["titoli"]
    assert "tv-2003" not in t and t["movie-1002"]["fonti"] == ["yamtrack"]

    # Nessuna fonte
    app_client.put("/api/admin/visti", headers=h, json={"fonti": []})
    assert app_client.get("/api/visti").json()["titoli"] == {}
    assert app_client.get("/api/servizi").json()["visti"] is False
    mock_arr.plex.state.library["movie"][0].pop("userRating")


# ------------------------------------------------ API compatibili OpenAI

def _openai_mock(seen: dict, structured_ok: bool = True):
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    api = FastAPI()

    @api.get("/api/v1/key")
    def key(request: Request):
        if request.headers.get("authorization") != "Bearer sk-or-buona":
            return JSONResponse({"error": {"message": "No auth credentials found", "code": 401}}, status_code=401)
        return {"data": {"label": "test"}}

    @api.get("/api/v1/models")
    def models():
        return {"data": [{"id": "openai/gpt-test"}, {"id": "google/gemini-test"}]}

    @api.post("/api/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        seen.setdefault("bodies", []).append(body)
        if "response_format" in body and not structured_ok:
            return JSONResponse({"error": {"message": "response_format json_schema is not supported"}}, status_code=400)
        text = '{"suggerimenti": [{"titolo": "Il mare d\'inverno", "titolo_originale": "Il mare d\'inverno", ' \
               '"anno": 1986, "tipo": "film", "motivo": "Malinconia."}]}'
        if "response_format" not in body:
            text = "Ecco i titoli:\n```json\n" + text + "\n```"
        return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}

    return api


def test_openai_compatible_provider(app_client, monkeypatch):
    seen: dict = {}
    api = _openai_mock(seen)
    monkeypatch.setattr(Consigli, "_http", lambda self, key, base_url: httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url=base_url, headers={"Authorization": f"Bearer {key}"}))
    h = _login(app_client)

    # Elenco dei modelli per sceglierli dal menu
    r = app_client.post("/api/admin/ai/modelli", headers=h, json={"url": "https://openrouter.ai/api/v1"})
    assert r.json()["modelli"] == ["google/gemini-test", "openai/gpt-test"]

    bad = {"servizio": "openai", "chiave": "sk-or-sbagliata", "modello": "openai/gpt-test"}
    assert app_client.put("/api/admin/ai", headers=h, json=bad).json()["detail"] == "Chiave API non valida."
    wrong_model = {"servizio": "openai", "chiave": "sk-or-buona", "modello": "openai/inesistente"}
    assert "non c'e'" in app_client.put("/api/admin/ai", headers=h, json=wrong_model).json()["detail"]
    r = app_client.put("/api/admin/ai", headers=h, json={"servizio": "openai", "chiave": "sk-or-buona",
                                                          "modello": "openai/gpt-test"}).json()
    assert r["servizio"] == "openai" and r["attivo"] and r["openai"]["url"] == "https://openrouter.ai/api/v1"
    assert main.consigli.provider == "openai" and main.consigli.label == "openai/gpt-test"

    app_client.put("/api/gusti/movie-1001", json={"mi_piace": True})
    asyncio.run(main.consigli.generate(main.catalog, main.gusti, {}, ["Netflix"]))
    d = app_client.get("/api/suggeriti").json()
    assert d["fonte"] == "ai" and d["modello"] == "openai/gpt-test" and d["errore"] is None
    assert [it["id"] for it in d["titoli"]] == ["movie-1201"]
    body = seen["bodies"][-1]
    assert body["model"] == "openai/gpt-test" and body["response_format"]["type"] == "json_schema"
    assert body["messages"][0]["role"] == "system"

    # Si torna a Claude senza perdere le impostazioni di OpenRouter
    r = app_client.put("/api/admin/ai", headers=h, json={"servizio": "anthropic"}).json()
    assert r["servizio"] == "anthropic" and not r["attivo"] and r["openai"]["modello"] == "openai/gpt-test"


def test_openai_without_structured_output(tmp_path, monkeypatch):
    seen: dict = {}
    api = _openai_mock(seen, structured_ok=False)
    c = Consigli(str(tmp_path), api_key="k", model="local/model", provider="openai", base_url="http://local/api/v1")
    monkeypatch.setattr(c, "_http", lambda key, base_url: httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url=base_url))
    out = asyncio.run(c.ask("gusti"))
    assert out[0]["anno"] == 1986
    assert len(seen["bodies"]) == 2 and "response_format" not in seen["bodies"][1]
