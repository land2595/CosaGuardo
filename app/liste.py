"""Le liste personali: la watchlist (cose che vorrei vedere) e i titoli ignorati.

- watchlist: titoli da vedere prima o poi, senza richiederli subito a Radarr/Sonarr.
- ignorati: film e serie che sicuramente non guarderai. Spariscono da tutte le sezioni e
  non vengono piu' suggeriti; si possono ripristinare dalla sezione "Le mie liste".

Sono salvate in /data/liste.json con una copia della scheda, come i gusti.
"""

import json
import logging
import os
from datetime import datetime, timezone

from .gusti import ID_RE, SCHEDA

log = logging.getLogger("liste")

LISTE = ("watchlist", "ignorati")


class ListeError(ValueError):
    pass


class Liste:
    def __init__(self, data_dir: str):
        self.file = os.path.join(data_dir, "liste.json")
        self.data: dict = {nome: {} for nome in LISTE}
        try:
            with open(self.file, encoding="utf-8") as f:
                saved = json.load(f)
            for nome in LISTE:
                if isinstance(saved.get(nome), dict):
                    self.data[nome] = saved[nome]
        except (OSError, ValueError, AttributeError):
            pass

    def items(self, nome: str) -> list[dict]:
        """Dal piu' recente."""
        return sorted(self.data[nome].values(), key=lambda e: e.get("aggiunto_il") or "", reverse=True)

    def ids(self, nome: str) -> set[str]:
        return set(self.data[nome])

    def get(self, item_id: str) -> dict | None:
        return next((self.data[n][item_id] for n in LISTE if item_id in self.data[n]), None)

    def add(self, nome: str, item_id: str, scheda: dict | None) -> dict:
        """Aggiunge un titolo a una lista (e lo toglie dall'altra: o lo vuoi vedere o no)."""
        if nome not in LISTE:
            raise ListeError("Lista non valida.")
        if not ID_RE.match(item_id):
            raise ListeError("Titolo non valido.")
        current = self.get(item_id) or {}
        entry = {k: current[k] for k in SCHEDA if k in current}
        entry |= {k: v for k, v in (scheda or {}).items() if k in SCHEDA}
        kind, _, tmdb = item_id.partition("-")
        entry |= {"id": item_id, "type": kind, "tmdb_id": int(tmdb)}
        if not entry.get("title"):
            raise ListeError("Manca il titolo.")
        entry["aggiunto_il"] = self.data[nome].get(item_id, {}).get("aggiunto_il") or \
            datetime.now(timezone.utc).isoformat(timespec="seconds")
        for other in LISTE:
            if other != nome:
                self.data[other].pop(item_id, None)
        self.data[nome][item_id] = entry
        self._save()
        return entry

    def remove(self, nome: str, item_id: str) -> bool:
        if nome not in LISTE:
            raise ListeError("Lista non valida.")
        if self.data[nome].pop(item_id, None) is None:
            return False
        self._save()
        return True

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.file), exist_ok=True)
            tmp = self.file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.file)
        except OSError as e:
            log.warning("Impossibile salvare le liste: %s", e)
