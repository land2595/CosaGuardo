import asyncio
import hashlib
import logging
import os
import re
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

from .admin import SERVICES, Admin, PinError
from .arr import ASSENTE, SCONOSCIUTO, ArrError, Radarr, Requests, Sonarr
from .catalog import CATEGORIES, Catalog, TMDBError
from .consigli import DEFAULT_MODEL, DEFAULT_OPENAI_URL, MODELS, PROVIDERS, AiError, Consigli
from .gusti import Gusti, GustiError
from .liste import LISTE, Liste, ListeError
from .yamtrack import Yamtrack, YamtrackError
from .plex import Plex, PlexError
from .rotten import OmdbError
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("cosaguardo")

catalog = Catalog(settings)
radarr = Radarr(settings.radarr_url, settings.radarr_api_key,
                settings.radarr_quality_profile, settings.radarr_root_folder)
sonarr = Sonarr(settings.sonarr_url, settings.sonarr_api_key,
                settings.sonarr_quality_profile, settings.sonarr_root_folder,
                language_profile=settings.sonarr_language_profile)
plex = Plex(settings.plex_url, settings.plex_token)
requests = Requests(settings.data_dir)
admin = Admin(settings.data_dir, settings.admin_pin)
gusti = Gusti(settings.data_dir)
liste = Liste(settings.data_dir)
consigli = Consigli(settings.data_dir, settings.anthropic_api_key, settings.ai_model)
yamtrack = Yamtrack(settings.yamtrack_url, settings.yamtrack_user, settings.yamtrack_password)
_tasks: set[asyncio.Task] = set()
STATIC = Path(__file__).parent / "static"


def _static_hash() -> str:
    h = hashlib.sha1()
    for f in sorted(STATIC.rglob("*")):
        if f.is_file():
            h.update(f.read_bytes())
    return h.hexdigest()[:8]


# Versione mostrata nella pagina; l'hash dei file statici serve anche a far
# scaricare al browser i file nuovi dopo ogni aggiornamento.
APP_VERSION = (os.getenv("APP_VERSION") or "dev")[:7]
ASSET_VERSION = _static_hash()


async def refresher() -> None:
    """Aggiorna il catalogo all'avvio (se la cache e' vecchia) e poi periodicamente."""
    if catalog.is_stale():
        await catalog.refresh()
    while True:
        await asyncio.sleep(max(settings.refresh_minutes, 5) * 60)
        await catalog.refresh()


@asynccontextmanager
async def lifespan(_: FastAPI):
    catalog.load_cache()
    task = asyncio.create_task(refresher())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="CosaGuardo", lifespan=lifespan)


def _filter(items: list[dict], tipo: str, piattaforma: list[str] | None, q: str | None,
            categoria: str | None = None) -> list[dict]:
    if tipo in ("movie", "tv"):
        items = [i for i in items if i["type"] == tipo]
    if categoria:
        items = [i for i in items if categoria in i.get("categorie", [])]
    if piattaforma:
        items = [i for i in items if set(i["platforms"]) & set(piattaforma)]
    if q:
        ql = q.lower()
        items = [i for i in items if ql in (i["title"] or "").lower() or ql in (i["original_title"] or "").lower()]
    return items


@app.get("/api/catalogo")
def get_catalogo(
    tipo: str = Query("all", pattern="^(all|movie|tv)$"),
    piattaforma: list[str] | None = Query(None),
    q: str | None = None,
    categoria: str | None = None,
):
    d = catalog.data
    sections = ("novita", "tendenze", "prossimamente", "migliori", "bambini")
    return {
        "aggiornato": d.get("aggiornato"),
        "aggiornamento_in_corso": catalog.refreshing,
        "errore": catalog.error,
        "serve_chiave": catalog.needs_key,
        "versione": APP_VERSION,
        "piattaforme": d.get("piattaforme", {}),
        "categorie": [{"id": k, "nome": v[0]} for k, v in CATEGORIES.items()],
        "rotten_tomatoes": catalog.rt.configured,
        "anni_migliori": settings.best_years,
    } | {k: _filter(d.get(k, []), tipo, piattaforma, q, categoria) for k in sections}


@app.get("/api/stato")
def get_stato():
    d = catalog.data
    return {
        "aggiornato": d.get("aggiornato"),
        "aggiornamento_in_corso": catalog.refreshing,
        "errore": catalog.error,
        "serve_chiave": catalog.needs_key,
        "novita": len(d.get("novita", [])),
        "tendenze": len(d.get("tendenze", [])),
        "intervallo_minuti": settings.refresh_minutes,
        "versione": APP_VERSION,
    }


@app.post("/api/aggiorna")
async def post_aggiorna():
    if not catalog.refreshing:
        asyncio.create_task(catalog.refresh())
    return {"ok": True}


class Chiave(BaseModel):
    chiave: str


@app.post("/api/chiave")
async def post_chiave(body: Chiave):
    # Si puo' impostare solo al primo avvio (o se quella salvata non e' valida),
    # cosi' chi visita la pagina non puo' sostituire una chiave funzionante.
    if not catalog.needs_key:
        raise HTTPException(403, "La chiave e' gia' configurata.")
    if len(body.chiave.strip()) < 20:
        raise HTTPException(400, "La chiave sembra troppo corta.")
    try:
        await catalog.set_key(body.chiave)
    except TMDBError as e:
        raise HTTPException(400, str(e)) from e
    asyncio.create_task(catalog.refresh())
    return {"ok": True}


# ------------------------------------------------------------------ trailer

@app.get("/api/trailer/{kind}/{tmdb_id}")
async def get_trailer(kind: str, tmdb_id: int):
    if kind not in ("movie", "tv"):
        raise HTTPException(404)
    try:
        return {"trailer": await catalog.trailer(kind, tmdb_id)}
    except TMDBError as e:
        raise HTTPException(502, str(e)) from e


# ---------------------------------------------------------------- richieste

def _service(kind: str):
    return radarr if kind == "movie" else sonarr


async def _arr_id(req_or_item: dict) -> int | None:
    """Radarr usa l'id TMDB, Sonarr l'id TVDB."""
    if req_or_item["type"] == "movie":
        return req_or_item["tmdb_id"]
    return req_or_item.get("tvdb_id") or await catalog.tvdb_id(req_or_item["tmdb_id"])


async def _safe(coro, default=None):
    """Interroga un servizio esterno senza far fallire tutta la risposta."""
    try:
        return await coro
    except (ArrError, PlexError, TMDBError) as e:
        log.warning("%s", e)
        return default


async def _none():
    return None


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def _plex_find(kind: str, tmdb_id: int, tvdb_id: int | None = None) -> dict | None:
    if not plex.configured:
        return None
    return await _safe(plex.find(kind, tmdb_id, tvdb_id))


def _find(item_id: str) -> dict | None:
    """Un titolo del catalogo, dei suggerimenti, dei giudizi o delle richieste."""
    return (catalog.find_item(item_id) or consigli.find(item_id) or gusti.get(item_id) or liste.get(item_id)
            or requests.get(item_id))


@app.get("/api/servizi")
def get_servizi():
    return {"radarr": radarr.configured, "sonarr": sonarr.configured, "plex": plex.configured,
            "yamtrack": yamtrack.configured, "ai": consigli.configured, "visti": bool(_visti_sources())}


@app.get("/api/plex")
async def get_plex():
    """Id TMDB dei titoli gia' su Plex, per segnarli nelle liste."""
    if not plex.configured:
        return {"movie": [], "tv": []}
    idx = await _safe(plex.index(), {"movie": {}, "tv": {}})
    return {"movie": list(idx["movie"]), "tv": list(idx["tv"])}


@app.get("/api/richieste")
async def get_richieste():
    """Le richieste fatte, con stato, download in corso e presenza su Plex."""
    async def maybe(svc, method):
        return await _safe(getattr(svc, method)()) if svc.configured else None

    movies, shows, mq, sq, pidx = await asyncio.gather(
        maybe(radarr, "library"), maybe(sonarr, "library"), maybe(radarr, "queue"), maybe(sonarr, "queue"),
        maybe(plex, "index"),
    )
    out = []
    for r in requests.items:
        movie = r["type"] == "movie"
        library, queue = (movies, mq) if movie else (shows, sq)
        info = None if library is None else library.get(r["tmdb_id"] if movie else r.get("tvdb_id"))
        stato = SCONOSCIUTO if library is None else (info["stato"] if info else ASSENTE)
        on_plex = None
        if pidx:
            on_plex = pidx[r["type"]].get(r["tmdb_id"]) or (None if movie else pidx["tvdb"].get(r.get("tvdb_id")))
        out.append(r | {
            "stato": stato,
            "dettagli": info,
            "download": (queue or {}).get(info["arr_id"]) if info else None,
            "plex": on_plex,
        })
    return {"richieste": out, "servizi": get_servizi()}


@app.get("/api/richieste/{item_id}")
async def get_richiesta(item_id: str):
    item = requests.get(item_id) or _find(item_id)
    if not item:
        raise HTTPException(404, "Titolo non trovato.")
    svc = _service(item["type"])
    res: dict = {"stato": None, "configurato": svc.configured, "servizio": svc.name, "dettagli": None,
                 "download": None, "plex": None, "serie": None}
    try:
        arr_id = await _arr_id(item)
    except TMDBError as e:
        return res | {"stato": SCONOSCIUTO, "errore": str(e)}
    tv = item["type"] == "tv"
    res["plex"], res["serie"] = await asyncio.gather(
        _plex_find(item["type"], item["tmdb_id"], arr_id if tv else None),
        _safe(catalog.tv_info(item["tmdb_id"])) if tv else _none(),
    )
    if tv and res["plex"]:
        # Episodi su Plex stagione per stagione, confrontati con quelli usciti
        per_season = await _safe(plex.seasons(res["plex"]["rating_key"]), {})
        aired = {s["numero"]: s for s in (res["serie"] or {}).get("stagioni", [])}
        res["plex"] = res["plex"] | {"per_stagione": [
            {"numero": n, "episodi": per_season.get(n, 0), "totali": aired[n]["episodi"] if n in aired else None}
            for n in sorted(set(per_season) | {n for n, s in aired.items() if s["data"] and s["data"] <= _today()})
        ]}
    if not svc.configured:
        return res
    try:
        info = await svc.get(arr_id) if arr_id else None
        queue = await svc.queue() if info else {}
    except ArrError as e:
        return res | {"stato": SCONOSCIUTO, "errore": str(e)}
    return res | {"stato": info["stato"] if info else ASSENTE, "dettagli": info,
                  "download": queue.get(info["arr_id"]) if info else None}


@app.get("/api/serie/{tmdb_id}/stagioni")
async def get_stagioni(tmdb_id: int):
    """Stagioni di una serie con lo stato in Sonarr e su Plex, per scegliere cosa richiedere."""
    try:
        seasons, tvdb = await asyncio.gather(catalog.tv_seasons(tmdb_id), catalog.tvdb_id(tmdb_id))
    except TMDBError as e:
        raise HTTPException(502, str(e)) from e
    in_sonarr = await _safe(sonarr.get(tvdb)) if sonarr.configured and tvdb else None
    on_plex = await _plex_find("tv", tmdb_id, tvdb)
    plex_seasons = await _safe(plex.seasons(on_plex["rating_key"]), {}) if on_plex else {}
    by_n = {s["numero"]: s for s in (in_sonarr or {}).get("stagioni", [])}
    return {"stagioni": [
        s | {"sonarr": by_n.get(s["numero"]), "plex": plex_seasons.get(s["numero"])} for s in seasons
    ]}


class NuovaRichiesta(BaseModel):
    id: str
    stagioni: list[int] | None = None  # solo serie: None = tutte


@app.post("/api/richieste")
async def post_richiesta(body: NuovaRichiesta):
    item = _find(body.id)
    if not item:
        raise HTTPException(404, "Titolo non trovato.")
    svc = _service(item["type"])
    if not svc.configured:
        raise HTTPException(400, f"{svc.name} non e' configurato.")
    if body.stagioni is not None and not body.stagioni:
        raise HTTPException(400, "Scegli almeno una stagione.")
    try:
        arr_id = await _arr_id(item)
        if not arr_id:
            raise HTTPException(400, "Questa serie non ha un id TVDB, Sonarr non puo' aggiungerla.")
        if item["type"] == "tv":
            stato = await sonarr.add(arr_id, body.stagioni)
        else:
            stato = await radarr.add(arr_id)
    except (ArrError, TMDBError) as e:
        raise HTTPException(502, str(e)) from e
    requests.add(item, tvdb_id=arr_id if item["type"] == "tv" else None,
                 seasons=body.stagioni if item["type"] == "tv" else None)
    liste.remove("watchlist", item["id"])  # richiesto: non serve piu' tenerlo da parte
    return {"stato": stato, "servizio": svc.name}


@app.delete("/api/richieste/{item_id}")
def delete_richiesta(item_id: str):
    """Toglie il titolo dalla schermata "Le mie liste". Non cancella niente da Radarr, Sonarr o Plex."""
    if not requests.remove(item_id):
        raise HTTPException(404, "Richiesta non trovata.")
    return {"ok": True}


# ---------------------------------------------------- watchlist e ignorati

class Voce(BaseModel):
    scheda: dict | None = None


def _lista(nome: str) -> str:
    if nome not in LISTE:
        raise HTTPException(404, "Lista non trovata.")
    return nome


@app.get("/api/liste")
def get_liste():
    return {nome: liste.items(nome) for nome in LISTE}


@app.put("/api/liste/{nome}/{item_id}")
def put_lista(nome: str, item_id: str, body: Voce | None = None):
    _lista(nome)
    try:
        return {"voce": liste.add(nome, item_id, _find(item_id) or (body.scheda if body else None))}
    except ListeError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/api/liste/{nome}/{item_id}")
def delete_lista(nome: str, item_id: str):
    liste.remove(_lista(nome), item_id)
    return {"ok": True}


# --------------------------------------------------------------------- voti

@app.get("/api/voti/{kind}/{tmdb_id}")
async def get_voti(kind: str, tmdb_id: int):
    """Voti IMDb e Rotten Tomatoes di un titolo, con la media."""
    if kind not in ("movie", "tv"):
        raise HTTPException(404)
    item = _find(f"{kind}-{tmdb_id}") or {}
    try:
        voti = await catalog.ratings(kind, tmdb_id, item.get("imdb_id"),
                                     item.get("original_title") or item.get("title") or "",
                                     int(item.get("year") or 0))
    except TMDBError as e:
        raise HTTPException(502, str(e)) from e
    return {"voti": voti}


# -------------------------------------------------------- gusti e per te

class Gusto(BaseModel):
    mi_piace: bool | None = None
    voto: int | None = None
    note: str | None = None
    scheda: dict | None = None


@app.get("/api/gusti")
def get_gusti():
    return {"titoli": gusti.items}


@app.put("/api/gusti/{item_id}")
def put_gusto(item_id: str, body: Gusto):
    values = {k: getattr(body, k) for k in ("mi_piace", "voto", "note") if k in body.model_fields_set}
    if "mi_piace" in values and values["mi_piace"] is None:
        values["mi_piace"] = False
    scheda = _find(item_id) or body.scheda
    try:
        return {"gusto": gusti.update(item_id, scheda, values)}
    except GustiError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/gusti/{item_id}/note-ai")
async def post_note_ai(item_id: str, body: Voce | None = None):
    """Qualche nota brevissima proposta dall'AI sul perche' il titolo ti e' piaciuto.
    Parte solo dal pulsante nella scheda: una richiesta all'AI per ogni pressione."""
    if not consigli.configured:
        raise HTTPException(400, "Collega un servizio AI nel menu impostazioni (l'ingranaggio in alto).")
    item = _find(item_id) or (body.scheda if body else None)
    if not item or not item.get("title"):
        raise HTTPException(404, "Titolo non trovato.")
    try:
        return {"note": await consigli.note(item, gusti.get(item_id))}
    except AiError as e:
        raise HTTPException(502, str(e)) from e


VISTI_SOURCES = ("yamtrack", "plex")


def _visti_sources() -> list[str]:
    """Da dove leggere i titoli gia' visti: quelle scelte nel menu admin (di base tutte) e collegate."""
    chosen = admin.data.get("visti_fonti")
    chosen = VISTI_SOURCES if chosen is None else chosen
    on = {"yamtrack": yamtrack.configured, "plex": plex.configured}
    return [s for s in VISTI_SOURCES if s in chosen and on[s]]


def plex_watched(idx: dict) -> dict[str, dict]:
    """I film visti e le serie iniziate su Plex (con il voto dato in Plex, da 0 a 10)."""
    out: dict[str, dict] = {}
    for kind in ("movie", "tv"):
        for tmdb_id, e in idx.get(kind, {}).items():
            if kind == "movie":
                seen, stato = bool(e.get("visti")), "Completed"
            else:
                viewed, total = e.get("visti") or 0, e.get("episodi") or 0
                seen, stato = viewed > 0, ("Completed" if total and viewed >= total else "In progress")
            voto = e.get("voto")
            if seen or voto is not None:
                out[f"{kind}-{tmdb_id}"] = {"titolo": e.get("titolo") or "", "stato": stato,
                                            "voto": float(voto) if voto is not None else None, "fonti": ["plex"]}
    return out


async def _visti() -> dict[str, dict]:
    """Titoli gia' visti, da Yamtrack e/o Plex. Se un titolo e' in entrambi vale lo stato di Yamtrack."""
    sources = _visti_sources()
    out: dict[str, dict] = {}
    if "plex" in sources:
        out = plex_watched(await _safe(plex.index(), {"movie": {}, "tv": {}}))
    if "yamtrack" in sources:
        try:
            yam = await yamtrack.watched()
        except YamtrackError:
            yam = {}
        for key, v in yam.items():
            other = out.get(key)
            out[key] = v | {"fonti": ["yamtrack"]}
            if other:
                out[key] |= {"voto": v["voto"] if v.get("voto") is not None else other["voto"],
                             "fonti": ["yamtrack", "plex"]}
    return out


@app.get("/api/visti")
async def get_visti():
    """I titoli gia' visti, da Yamtrack e/o Plex."""
    titoli = await _visti()
    return {"fonti": _visti_sources(), "errore": yamtrack.error if "yamtrack" in _visti_sources() else None,
            "titoli": titoli}


def _has_tastes() -> bool:
    return bool(gusti.items) or (consigli.configured and bool(_visti_sources()))


async def _generate() -> None:
    names = [p["name"] for p in catalog.data.get("piattaforme", {}).values()]
    await consigli.generate(catalog, gusti, await _visti(), names, liste)


def _start_generation() -> None:
    task = asyncio.create_task(_generate())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _suggeriti_state() -> dict:
    d = consigli.data
    return {"titoli": d.get("titoli", []), "generato": d.get("generato"), "fonte": d.get("fonte"),
            "errore": d.get("errore"), "in_corso": consigli.running, "ai": consigli.configured,
            "yamtrack": yamtrack.configured, "visti": _visti_sources(), "modello": d.get("modello"),
            "gusti": len(gusti.items),
            "da_aggiornare": consigli.stale(gusti.modified)}


@app.get("/api/suggeriti")
async def get_suggeriti():
    """"Suggeriti per te". Senza AI, se i gusti sono cambiati si ricalcolano da soli in background;
    con l'AI mai: si aggiornano solo con il pulsante (POST /api/suggeriti/aggiorna)."""
    if consigli.needs_update(gusti.modified, _has_tastes()):
        _start_generation()
        return _suggeriti_state() | {"in_corso": True}
    return _suggeriti_state()


@app.post("/api/suggeriti/aggiorna")
async def post_suggeriti():
    if not _has_tastes():
        raise HTTPException(400, "Metti qualche mi piace o un voto: servono per capire i tuoi gusti.")
    if not consigli.running:
        _start_generation()
    return _suggeriti_state() | {"in_corso": True}


# -------------------------------------------------------------- menu admin

def _clients() -> dict:
    return {"sonarr": sonarr, "radarr": radarr}


def apply_admin_settings() -> None:
    """Le scelte del menu admin prevalgono sulle variabili d'ambiente."""
    for name, svc in _clients().items():
        conf = admin.service(name)
        svc.tags = list(conf["tag"])
        svc.quality_profile = conf["profilo_qualita"] or svc.default_quality_profile
        svc.root_folder = conf["cartella"] or svc.default_root_folder


apply_admin_settings()


def require_admin(x_admin_token: str | None = Header(None)) -> str:
    if not admin.valid(x_admin_token):
        raise HTTPException(401, "Sessione scaduta: inserisci di nuovo il PIN.")
    return x_admin_token


def _pin_error(e: PinError) -> HTTPException:
    return HTTPException(e.status, str(e))


class Pin(BaseModel):
    pin: str


class CambioPin(BaseModel):
    attuale: str
    nuovo: str


@app.get("/api/admin")
def get_admin(x_admin_token: str | None = Header(None)):
    return {"pin_impostato": admin.pin_set, "pin_da_env": admin.pin_from_env, "sbloccato": admin.valid(x_admin_token)}


@app.post("/api/admin/pin")
def post_admin_pin(body: Pin):
    """Primo utilizzo: sceglie il PIN."""
    try:
        return {"token": admin.create_pin(body.pin)}
    except PinError as e:
        raise _pin_error(e) from e


@app.put("/api/admin/pin")
def put_admin_pin(body: CambioPin, _: str = Depends(require_admin)):
    try:
        admin.change_pin(body.attuale, body.nuovo)
    except PinError as e:
        raise _pin_error(e) from e
    return {"ok": True}


@app.post("/api/admin/accesso")
def post_admin_login(body: Pin):
    try:
        return {"token": admin.login(body.pin)}
    except PinError as e:
        raise _pin_error(e) from e


@app.post("/api/admin/esci")
def post_admin_logout(token: str = Depends(require_admin)):
    admin.logout(token)
    return {"ok": True}


async def _service_settings(name: str) -> dict:
    svc = _clients()[name]
    out = {"configurato": svc.configured, "nome": svc.name, "valori": admin.service(name),
           "predefiniti": {"profilo_qualita": svc.default_quality_profile, "cartella": svc.default_root_folder},
           "opzioni": None, "errore": None}
    if svc.configured:
        try:
            out["opzioni"] = await svc.options()
        except ArrError as e:
            out["errore"] = str(e)
    return out


@app.get("/api/admin/impostazioni")
async def get_admin_settings(_: str = Depends(require_admin)):
    sonarr_s, radarr_s = await asyncio.gather(_service_settings("sonarr"), _service_settings("radarr"))
    return {"sonarr": sonarr_s, "radarr": radarr_s, "pin_da_env": admin.pin_from_env}


class ServizioImpostazioni(BaseModel):
    tag: list[int] | None = None
    profilo_qualita: str | None = None
    cartella: str | None = None


class Impostazioni(BaseModel):
    sonarr: ServizioImpostazioni | None = None
    radarr: ServizioImpostazioni | None = None


@app.put("/api/admin/impostazioni")
def put_admin_settings(body: Impostazioni, _: str = Depends(require_admin)):
    for name in SERVICES:
        values = getattr(body, name)
        if values is not None:
            admin.update_service(name, values.model_dump(exclude_none=True))
    apply_admin_settings()
    return {name: admin.service(name) for name in SERVICES}


def _omdb_state() -> dict:
    key = catalog.rt.api_key
    return {"configurata": bool(key), "da_env": bool(settings.omdb_api_key) and key == settings.omdb_api_key,
            "chiave": f"{key[:2]}••••{key[-2:]}" if len(key) > 6 else ("••••" if key else ""),
            "usate_oggi": catalog.rt.used_today, "limite": catalog.rt.daily_limit, "errore": catalog.rt.error}


def apply_omdb_key() -> None:
    """La chiave OMDb del menu admin prevale su OMDB_API_KEY."""
    catalog.rt.api_key = admin.data.get("omdb_chiave") or settings.omdb_api_key


apply_omdb_key()


@app.get("/api/admin/omdb")
def get_admin_omdb(_: str = Depends(require_admin)):
    return _omdb_state()


class ChiaveOmdb(BaseModel):
    chiave: str


@app.put("/api/admin/omdb")
async def put_admin_omdb(body: ChiaveOmdb, _: str = Depends(require_admin)):
    key = body.chiave.strip()
    if key:
        try:
            await catalog.rt.verify(key)
        except OmdbError as e:
            raise HTTPException(400, str(e)) from e
    admin.set_value("omdb_chiave", key)
    apply_omdb_key()
    catalog.rt.error = None
    # Ricalcola subito "I migliori" con i voti di Rotten Tomatoes
    catalog.force_best()
    if not catalog.refreshing:
        asyncio.create_task(catalog.refresh())
    return _omdb_state()


def _mask(key: str) -> str:
    return f"{key[:7]}••••{key[-4:]}" if len(key) > 14 else ("••••" if key else "")


def _ai_conf(provider: str) -> dict:
    """Chiave, modello e indirizzo di un servizio AI: il menu admin prevale sulle variabili d'ambiente."""
    env_model = settings.ai_model if settings.ai_provider == provider else ""
    if provider == "openai":
        env_key = settings.openai_api_key
        key = admin.data.get("openai_chiave") or env_key
        return {"chiave": key, "da_env": bool(env_key) and key == env_key,
                "modello": admin.data.get("openai_modello") or env_model,
                "url": (admin.data.get("openai_url") or settings.openai_base_url or DEFAULT_OPENAI_URL).rstrip("/")}
    env_key = settings.anthropic_api_key
    key = admin.data.get("ai_chiave") or env_key
    return {"chiave": key, "da_env": bool(env_key) and key == env_key,
            "modello": admin.data.get("ai_modello") or env_model or DEFAULT_MODEL, "url": None}


def apply_ai_settings() -> None:
    provider = admin.data.get("ai_provider") or settings.ai_provider
    consigli.provider = provider if provider in PROVIDERS else "anthropic"
    conf = _ai_conf(consigli.provider)
    consigli.api_key, consigli.model = conf["chiave"], conf["modello"]
    if conf["url"]:
        consigli.base_url = conf["url"]


apply_ai_settings()


def _ai_state() -> dict:
    services = {}
    for p in PROVIDERS:
        c = _ai_conf(p)
        services[p] = {"configurata": bool(c["chiave"]), "da_env": c["da_env"], "chiave": _mask(c["chiave"]),
                       "modello": c["modello"], "url": c["url"]}
    return {"servizio": consigli.provider, "attivo": consigli.configured, "modelli": MODELS,
            "url_predefinito": DEFAULT_OPENAI_URL, "errore": consigli.data.get("errore")} | services


@app.get("/api/admin/ai")
def get_admin_ai(_: str = Depends(require_admin)):
    return _ai_state()


class AiImpostazioni(BaseModel):
    servizio: str | None = None  # "anthropic" o "openai"; None = quello attuale
    chiave: str | None = None    # None = lascia quella attuale, "" = rimuovi
    modello: str | None = None
    url: str | None = None       # solo per le API compatibili OpenAI


@app.put("/api/admin/ai")
async def put_admin_ai(body: AiImpostazioni, _: str = Depends(require_admin)):
    provider = body.servizio or consigli.provider
    if provider not in PROVIDERS:
        raise HTTPException(400, "Servizio AI sconosciuto.")
    current = _ai_conf(provider)
    key = current["chiave"] if body.chiave is None else body.chiave.strip()
    model = (current["modello"] if body.modello is None else body.modello).strip()
    url = (body.url or current["url"] or "").strip().rstrip("/") or None
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(400, "L'indirizzo deve iniziare con http:// o https://")
    if key:
        try:
            await consigli.verify(key, model, provider, url)
        except AiError as e:
            raise HTTPException(400, str(e)) from e
    prefix = "ai" if provider == "anthropic" else "openai"
    if body.chiave is not None:
        admin.set_value(f"{prefix}_chiave", key)
    admin.set_value(f"{prefix}_modello", model)
    if provider == "openai" and body.url is not None:
        admin.set_value("openai_url", url or "")
    admin.set_value("ai_provider", provider)
    apply_ai_settings()
    consigli.data["errore"] = None
    return _ai_state()


class ElencoModelli(BaseModel):
    url: str | None = None
    chiave: str | None = None


@app.post("/api/admin/ai/modelli")
async def post_admin_ai_models(body: ElencoModelli, _: str = Depends(require_admin)):
    """I modelli offerti da un servizio compatibile OpenAI, per sceglierli dal menu."""
    conf = _ai_conf("openai")
    try:
        return {"modelli": await consigli.list_models(body.chiave or conf["chiave"], body.url or conf["url"])}
    except AiError as e:
        raise HTTPException(400, str(e)) from e


class FontiVisti(BaseModel):
    fonti: list[str]


@app.get("/api/admin/visti")
async def get_admin_visti(_: str = Depends(require_admin)):
    chosen = admin.data.get("visti_fonti")
    return {"fonti": list(VISTI_SOURCES) if chosen is None else chosen, "attive": _visti_sources(),
            "collegate": {"yamtrack": yamtrack.configured, "plex": plex.configured}}


@app.put("/api/admin/visti")
async def put_admin_visti(body: FontiVisti, _: str = Depends(require_admin)):
    admin.set_value("visti_fonti", [s for s in VISTI_SOURCES if s in body.fonti])
    titoli = await _visti()
    return await get_admin_visti(_) | {"titoli": len(titoli)}


def apply_yamtrack_settings() -> None:
    conf = admin.data.get("yamtrack") or {}
    if conf.get("url"):
        yamtrack.configure(conf["url"], conf.get("utente", ""), conf.get("password", ""))
    else:
        yamtrack.configure(settings.yamtrack_url, settings.yamtrack_user, settings.yamtrack_password)


apply_yamtrack_settings()


def _yamtrack_state(titoli: int | None = None) -> dict:
    from_admin = bool((admin.data.get("yamtrack") or {}).get("url"))
    return {"configurato": yamtrack.configured, "url": yamtrack.url, "utente": yamtrack.user,
            "da_env": yamtrack.configured and not from_admin, "errore": yamtrack.error, "titoli": titoli}


@app.get("/api/admin/yamtrack")
async def get_admin_yamtrack(_: str = Depends(require_admin)):
    titoli = len(await _visti()) if yamtrack.configured else None
    return _yamtrack_state(titoli)


class YamtrackImpostazioni(BaseModel):
    url: str
    utente: str = ""
    password: str | None = None  # None o vuota = lascia quella salvata


@app.put("/api/admin/yamtrack")
async def put_admin_yamtrack(body: YamtrackImpostazioni, _: str = Depends(require_admin)):
    url = body.url.strip().rstrip("/")
    if not url:
        admin.set_value("yamtrack", {})
        apply_yamtrack_settings()
        return _yamtrack_state()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "L'indirizzo deve iniziare con http:// o https://")
    password = body.password or (admin.data.get("yamtrack") or {}).get("password") or ""
    test = Yamtrack(url, body.utente, password)
    if not test.configured:
        raise HTTPException(400, "Servono indirizzo, utente e password di Yamtrack.")
    try:
        titoli = await test.watched(force=True)
    except YamtrackError as e:
        raise HTTPException(400, str(e)) from e
    admin.set_value("yamtrack", {"url": url, "utente": body.utente.strip(), "password": password})
    apply_yamtrack_settings()
    yamtrack._cache, yamtrack._ts = titoli, test._ts
    return _yamtrack_state(len(titoli))


class NuovoTag(BaseModel):
    servizio: str
    nome: str


@app.post("/api/admin/tag")
async def post_admin_tag(body: NuovoTag, _: str = Depends(require_admin)):
    svc = _clients().get(body.servizio)
    if not svc:
        raise HTTPException(404, "Servizio sconosciuto.")
    if not svc.configured:
        raise HTTPException(400, f"{svc.name} non e' configurato.")
    try:
        return await svc.create_tag(body.nome)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except ArrError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/health")
def health():
    return {"ok": True}


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/") and request.query_params.get("v") == ASSET_VERSION:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def index():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html = re.sub(r'(/static/[\w./-]+\.(?:css|js|svg))"', rf'\1?v={ASSET_VERSION}"', html)
    return HTMLResponse(html)


app.mount("/static", StaticFiles(directory=STATIC), name="static")
