"""Finto server TMDB per provare l'app senza chiave e senza rete.

Avvio:  uvicorn tests.mock_tmdb:app --port 9000
Poi:    TMDB_API_KEY=test TMDB_BASE_URL=http://127.0.0.1:9000 \
        TMDB_IMAGE_URL=http://127.0.0.1:9000/img DATA_DIR=./data uvicorn app.main:app --port 8080
"""

import hashlib
import random
from datetime import date, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import Response

app = FastAPI()
TODAY = date.today()

PROVIDERS = [
    (8, "Netflix"), (1796, "Netflix Standard with Ads"), (119, "Amazon Prime Video"),
    (337, "Disney Plus"), (531, "Paramount Plus"), (29, "Sky Go"), (39, "Now TV"),
    (350, "Apple TV Plus"), (10, "Amazon Video"), (2, "Apple TV"), (109, "Timvision"),
    (222, "Rai Play"), (359, "Mediaset Infinity"),
]

GENRES = ["Dramma", "Commedia", "Thriller", "Azione", "Fantascienza", "Crime", "Animazione", "Documentario", "Fantasy"]
WORDS = ["Notte", "Ombra", "Mare", "Città", "Fuoco", "Silenzio", "Vento", "Stelle", "Confine", "Segreto",
         "Ritorno", "Inverno", "Cuore", "Luce", "Destino", "Labirinto"]
MOVIE_TITLES = [
    "La notte di Roma", "Oltre il confine", "L'ultima estate", "Il segreto del mare", "Fuoco nel vento",
    "Le stelle di Napoli", "Il silenzio rosso", "Ritorno a casa", "Cuore d'inverno", "La città invisibile",
    "Il labirinto", "Destino perduto", "Ombre sul lago", "La luce del Nord", "Una vita in fuga",
    "Il colpo perfetto", "Viaggio a Sud", "La regina di cuori", "Ultima chiamata", "Il giardino segreto",
    "Notturno", "La grande corsa", "Mezzanotte a Milano", "Il guardiano",
]
TV_WORDS = ["Cronache", "Gli eredi", "Distretto", "La casa", "Codice", "Linea", "Frontiera", "Il clan"]

rnd = random.Random(7)
MOVIES: dict[int, dict] = {}
SHOWS: dict[int, dict] = {}

for n in range(1, 25):
    d = TODAY - timedelta(days=rnd.choice([0, 0, 1, 1, 2, 3, 5, 6, 9, 12, 20, 35, 60]))
    MOVIES[1000 + n] = {
        "title": MOVIE_TITLES[n - 1],
        "date": d.isoformat(),
        "providers": rnd.sample([8, 119, 337, 531, 29, 350, 109], k=rnd.choice([1, 1, 1, 2, 3])),
    }
for n in range(1, 19):
    first = TODAY - timedelta(days=rnd.choice([3, 10, 200, 400, 800]))
    last = TODAY - timedelta(days=rnd.choice([0, 1, 2, 4, 8, 15]))
    SHOWS[2000 + n] = {
        "title": f"{rnd.choice(TV_WORDS)} {rnd.choice(WORDS).lower()}",
        "first": first.isoformat(),
        "last": last.isoformat(),
        "season": 1 if (TODAY - first).days < 30 else rnd.randint(2, 5),
        "providers": rnd.sample([8, 1796, 119, 337, 39, 29, 222, 359], k=rnd.choice([1, 1, 2])),
    }
# Stesso film su piu' piattaforme (verifica dei doppioni)
MOVIES[1001]["providers"] = [8, 119, 39]


def color(seed: str) -> tuple[str, str]:
    h = hashlib.md5(seed.encode()).hexdigest()
    return f"#{h[:6]}", f"#{h[6:12]}"


@app.get("/img/{size}/{name}")
def img(size: str, name: str):
    a, b = color(name)
    wide = name.startswith("b")
    w, h = (1280, 720) if wide else (342, 513)
    title = name.split(".")[0][1:]
    svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}' viewBox='0 0 {w} {h}'>
<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='{a}'/><stop offset='1' stop-color='{b}'/></linearGradient>
<radialGradient id='r' cx='.3' cy='.25' r='.8'><stop offset='0' stop-color='#fff' stop-opacity='.35'/><stop offset='1' stop-color='#000' stop-opacity='.55'/></radialGradient></defs>
<rect width='100%' height='100%' fill='url(#g)'/><rect width='100%' height='100%' fill='url(#r)'/>
<circle cx='{w*0.7}' cy='{h*0.35}' r='{min(w,h)*0.22}' fill='#fff' fill-opacity='.12'/>
<text x='50%' y='{h*0.82}' fill='#fff' fill-opacity='.85' font-family='Georgia' font-size='{w/9}' text-anchor='middle'>{title}</text></svg>"""
    if name.startswith("l"):
        pid = int(title)
        colors = {8: "#E50914", 1796: "#E50914", 119: "#00A8E1", 337: "#113CCF", 531: "#0064FF", 29: "#00C8B4",
                  39: "#111", 350: "#333", 109: "#004691", 222: "#0A6EBD", 359: "#7B2CBF"}
        letter = dict(PROVIDERS)[pid][0]
        svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='92' height='92'><rect width='92' height='92' rx='18' fill='{colors.get(pid, '#444')}'/>
<text x='46' y='62' fill='#fff' font-family='Arial' font-weight='bold' font-size='48' text-anchor='middle'>{letter}</text></svg>"""
    return Response(svg, media_type="image/svg+xml")


@app.get("/watch/providers/{kind}")
def providers(kind: str):
    return {"results": [
        {"provider_id": i, "provider_name": n, "logo_path": f"/l{i}.svg", "display_priorities": {"IT": k}}
        for k, (i, n) in enumerate(PROVIDERS)
    ]}


def _wp(ids):
    return {"results": {"IT": {
        "link": "https://www.themoviedb.org/watch",
        "flatrate": [{"provider_id": i} for i in ids if i not in (222,)],
        "free": [{"provider_id": i} for i in ids if i == 222],
    }}}


@app.get("/discover/movie")
def discover_movie(request: Request, page: int = 1):
    ids = list(MOVIES) if page == 1 else []
    if request.query_params.get("sort_by") == "popularity.desc":
        ids = list(MOVIES)[::-1]
    return {"page": page, "total_pages": 1, "results": [{"id": i} for i in ids]}


@app.get("/discover/tv")
def discover_tv(page: int = 1):
    return {"page": page, "total_pages": 1, "results": [{"id": i} for i in SHOWS]}


@app.get("/trending/all/week")
def trending(page: int = 1):
    if page > 1:
        return {"results": []}
    mix = []
    for m, s in zip(list(MOVIES)[3:], list(SHOWS)[::-1]):
        mix += [{"id": m, "media_type": "movie"}, {"id": s, "media_type": "tv"}]
    return {"results": mix[:20] + [{"id": 1, "media_type": "person"}]}


@app.get("/movie/{mid}")
def movie(mid: int):
    m = MOVIES[mid]
    r = random.Random(mid)
    return {
        "id": mid, "title": m["title"], "original_title": m["title"].upper(),
        "overview": "Un racconto intenso tra segreti di famiglia e scelte impossibili, ambientato in una città che non dorme mai. "
                    "Quando il passato torna a bussare, nessuno potrà più nascondersi.",
        "poster_path": f"/p{mid}.svg", "backdrop_path": f"/b{mid}.svg",
        "release_date": m["date"], "runtime": r.randint(88, 150),
        "genres": [{"name": g} for g in r.sample(GENRES, 2)],
        "vote_average": round(r.uniform(5.8, 8.6), 1), "vote_count": r.randint(40, 4000),
        "popularity": r.uniform(10, 400),
        "watch/providers": _wp(m["providers"]),
        "release_dates": {"results": [{"iso_3166_1": "IT", "release_dates": [{"type": 4, "release_date": m["date"] + "T00:00:00.000Z"}]}]},
    }


@app.get("/tv/{sid}")
def tv(sid: int):
    s = SHOWS[sid]
    r = random.Random(sid)
    return {
        "id": sid, "name": s["title"], "original_name": s["title"],
        "overview": "Una serie corale che segue le vite intrecciate di un gruppo di persone alle prese con un mistero che cambierà tutto.",
        "poster_path": f"/p{sid}.svg", "backdrop_path": f"/b{sid}.svg",
        "first_air_date": s["first"], "number_of_seasons": s["season"],
        "last_episode_to_air": {"air_date": s["last"], "season_number": s["season"], "episode_number": 3},
        "seasons": [{"season_number": n, "air_date": s["first"] if n == 1 else (TODAY - timedelta(days=r.choice([5, 60]))).isoformat()}
                    for n in range(1, s["season"] + 1)],
        "genres": [{"name": g} for g in r.sample(GENRES, 2)],
        "vote_average": round(r.uniform(6, 9), 1), "vote_count": r.randint(40, 4000),
        "popularity": r.uniform(10, 400),
        "watch/providers": _wp(s["providers"]),
    }


@app.get("/configuration")
def configuration(api_key: str = ""):
    if api_key.startswith("sbagliata"):
        return Response('{"status_code":7}', status_code=401, media_type="application/json")
    return {"images": {"base_url": "/img/"}}
