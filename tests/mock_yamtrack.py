"""Finto Yamtrack: pagina di accesso con CSRF, sessione e esportazione CSV."""

from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

app = FastAPI()
USER, PASSWORD = "federico", "segreta"
CSV = """media_id,source,media_type,title,image,season_number,episode_number,score,status,progress,notes,start_date,end_date,created_at,progressed_at
1396,tmdb,tv,Breaking Bad,,,,9.5,Completed,62,,,,,
2001,tmdb,season,Cronache notte,,1,,,In progress,3,,,,,
1002,tmdb,movie,Oltre il confine,,,,4,Completed,1,,,,,
1003,tmdb,movie,L'ultima estate,,,,,Planning,0,,,,,
21,mal,anime,Cowboy Bebop,,,,10,Completed,26,,,,,
"""


@app.get("/accounts/login/")
def login_page():
    r = HTMLResponse('<form method="post"><input type="hidden" name="csrfmiddlewaretoken" value="tok123">'
                     '<input name="login"><input name="password"></form>')
    r.set_cookie("csrftoken", "cookie-tok")
    return r


@app.post("/accounts/login/")
async def login(request: Request):
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    if form.get("csrfmiddlewaretoken") != "tok123" or form.get("login") != USER or form.get("password") != PASSWORD:
        return HTMLResponse("<p>Il nome utente e/o la password non sono corretti.</p>")
    r = RedirectResponse("/", status_code=302)
    r.set_cookie("sessionid", "ok")
    return r


@app.get("/")
def home():
    return HTMLResponse("<p>Home</p>")


@app.get("/export/csv")
def export(request: Request):
    if request.cookies.get("sessionid") != "ok":
        return RedirectResponse("/accounts/login/?next=/export/csv", status_code=302)
    return Response(CSV, media_type="text/csv")
