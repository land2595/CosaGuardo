"""La sezione "Suggeriti per te".

Con una chiave API di Anthropic i suggerimenti li sceglie Claude, partendo da mi piace, voti e
note (e dai titoli gia' visti su Yamtrack), pescando tra film e serie degli ultimi 50 anni.
Ogni titolo suggerito viene poi cercato su TMDB per avere locandina, trama e piattaforme italiane.
Senza chiave si usano i "consigliati" di TMDB dei titoli che ti sono piaciuti.
"""

import asyncio
import json
import logging
import os
import time
from collections import Counter
from datetime import date, datetime, timezone

import anthropic

log = logging.getLogger("consigli")

DEFAULT_MODEL = "claude-opus-5-5"
MODELS = [
    {"id": "claude-opus-5-5", "nome": "Claude Opus 5.5 (consigliato)"},
    {"id": "claude-sonnet-5-5", "nome": "Claude Sonnet 5.5 (piu' economico)"},
    {"id": "claude-haiku-5-5", "nome": "Claude Haiku 5.5 (il piu' economico)"},
]
# Modelli che accettano il ripiego automatico su un altro modello se la richiesta viene rifiutata
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
HOW_MANY = 30
YEARS = 50
RETRY_SECONDS = 120   # dopo un errore non si riprova da soli prima di 2 minuti

SCHEMA = {
    "type": "object",
    "properties": {
        "suggerimenti": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titolo": {"type": "string", "description": "Titolo italiano, se esiste"},
                    "titolo_originale": {"type": "string"},
                    "anno": {"type": "integer", "description": "Anno di uscita (per le serie, del debutto)"},
                    "tipo": {"type": "string", "enum": ["film", "serie"]},
                    "motivo": {"type": "string", "description": "Perche' piacera', in italiano, 1-2 frasi"},
                },
                "required": ["titolo", "titolo_originale", "anno", "tipo", "motivo"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["suggerimenti"],
    "additionalProperties": False,
}

SYSTEM = (
    "Sei un critico cinematografico e televisivo con una conoscenza enciclopedica di film e serie TV "
    "di tutto il mondo. Consigli titoli a una persona che vive in Italia partendo dai suoi gusti reali: "
    "cosa le e' piaciuto, i voti che ha dato e soprattutto le note in cui spiega cosa l'ha colpita. "
    "Scegli titoli che condividono quelle qualita' (tono, temi, stile di regia o scrittura, ritmo, "
    "tipo di personaggi), non solo lo stesso genere. Rispondi sempre in italiano."
)


class AiError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_prompt(gusti: list[dict], visti: dict[str, dict], platforms: list[str], how_many: int = HOW_MANY) -> str:
    """Il profilo dei gusti spiegato a Claude."""
    first_year = date.today().year - YEARS
    lines = []
    for g in sorted(gusti, key=lambda g: (g.get("voto") is None, -(g.get("voto") or 0), not g.get("mi_piace"))):
        bits = ["mi piace" if g.get("mi_piace") else None,
                f"voto {g['voto']}/5" if g.get("voto") is not None else None]
        desc = f"- {g.get('title')}"
        if g.get("original_title") and g["original_title"] != g.get("title"):
            desc += f" ({g['original_title']})"
        desc += f" [{'film' if g.get('type') == 'movie' else 'serie'}, {g.get('year') or '?'}]"
        desc += ": " + ", ".join(b for b in bits if b) if any(bits) else ""
        if g.get("note"):
            desc += f'. Note: "{g["note"]}"'
        lines.append(desc)
    parts = [f"Ecco i miei giudizi (voto da 0 a 5 stelle):\n" + ("\n".join(lines) or "(nessuno, per ora)")]

    rated = [v for v in visti.values() if v.get("voto") is not None]
    rated.sort(key=lambda v: -v["voto"])
    if rated:
        parts.append("Altri titoli che ho visto, con il mio voto da 0 a 10 (dal mio tracker Yamtrack):\n" +
                     "\n".join(f"- {v['titolo']}: {v['voto']:g}/10" for v in rated[:120]))
    seen = [v["titolo"] for v in visti.values() if v.get("voto") is None and v.get("titolo")]
    if seen:
        parts.append("Titoli che ho gia' visto o sto guardando (senza voto):\n" + "; ".join(seen[:300]))

    parts.append(
        f"Suggeriscimi {how_many} film e serie TV che potrebbero piacermi davvero, scegliendo tra tutto cio' "
        f"che e' uscito dal {first_year} a oggi, non solo le novita': al massimo un terzo degli ultimi 3 anni, "
        "il resto distribuito nei decenni, con qualche perla meno conosciuta accanto ai classici. "
        "Mescola film e serie in proporzione ai miei gusti. Non suggerire nessuno dei titoli elencati sopra "
        "(ne' i loro sequel se ho dato un voto basso). "
        f"Se puoi, preferisci titoli disponibili in Italia su: {', '.join(platforms) or 'qualsiasi piattaforma'}. "
        "Per ogni titolo indica titolo italiano, titolo originale, anno esatto di uscita, tipo e il motivo "
        "legato ai miei gusti (cita le mie note quando servono). Ordinali dal piu' adatto a me."
    )
    return "\n\n".join(parts)


class Consigli:
    def __init__(self, data_dir: str, api_key: str = "", model: str = ""):
        self.file = os.path.join(data_dir, "suggeriti.json")
        self.api_key = api_key.strip()
        self.model = model.strip() or DEFAULT_MODEL
        self.running = False
        self.data: dict = {"titoli": [], "generato": None, "gusti": None, "fonte": None, "errore": None}
        self._last_try = 0.0
        try:
            with open(self.file, encoding="utf-8") as f:
                self.data |= json.load(f)
        except (OSError, ValueError):
            pass

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _client(self, key: str | None = None) -> anthropic.AsyncAnthropic:
        return anthropic.AsyncAnthropic(api_key=key or self.api_key, timeout=300, max_retries=2)

    # ------------------------------------------------------------- Claude

    @staticmethod
    def _error(e: Exception) -> AiError:
        if isinstance(e, anthropic.AuthenticationError):
            return AiError("Chiave API di Anthropic non valida.")
        if isinstance(e, anthropic.PermissionDeniedError):
            return AiError("La chiave API non ha accesso a questo modello.")
        if isinstance(e, anthropic.NotFoundError):
            return AiError("Modello non trovato: scegline un altro nel menu impostazioni.")
        if isinstance(e, anthropic.RateLimitError):
            return AiError("Troppe richieste ad Anthropic: riprova tra qualche minuto.")
        if isinstance(e, anthropic.APIStatusError):
            return AiError(f"Errore di Anthropic ({e.status_code}): {e.message}")
        if isinstance(e, anthropic.APIConnectionError):
            return AiError("Anthropic non raggiungibile: controlla la connessione del server.")
        return AiError(str(e))

    async def verify(self, key: str, model: str) -> None:
        """Controlla chiave e modello senza consumare token."""
        try:
            async with self._client(key) as client:
                await client.models.retrieve(model)
        except anthropic.AnthropicError as e:
            raise self._error(e) from e

    async def ask(self, prompt: str) -> list[dict]:
        """Chiede i suggerimenti a Claude; restituisce la lista grezza."""
        extra: dict = {}
        if self.model in FALLBACK_MODELS:
            # Se il modello rifiuta la richiesta, l'API la ripete su un altro modello
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        try:
            async with self._client() as client:
                response = await client.beta.messages.create(
                    model=self.model,
                    max_tokens=16000,
                    system=SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                    output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
                    **extra,
                )
        except anthropic.AnthropicError as e:
            raise self._error(e) from e
        if response.stop_reason == "refusal":
            raise AiError("Il modello non ha risposto a questa richiesta.")
        if response.stop_reason == "max_tokens":
            raise AiError("Risposta troppo lunga, interrotta: riprova.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)["suggerimenti"]
        except (ValueError, KeyError, TypeError) as e:
            raise AiError("Risposta del modello non leggibile.") from e

    # ------------------------------------------------------- generazione

    def needs_update(self, gusti_modified: str | None, has_tastes: bool) -> bool:
        if self.running or not has_tastes:
            return False
        if time.monotonic() - self._last_try < RETRY_SECONDS:
            return False
        if self.data.get("fonte") != ("ai" if self.configured else "tmdb"):
            return True
        return not self.data.get("titoli") or self.data.get("gusti") != gusti_modified

    async def generate(self, catalog, gusti, visti: dict[str, dict], platforms: list[str]) -> None:
        """Ricalcola i suggerimenti e li salva. Gli errori finiscono in data["errore"]."""
        if self.running:
            return
        self.running = True
        self._last_try = time.monotonic()
        modified = gusti.modified
        try:
            exclude = {g["id"] for g in gusti.items} | set(visti)
            if self.configured:
                prompt = build_prompt(gusti.items, visti, platforms)
                raw = await self.ask(prompt)
                items = await self._resolve(catalog, raw, exclude)
                source = "ai"
            else:
                items = await self._from_tmdb(catalog, gusti.items, exclude)
                source = "tmdb"
            await catalog._add_ratings(items)
            self.data = {"titoli": items, "generato": _now(), "gusti": modified, "fonte": source, "errore": None}
            log.info("Suggeriti per te: %d titoli (%s)", len(items), source)
        except Exception as e:  # noqa: BLE001 - l'errore viene mostrato nella pagina
            msg = str(e) if isinstance(e, AiError) else f"Suggerimenti non riusciti: {e}"
            self.data["errore"] = msg
            log.warning("%s", msg)
        finally:
            self.running = False
            self._save()

    async def _resolve(self, catalog, raw: list[dict], exclude: set[str]) -> list[dict]:
        """Cerca su TMDB ogni titolo suggerito da Claude."""
        async def one(s: dict) -> dict | None:
            kind = "movie" if s.get("tipo") == "film" else "tv"
            year = s.get("anno") if isinstance(s.get("anno"), int) else None
            tmdb_id = None
            for title in dict.fromkeys(t for t in (s.get("titolo_originale"), s.get("titolo")) if t):
                tmdb_id = await catalog.search(kind, title, year)
                if tmdb_id:
                    break
            if not tmdb_id or f"{kind}-{tmdb_id}" in exclude:
                return None
            item = await catalog.item_for(kind, tmdb_id)
            return item | {"motivo": s.get("motivo") or ""} if item else None

        found = await asyncio.gather(*(one(s) for s in raw if isinstance(s, dict)), return_exceptions=True)
        items, seen = [], set()
        for it in found:
            if isinstance(it, dict) and it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)
            elif isinstance(it, Exception):
                log.warning("Titolo suggerito non trovato su TMDB: %s", it)
        return items

    async def _from_tmdb(self, catalog, gusti: list[dict], exclude: set[str]) -> list[dict]:
        """Senza AI: i titoli consigliati da TMDB per quelli che ti sono piaciuti di piu'."""
        liked = [g for g in gusti if g.get("mi_piace") or (g.get("voto") or 0) >= 4]
        liked.sort(key=lambda g: -(g.get("voto") or 3))
        recs = await asyncio.gather(*(catalog.recommendations(g["type"], g["tmdb_id"]) for g in liked[:15]),
                                    return_exceptions=True)
        votes: Counter = Counter()
        source: dict[tuple, str] = {}
        for g, lst in zip(liked, recs):
            if isinstance(lst, Exception):
                continue
            for rank, key in enumerate(lst[:20]):
                votes[key] += (g.get("voto") or 3) * (20 - rank)
                source.setdefault(key, g.get("title") or "")
        best = [k for k, _ in votes.most_common() if f"{k[0]}-{k[1]}" not in exclude][:HOW_MANY]
        items = await asyncio.gather(*(catalog.item_for(k, i) for k, i in best), return_exceptions=True)
        return [it | {"motivo": f"Perché ti è piaciuto «{source[k]}»."}
                for k, it in zip(best, items) if isinstance(it, dict)]

    def find(self, item_id: str) -> dict | None:
        return next((it for it in self.data.get("titoli", []) if it["id"] == item_id), None)

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.file), exist_ok=True)
            tmp = self.file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.file)
        except OSError as e:
            log.warning("Impossibile salvare i suggerimenti: %s", e)
