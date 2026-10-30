import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

from .catalog import Catalog, TMDBError
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("cosaguardo")

catalog = Catalog(settings)
STATIC = Path(__file__).parent / "static"


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


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
