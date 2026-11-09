"""I gusti personali: mi piace, voto da 0 a 5 stelle e note per ogni film o serie.

Sono salvati in /data/gusti.json insieme a una copia della scheda del titolo, cosi' si
possono mostrare anche quando il titolo non e' piu' tra le uscite del catalogo.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

log = logging.getLogger("gusti")

ID_RE = re.compile(r"^(movie|tv)-\d+$")
NOTE_MAX = 2000
# Campi della scheda conservati con il giudizio
SCHEDA = ("id", "tmdb_id", "type", "title", "original_title", "year", "date", "badge", "poster", "backdrop",
          "overview", "genres", "categorie", "platforms", "watch_link", "tmdb_url", "imdb_id", "runtime",
          "seasons", "voti")


class GustiError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Gusti:
    def __init__(self, data_dir: str):
        self.file = os.path.join(data_dir, "gusti.json")
        self.data: dict = {"titoli": {}, "modificato": None}
        try:
            with open(self.file, encoding="utf-8") as f:
                self.data |= json.load(f)
        except (OSError, ValueError):
            pass

    @property
    def items(self) -> list[dict]:
        """Dal giudizio piu' recente."""
        return sorted(self.data["titoli"].values(), key=lambda g: g.get("aggiornato") or "", reverse=True)

    @property
    def modified(self) -> str | None:
        """Quando sono cambiati l'ultima volta (serve a capire se rifare i suggerimenti)."""
        return self.data.get("modificato")

    def get(self, item_id: str) -> dict | None:
        return self.data["titoli"].get(item_id)

    def update(self, item_id: str, scheda: dict | None, values: dict) -> dict | None:
        """Aggiorna mi piace, voto e note. Restituisce None se il titolo non ha piu' nessun giudizio."""
        if not ID_RE.match(item_id):
            raise GustiError("Titolo non valido.")
        current = self.get(item_id) or {}
        entry = {k: v for k, v in (scheda or {}).items() if k in SCHEDA}
        entry = {**{k: current[k] for k in SCHEDA if k in current}, **entry}
        kind, _, tmdb = item_id.partition("-")
        entry |= {"id": item_id, "type": kind, "tmdb_id": int(tmdb)}
        entry["mi_piace"] = bool(values["mi_piace"]) if "mi_piace" in values else bool(current.get("mi_piace"))
        if "voto" in values:
            voto = values["voto"]
            if voto is not None and (not isinstance(voto, int) or isinstance(voto, bool) or not 0 <= voto <= 5):
                raise GustiError("Il voto va da 0 a 5 stelle.")
            entry["voto"] = voto
        else:
            entry["voto"] = current.get("voto")
        note = values["note"] if "note" in values else current.get("note", "")
        entry["note"] = str(note or "").strip()[:NOTE_MAX]
        if not entry.get("title"):
            raise GustiError("Manca il titolo.")

        if not entry["mi_piace"] and entry["voto"] is None and not entry["note"]:
            self.data["titoli"].pop(item_id, None)
            result = None
        else:
            entry["aggiornato"] = _now()
            self.data["titoli"][item_id] = entry
            result = entry
        self.data["modificato"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        self._save()
        return result

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.file), exist_ok=True)
            tmp = self.file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.file)
        except OSError as e:
            log.warning("Impossibile salvare i gusti: %s", e)
