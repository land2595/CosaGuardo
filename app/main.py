import asyncio
import hashlib
import logging
import os
import re
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

from .arr import ASSENTE, SCONOSCIUTO, ArrError, Radarr, Requests, Sonarr
from .catalog import Catalog, TMDBError
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
requests = Requests(settings.data_dir)
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


def _filter(items: list[dict], tipo: str, piattaforma: list[str] | None, q: str | None) -> list[dict]:
    if tipo in ("movie", "tv"):
        items = [i for i in items if i["type"] == tipo]
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
):
    d = catalog.data
    return {
        "aggiornato": d.get("aggiornato"),
        "aggiornamento_in_corso": catalog.refreshing,
        "errore": catalog.error,
        "serve_chiave": catalog.needs_key,
        "versione": APP_VERSION,
        "piattaforme": d.get("piattaforme", {}),
        "novita": _filter(d.get("novita", []), tipo, piattaforma, q),
        "tendenze": _filter(d.get("tendenze", []), tipo, piattaforma, q),
    }


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


@app.get("/api/servizi")
def get_servizi():
    return {"radarr": radarr.configured, "sonarr": sonarr.configured}


@app.get("/api/richieste")
async def get_richieste():
    """Le richieste fatte, con lo stato aggiornato da Radarr e Sonarr."""
    async def lib(svc):
        if not svc.configured:
            return None
        try:
            return await svc.library()
        except ArrError as e:
            log.warning("%s", e)
            return None

    movies, shows = await asyncio.gather(lib(radarr), lib(sonarr))
    out = []
    for r in requests.items:
        library, key = (movies, r["tmdb_id"]) if r["type"] == "movie" else (shows, r.get("tvdb_id"))
        stato = SCONOSCIUTO if library is None else library.get(key, ASSENTE)
        out.append(r | {"stato": stato})
    return {"richieste": out, "servizi": get_servizi()}


@app.get("/api/richieste/{item_id}")
async def get_richiesta(item_id: str):
    item = requests.get(item_id) or catalog.find_item(item_id)
    if not item:
        raise HTTPException(404, "Titolo non trovato.")
    svc = _service(item["type"])
    if not svc.configured:
        return {"stato": None, "configurato": False, "servizio": svc.name}
    try:
        arr_id = await _arr_id(item)
        stato = await svc.status(arr_id) if arr_id else ASSENTE
    except (ArrError, TMDBError) as e:
        return {"stato": SCONOSCIUTO, "configurato": True, "servizio": svc.name, "errore": str(e)}
    return {"stato": stato, "configurato": True, "servizio": svc.name}


class NuovaRichiesta(BaseModel):
    id: str


@app.post("/api/richieste")
async def post_richiesta(body: NuovaRichiesta):
    item = catalog.find_item(body.id)
    if not item:
        raise HTTPException(404, "Titolo non trovato.")
    svc = _service(item["type"])
    if not svc.configured:
        raise HTTPException(400, f"{svc.name} non e' configurato.")
    try:
        arr_id = await _arr_id(item)
        if not arr_id:
            raise HTTPException(400, "Questa serie non ha un id TVDB, Sonarr non puo' aggiungerla.")
        stato = await svc.add(arr_id)
    except (ArrError, TMDBError) as e:
        raise HTTPException(502, str(e)) from e
    requests.add(item, tvdb_id=arr_id if item["type"] == "tv" else None)
    return {"stato": stato, "servizio": svc.name}


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
