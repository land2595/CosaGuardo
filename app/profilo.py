"""La memoria dei gusti usata dall'AI per "Suggeriti per te".

- testo: un profilo dei gusti in punti brevi. Lo riscrive l'AI a ogni aggiornamento dei
  suggerimenti (nella stessa richiesta, senza costi in piu'), tenendo conto di giudizi, note,
  titoli visti, liste e di come hai reagito ai suggerimenti precedenti. Si puo' leggere e
  correggere dalla pagina: l'AI conserva le correzioni.
- storico: i titoli gia' suggeriti, per non riproporli e per capire quali sono piaciuti.

Salvata in /data/profilo.json.
"""

import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger("profilo")

TEXT_MAX = 3000       # caratteri del profilo
VERSION = 2           # dalla 2 la memoria scritta dall'AI si basa solo su mi piace, stelle e note
HISTORY_MAX = 1500    # titoli suggeriti ricordati


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Profilo:
    def __init__(self, data_dir: str):
        self.file = os.path.join(data_dir, "profilo.json")
        self.data: dict = {"testo": "", "aggiornato": None, "autore": None, "storico": [], "versione": VERSION}
        try:
            with open(self.file, encoding="utf-8") as f:
                saved = json.load(f)
            self.data |= saved
            if saved.get("versione", 1) < VERSION and saved.get("autore") == "ai":
                # Memoria scritta con le regole vecchie (poteva citare titoli mai giudicati): si ricrea
                self.data |= {"testo": "", "aggiornato": None, "autore": None}
            self.data["versione"] = VERSION
        except (OSError, ValueError):
            pass

    @property
    def text(self) -> str:
        return self.data.get("testo") or ""

    @property
    def history(self) -> list[dict]:
        """Dal suggerimento piu' recente."""
        return list(reversed(self.data.get("storico") or []))

    @property
    def history_ids(self) -> set[str]:
        return {h["id"] for h in self.data.get("storico") or []}

    @property
    def edited_by_user(self) -> str | None:
        """Quando l'hai corretto tu l'ultima volta (None se l'ultima versione e' dell'AI)."""
        return self.data["aggiornato"] if self.data.get("autore") == "utente" else None

    def public(self) -> dict:
        return {"testo": self.text, "aggiornato": self.data.get("aggiornato"), "autore": self.data.get("autore"),
                "suggeriti": len(self.data.get("storico") or [])}

    def set_text(self, text: str, author: str) -> None:
        text = "\n".join(line.rstrip() for line in str(text or "").strip().splitlines())
        self.data |= {"testo": text[:TEXT_MAX], "aggiornato": _now(), "autore": author if text else None}
        self._save()

    def add_history(self, items: list[dict]) -> None:
        known = self.history_ids
        when = _now()[:10]
        storico = self.data.setdefault("storico", [])
        for it in items:
            if it.get("id") and it["id"] not in known:
                known.add(it["id"])
                storico.append({"id": it["id"], "title": it.get("title"), "year": it.get("year"),
                                "type": it.get("type"), "data": when})
        del storico[:-HISTORY_MAX]
        self._save()

    def reset_history(self) -> None:
        self.data["storico"] = []
        self._save()

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.file), exist_ok=True)
            tmp = self.file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.file)
        except OSError as e:
            log.warning("Impossibile salvare il profilo: %s", e)
