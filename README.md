# 🎬 CosaGuardo

**Le ultime uscite in streaming in Italia, in un unico posto.**

CosaGuardo raccoglie i film e le serie TV appena usciti sulle piattaforme italiane (Netflix, Prime Video, Disney+, Sky/NOW, Paramount+, Apple TV+, TimVision, RaiPlay, Mediaset Infinity, discovery+) e li mostra in un'interfaccia in stile sala cinematografica.

- **Solo il catalogo italiano**: regione `IT`, titoli, trame e generi in italiano.
- **Nessun doppione**: un titolo disponibile su più piattaforme compare una sola volta, con il logo di ciascuna piattaforma.
- **Sempre aggiornato**: il catalogo si aggiorna da solo ogni ora (configurabile), e la pagina si ricarica quando ci sono novità.
- **Di tendenza in Italia**: la classifica dei titoli più visti della settimana disponibili sulle piattaforme italiane.
- Filtri per film o serie e per piattaforma, ricerca per titolo, schede con trama, voto e link "dove guardarlo".
- Le serie hanno un'etichetta che dice cosa c'è di nuovo: *Nuova serie*, *Nuova stagione* o *Nuovi episodi*.

![Home](docs/home.png)

| Di tendenza in Italia | Scheda del titolo | Mobile |
|---|---|---|
| ![Tendenze](docs/tendenze.png) | ![Scheda](docs/scheda.png) | ![Mobile](docs/mobile.png) |

> Gli screenshot sono stati fatti con dati di prova (locandine segnaposto). Con la chiave TMDB vedrai locandine e titoli reali.

## Avvio rapido

Serve solo Docker.

1. **Ottieni una chiave TMDB gratuita**: registrati su [themoviedb.org](https://www.themoviedb.org/signup) e richiedi la chiave da [Impostazioni → API](https://www.themoviedb.org/settings/api). Va bene sia la *API Key* sia la *API Read Access Token*.

2. **Scarica il progetto e configura la chiave**:

   ```bash
   git clone https://github.com/land2595/cosaguardo.git
   cd cosaguardo
   cp .env.example .env
   # apri .env e incolla la chiave in TMDB_API_KEY=
   ```

3. **Avvia**:

   ```bash
   docker compose up -d
   ```

4. Apri **http://localhost:8080**. Il primo caricamento del catalogo richiede circa 30 secondi.

Per aggiornare all'ultima versione:

```bash
docker compose pull && docker compose up -d
```

### Senza clonare il repository

L'immagine viene pubblicata automaticamente su GitHub Container Registry:

```bash
docker run -d --name cosaguardo -p 8080:8080 \
  -e TMDB_API_KEY=la_tua_chiave \
  -v cosaguardo-data:/data \
  --restart unless-stopped \
  ghcr.io/land2595/cosaguardo:latest
```

L'immagine è disponibile sia per `amd64` sia per `arm64`, quindi gira anche su Raspberry Pi e Mac con Apple Silicon.

## Configurazione

Tutte le opzioni si impostano nel file `.env` (o come variabili d'ambiente).

| Variabile | Predefinito | Descrizione |
|---|---|---|
| `TMDB_API_KEY` | | **Obbligatoria.** Chiave TMDB (v3) o token di lettura (v4). |
| `PORT` | `8080` | Porta su cui esporre l'app (solo con docker compose). |
| `REFRESH_MINUTES` | `60` | Ogni quanti minuti aggiornare il catalogo. |
| `MOVIE_DAYS` | `90` | Un film è "uscita recente" se è uscito negli ultimi N giorni. |
| `TV_DAYS` | `30` | Una serie è "uscita recente" se ha avuto episodi negli ultimi N giorni. |
| `PLATFORMS` | tutte | Piattaforme da mostrare, separate da virgola: `netflix,prime,disney,paramount,sky,apple,timvision,raiplay,mediaset,discovery`. |
| `PAGES` | `5` | Pagine di risultati lette da TMDB per ogni ricerca (20 titoli per pagina). |
| `REGION` / `LANGUAGE` | `IT` / `it-IT` | Paese del catalogo e lingua dei metadati. |

Hulu non è presente perché non esiste in Italia: i suoi contenuti escono su Disney+.

## Come funziona

Ad ogni aggiornamento, CosaGuardo:

1. legge da TMDB l'elenco dei servizi di streaming attivi in Italia e li raggruppa per piattaforma (ad esempio "Netflix" e "Netflix con pubblicità" diventano un'unica piattaforma Netflix);
2. cerca i **film** usciti di recente e quelli arrivati in **digitale/TV in Italia** di recente, disponibili in abbonamento o gratis su quelle piattaforme;
3. cerca le **serie** nuove e quelle con **episodi o stagioni nuove**;
4. per ogni titolo legge la scheda in italiano e le piattaforme italiane su cui è disponibile, poi unisce i doppioni;
5. ordina tutto dalla data di uscita più recente e salva il risultato in `/data/catalogo.json`, così al riavvio la pagina è subito pronta.

I dati di disponibilità arrivano da [JustWatch](https://www.justwatch.com/it) tramite le API di [TMDB](https://www.themoviedb.org).

**Un limite da sapere:** TMDB non indica il giorno in cui un titolo è stato aggiunto a una piattaforma. Per i film CosaGuardo usa la data di uscita digitale in Italia quando c'è (altrimenti la data di uscita), per le serie la data dell'ultimo episodio. Un film vecchio appena aggiunto al catalogo di una piattaforma quindi non compare tra le ultime uscite.

## API

| Endpoint | Descrizione |
|---|---|
| `GET /api/catalogo` | Ultime uscite, tendenze e piattaforme. Filtri opzionali: `tipo=movie\|tv`, `piattaforma=netflix` (ripetibile), `q=titolo`. |
| `GET /api/stato` | Ora dell'ultimo aggiornamento ed eventuali errori. |
| `POST /api/aggiorna` | Forza un aggiornamento immediato. |
| `GET /health` | Controllo di salute per Docker. |

## Sviluppo

```bash
pip install -r requirements.txt pytest
pytest -q tests

# Prova senza chiave e senza rete, con un finto server TMDB
uvicorn tests.mock_tmdb:app --port 9000 &
TMDB_API_KEY=test TMDB_BASE_URL=http://127.0.0.1:9000 \
TMDB_IMAGE_URL=http://127.0.0.1:9000/img DATA_DIR=./data \
uvicorn app.main:app --reload --port 8080
```

Struttura:

```
app/
  config.py      piattaforme e impostazioni
  catalog.py     raccolta dati da TMDB, unione dei doppioni, cache
  main.py        server FastAPI e aggiornamento periodico
  static/        interfaccia (HTML, CSS, JS, font)
tests/           test e finto server TMDB
```

---

Questo prodotto usa le API di TMDB ma non è approvato né certificato da TMDB. I font Bebas Neue e Inter sono distribuiti con licenza SIL Open Font License.
