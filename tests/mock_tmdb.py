"""Finto server TMDB per provare l'app senza chiave e senza rete.

Avvio:  uvicorn tests.mock_tmdb:app --port 9000
Poi:    TMDB_API_KEY=test TMDB_BASE_URL=http://127.0.0.1:9000 \
        TMDB_IMAGE_URL=http://127.0.0.1:9000/img DATA_DIR=./data uvicorn app.main:app --port 8080
"""

import gzip
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

GENRE_IDS = {"Dramma": 18, "Commedia": 35, "Thriller": 53, "Azione": 28, "Fantascienza": 878, "Crime": 80,
             "Animazione": 16, "Documentario": 99, "Fantasy": 14, "Reality": 10764, "Famiglia": 10751,
             "Kids": 10762}
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

# In arrivo: la piattaforma si ricava dalla nota della data di uscita o dalla casa di produzione
for n, (days, title, note, company) in enumerate([
    (3, "Il ritorno del re di picche", "Netflix", ""),
    (6, "Lungo la costa", "Prime Video", ""),
    (12, "La cometa", "", "Apple Original Films"),
    (25, "Fiabe di mezzanotte", "Disney+", ""),
    (40, "Senza piattaforma", "", "Studio indipendente"),
    (90, "Troppo lontano", "Netflix", ""),
], start=101):
    MOVIES[1000 + n] = {"title": title, "date": (TODAY + timedelta(days=days)).isoformat(), "providers": [],
                        "note": note, "companies": [company] if company else []}
SHOWS[2101] = {"title": "Codice zero", "first": (TODAY + timedelta(days=4)).isoformat(), "last": "",
               "season": 1, "providers": [], "networks": [213]}
SHOWS[2102] = {"title": "La casa sul lago", "first": "2023-03-01", "last": "2025-05-01", "season": 2,
               "providers": [337], "next": {"air_date": (TODAY + timedelta(days=9)).isoformat(),
                                            "season_number": 3, "episode_number": 1}}
SHOWS[2103] = {"title": "Episodi settimanali", "first": "2024-01-01", "last": (TODAY - timedelta(days=3)).isoformat(),
               "season": 2, "providers": [8], "next": {"air_date": (TODAY + timedelta(days=4)).isoformat(),
                                                        "season_number": 2, "episode_number": 5}}
# Per "I migliori": stagione partita piu' di 6 mesi fa, reality show, film senza scheda italiana
SHOWS[2105] = {"title": "Saga infinita", "first": "2022-01-01", "last": (TODAY - timedelta(days=6)).isoformat(),
               "season": 3, "season_start": (TODAY - timedelta(days=300)).isoformat(), "providers": [8]}
SHOWS[2106] = {"title": "L'isola dei vip", "first": (TODAY - timedelta(days=40)).isoformat(),
               "last": (TODAY - timedelta(days=2)).isoformat(), "season": 1, "providers": [359], "genres": [10764]}
UNTRANSLATED = {1007}
FEW_IMDB_VOTES = {1003}
# Per "Bambini": film per famiglie (G/PG), animazione per adulti (PG-13), serie per bambini e non
KIDS_MOVIES = {1004: "G", 1010: "PG", 1012: "PG-13"}
SHOWS[2007]["genres"] = [10762, 16]
SHOWS[2009]["genres"] = [16, 35]
TV_RATINGS = {2007: "TV-Y", 2009: "TV-MA"}
# Per "I migliori": Tomatometer su OMDb (le serie di solito non ce l'hanno)
ROTTEN = {1001: 97, 1002: 91, 1005: 55, 1006: 88, 1008: 100, 1009: 74, 1011: 83}
SHOWS[2104] = {"title": "Orbita", "first": (TODAY + timedelta(days=20)).isoformat(), "last": "",
               "season": 1, "providers": [], "networks": [2552]}


# Titoli vecchi, fuori dal catalogo delle uscite: si trovano solo cercandoli (per "Suggeriti per te")
OLD_MOVIES = {
    1201: {"title": "Il mare d'inverno", "date": "1986-04-10", "providers": [8]},
    1202: {"title": "Notte a Bisanzio", "date": "1994-11-02", "providers": []},
}
RECOMMENDATIONS = {1001: [1201, 1002, 1202], 1002: [1202, 1001]}


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


def _and_providers(request: Request) -> bool:
    # Come TMDB: con la virgola servono tutte le piattaforme insieme, quindi nessun titolo
    return "," in request.query_params.get("with_watch_providers", "")


@app.get("/discover/movie")
def discover_movie(request: Request, page: int = 1):
    if _and_providers(request):
        return {"page": page, "total_pages": 0, "results": []}
    ids = list(MOVIES) if page == 1 else []
    if request.query_params.get("sort_by") == "popularity.desc":
        ids = list(MOVIES)[::-1]
    return {"page": page, "total_pages": 1, "results": [{"id": i} for i in ids]}


@app.get("/discover/tv")
def discover_tv(request: Request, page: int = 1):
    if _and_providers(request):
        return {"page": page, "total_pages": 0, "results": []}
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
def movie(mid: int, language: str = "it-IT"):
    if mid not in MOVIES and mid not in OLD_MOVIES:
        return Response('{"status_code":34}', status_code=404, media_type="application/json")
    m = MOVIES.get(mid) or OLD_MOVIES[mid]
    r = random.Random(mid)
    overview = ("Un racconto intenso tra segreti di famiglia e scelte impossibili, ambientato in una città che non dorme mai. "
                "Quando il passato torna a bussare, nessuno potrà più nascondersi.")
    if mid in UNTRANSLATED:
        overview = "An intense story of family secrets." if language == "en-US" else ""
    return {
        "id": mid, "title": m["title"], "original_title": m["title"].upper(), "imdb_id": f"tt{mid:07d}",
        "overview": overview,
        "poster_path": f"/p{mid}.svg", "backdrop_path": f"/b{mid}.svg",
        "release_date": m["date"], "runtime": r.randint(88, 150),
        "genres": [{"id": GENRE_IDS[g], "name": g} for g in (["Animazione", "Famiglia"] if mid in KIDS_MOVIES
                                                               else r.sample(GENRES, 2))],
        "vote_average": round(r.uniform(5.8, 8.6), 1), "vote_count": r.randint(40, 4000),
        "popularity": r.uniform(10, 400),
        "watch/providers": _wp(m["providers"]),
        "release_dates": {"results": [{"iso_3166_1": "IT", "release_dates": [
            {"type": 4, "release_date": m["date"] + "T00:00:00.000Z", "note": m.get("note", "")}]},
            {"iso_3166_1": "US", "release_dates": [
                {"type": 3, "release_date": m["date"] + "T00:00:00.000Z", "certification": KIDS_MOVIES.get(mid, "R")}]}]},
        "production_companies": [{"name": c} for c in m.get("companies", [])],
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
        "last_episode_to_air": {"air_date": s["last"], "season_number": s["season"], "episode_number": 3} if s["last"] else None,
        "next_episode_to_air": s.get("next"),
        "status": s.get("status") or ("Ended" if sid in (2003, 2008) else "Returning Series"),
        "networks": [{"id": n} for n in s.get("networks", [])],
        "seasons": [{"season_number": 0, "name": "Speciali", "episode_count": 2}] + [
            {"season_number": n, "name": f"Stagione {n}", "episode_count": 8,
             "air_date": s["first"] if n == 1 else s.get("season_start") or (TODAY - timedelta(days=r.choice([5, 60]))).isoformat()}
            for n in range(1, s["season"] + 1)],
        "content_ratings": {"results": [{"iso_3166_1": "US", "rating": TV_RATINGS[sid]}] if sid in TV_RATINGS else []},
        "genres": [{"id": g, "name": "Genere"} for g in s["genres"]] if s.get("genres")
        else [{"id": GENRE_IDS[g], "name": g} for g in r.sample(GENRES, 2)],
        "external_ids": external_ids(sid),
        "vote_average": round(r.uniform(6, 9), 1), "vote_count": r.randint(40, 4000),
        "popularity": r.uniform(10, 400),
        "watch/providers": _wp(s["providers"]),
    }


@app.get("/configuration")
def configuration(api_key: str = ""):
    if api_key.startswith("sbagliata"):
        return Response('{"status_code":7}', status_code=401, media_type="application/json")
    return {"images": {"base_url": "/img/"}}


@app.get("/{kind}/{tid}/videos")
def videos(kind: str, tid: int):
    return {"results": [
        {"site": "YouTube", "key": "teaser-en", "type": "Teaser", "iso_639_1": "en", "official": True,
         "published_at": "2026-01-01T00:00:00.000Z", "name": "Teaser"},
        {"site": "YouTube", "key": "trailer-en", "type": "Trailer", "iso_639_1": "en", "official": True,
         "published_at": "2026-02-01T00:00:00.000Z", "name": "Official Trailer"},
        {"site": "YouTube", "key": "aqz-KE-bpKQ", "type": "Trailer", "iso_639_1": "it", "official": True,
         "published_at": "2026-02-02T00:00:00.000Z", "name": "Trailer ufficiale italiano"},
        {"site": "Vimeo", "key": "x", "type": "Trailer", "iso_639_1": "it"},
    ] if tid % 5 else []}


@app.get("/search/{kind}")
def search(kind: str, query: str = "", year: int | None = None, first_air_date_year: int | None = None):
    pool = (MOVIES | OLD_MOVIES) if kind == "movie" else SHOWS
    wanted = year or first_air_date_year
    found = [i for i, m in pool.items() if m["title"].lower() == query.lower()
             and (not wanted or (m.get("date") or m.get("first") or "").startswith(str(wanted)))]
    return {"results": [{"id": i} for i in found]}


@app.get("/movie/{mid}/recommendations")
def recommendations(mid: int):
    return {"results": [{"id": i} for i in RECOMMENDATIONS.get(mid, [])]}


@app.get("/movie/{mid}/external_ids")
def movie_external_ids(mid: int):
    return {"imdb_id": f"tt{mid:07d}"}


@app.get("/tv/{sid}/external_ids")
def external_ids(sid: int):
    return {"tvdb_id": 900000 + sid, "imdb_id": f"tt{sid:07d}"}


@app.get("/imdb/title.ratings.tsv.gz")
def imdb_ratings():
    """Come il dataset ufficiale di IMDb: tconst, averageRating, numVotes."""
    rows = ["tconst\taverageRating\tnumVotes", "tt9999999\t5.0\t10"]
    for tid in sorted(MOVIES) + sorted(SHOWS):
        r = random.Random(tid * 31)
        rating = 9.1 if tid in (1003, 1007, 2105, 2106) else round(r.uniform(6.0, 8.8), 1)
        votes = 120 if tid in FEW_IMDB_VOTES else r.randint(1500, 90000)
        rows.append(f"tt{tid:07d}\t{rating}\t{votes}")
    return Response(gzip.compress(("\n".join(rows) + "\n").encode()), media_type="application/gzip")


@app.get("/omdb/")
def omdb(apikey: str = "", i: str = ""):
    """Come OMDb: il Tomatometer e' tra i "Ratings"."""
    if apikey.startswith("sbagliata"):
        return Response('{"Response":"False","Error":"Invalid API key!"}', status_code=401,
                        media_type="application/json")
    tid = int(i[2:]) if i.startswith("tt") else 0
    if i == "tt0111161":
        tid, ratings = 0, [{"Source": "Rotten Tomatoes", "Value": "89%"}]
    else:
        ratings = [{"Source": "Internet Movie Database", "Value": "8.0/10"}]
        if tid in ROTTEN:
            ratings.append({"Source": "Rotten Tomatoes", "Value": f"{ROTTEN[tid]}%"})
    return {"Response": "True", "imdbID": i, "Ratings": ratings}
