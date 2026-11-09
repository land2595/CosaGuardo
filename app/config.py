import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


# Piattaforme mostrate nell'app. Ogni piattaforma raggruppa le varianti con cui
# TMDB/JustWatch la elencano in Italia (es. "Netflix" e "Netflix Standard with Ads").
# La chiave e' l'identificativo usato nei filtri, "match" sono sottostringhe
# (minuscole) cercate nel nome del provider restituito da TMDB.
PLATFORMS: dict[str, dict] = {
    "netflix": {"name": "Netflix", "color": "#E50914", "match": ["netflix"]},
    "prime": {"name": "Prime Video", "color": "#00A8E1", "match": ["amazon prime video", "prime video"]},
    "disney": {"name": "Disney+", "color": "#113CCF", "match": ["disney plus", "disney+"]},
    "paramount": {"name": "Paramount+", "color": "#0064FF", "match": ["paramount plus", "paramount+"]},
    "sky": {"name": "Sky / NOW", "color": "#00C8B4", "match": ["sky go", "now tv", "sky "]},
    "apple": {"name": "Apple TV+", "color": "#A2AAAD", "match": ["apple tv plus", "apple tv+"]},
    "timvision": {"name": "TimVision", "color": "#004691", "match": ["timvision"]},
    "raiplay": {"name": "RaiPlay", "color": "#0A6EBD", "match": ["rai play", "raiplay"]},
    "mediaset": {"name": "Mediaset Infinity", "color": "#7B2CBF", "match": ["mediaset infinity", "infinity"]},
    "discovery": {"name": "discovery+", "color": "#2175D9", "match": ["discovery+", "discovery plus"]},
}


@dataclass
class Settings:
    tmdb_api_key: str = field(default_factory=lambda: os.getenv("TMDB_API_KEY", "").strip())
    tmdb_base_url: str = field(default_factory=lambda: os.getenv("TMDB_BASE_URL", "https://api.themoviedb.org/3"))
    region: str = field(default_factory=lambda: os.getenv("REGION", "IT"))
    language: str = field(default_factory=lambda: os.getenv("LANGUAGE", "it-IT"))
    # Finestra temporale (giorni) entro cui un titolo e' considerato "uscita recente"
    movie_days: int = field(default_factory=lambda: _int("MOVIE_DAYS", 90))
    tv_days: int = field(default_factory=lambda: _int("TV_DAYS", 30))
    # Pagine di TMDB discover lette per ogni ricerca (20 titoli per pagina)
    pages: int = field(default_factory=lambda: _int("PAGES", 5))
    # Giorni in avanti mostrati in "Prossimamente"
    upcoming_days: int = field(default_factory=lambda: _int("UPCOMING_DAYS", 60))
    # "I migliori": quanti anni indietro guardare e ogni quante ore ricalcolare la classifica
    best_years: int = field(default_factory=lambda: _int("BEST_YEARS", 10))
    best_hours: int = field(default_factory=lambda: _int("BEST_HOURS", 24))
    # Voti Rotten Tomatoes via OMDb (chiave gratuita su omdbapi.com, 1000 richieste al giorno)
    omdb_api_key: str = field(default_factory=lambda: os.getenv("OMDB_API_KEY", "").strip())
    omdb_daily_limit: int = field(default_factory=lambda: _int("OMDB_DAILY_LIMIT", 900))
    # Voti IMDb per "I migliori" (dataset pubblico, scaricato una volta al giorno)
    imdb_ratings: bool = field(default_factory=lambda: os.getenv("IMDB_RATINGS", "1").strip().lower() not in ("0", "false", "no"))
    imdb_ratings_url: str = field(default_factory=lambda: os.getenv("IMDB_RATINGS_URL", "https://datasets.imdbws.com/title.ratings.tsv.gz"))
    refresh_minutes: int = field(default_factory=lambda: _int("REFRESH_MINUTES", 60))
    data_dir: str = field(default_factory=lambda: os.getenv("DATA_DIR", "/data"))
    # Richieste: i film vanno a Radarr, le serie a Sonarr (facoltativi)
    radarr_url: str = field(default_factory=lambda: os.getenv("RADARR_URL", "").strip().rstrip("/"))
    radarr_api_key: str = field(default_factory=lambda: os.getenv("RADARR_API_KEY", "").strip())
    radarr_quality_profile: str = field(default_factory=lambda: os.getenv("RADARR_QUALITY_PROFILE", "").strip())
    radarr_root_folder: str = field(default_factory=lambda: os.getenv("RADARR_ROOT_FOLDER", "").strip())
    sonarr_url: str = field(default_factory=lambda: os.getenv("SONARR_URL", "").strip().rstrip("/"))
    sonarr_api_key: str = field(default_factory=lambda: os.getenv("SONARR_API_KEY", "").strip())
    sonarr_quality_profile: str = field(default_factory=lambda: os.getenv("SONARR_QUALITY_PROFILE", "").strip())
    sonarr_root_folder: str = field(default_factory=lambda: os.getenv("SONARR_ROOT_FOLDER", "").strip())
    sonarr_language_profile: str = field(default_factory=lambda: os.getenv("SONARR_LANGUAGE_PROFILE", "").strip())
    # PIN del menu admin: se vuoto si sceglie dalla pagina la prima volta
    admin_pin: str = field(default_factory=lambda: os.getenv("ADMIN_PIN", "").strip())
    plex_url: str = field(default_factory=lambda: os.getenv("PLEX_URL", "").strip().rstrip("/"))
    plex_token: str = field(default_factory=lambda: os.getenv("PLEX_TOKEN", "").strip())
    # "Suggeriti per te": chiave API di Anthropic (Claude) e modello. Si possono impostare dal menu admin.
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "").strip())
    ai_model: str = field(default_factory=lambda: os.getenv("AI_MODEL", "").strip())
    # Yamtrack (facoltativo): titoli gia' visti, per non suggerirli e capire meglio i gusti
    yamtrack_url: str = field(default_factory=lambda: os.getenv("YAMTRACK_URL", "").strip().rstrip("/"))
    yamtrack_user: str = field(default_factory=lambda: os.getenv("YAMTRACK_USER", "").strip())
    yamtrack_password: str = field(default_factory=lambda: os.getenv("YAMTRACK_PASSWORD", ""))
    enabled_platforms: list[str] = field(
        default_factory=lambda: [
            p.strip()
            for p in os.getenv("PLATFORMS", ",".join(PLATFORMS)).split(",")
            if p.strip() in PLATFORMS
        ]
    )


settings = Settings()
