"""La sezione "Suggeriti per te".

I suggerimenti li sceglie un modello AI, partendo da mi piace, voti e note (e dai titoli gia'
visti su Yamtrack o Plex), pescando tra film e serie degli ultimi 50 anni. Il modello puo' essere
Claude (API di Anthropic) oppure uno qualsiasi raggiungibile con un'API compatibile con quella
di OpenAI (OpenRouter, OpenAI, un server locale...).
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
import httpx

log = logging.getLogger("consigli")

PROVIDERS = ("anthropic", "openai")
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_OPENAI_URL = "https://openrouter.ai/api/v1"
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
NOTE_WORDS = 20       # note suggerite dall'AI: brevissime
NOTE_HOW_MANY = 3

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


NOTE_SCHEMA = {
    "type": "object",
    "properties": {"note": {"type": "array", "items": {"type": "string", "description": "Max 20 parole"}}},
    "required": ["note"],
    "additionalProperties": False,
}

NOTE_SYSTEM = (
    "Aiuti una persona a scrivere le sue note personali su film e serie TV che ha visto, cosi' da ricordare "
    "cosa l'ha colpita e ricevere consigli migliori. Scrivi frasi brevissime, naturali, in prima persona, "
    "in italiano."
)


class AiError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _names(items: list[dict]) -> str:
    return "; ".join(f"{i.get('title')} ({i.get('year') or '?'})" for i in items if i.get("title"))


def build_prompt(gusti: list[dict], visti: dict[str, dict], platforms: list[str], how_many: int = HOW_MANY,
                 watchlist: list[dict] | None = None, ignorati: list[dict] | None = None) -> str:
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
        parts.append("Altri titoli che ho visto, con il mio voto da 0 a 10 (da Yamtrack o Plex):\n" +
                     "\n".join(f"- {v['titolo']}: {v['voto']:g}/10" for v in rated[:120]))
    seen = [v["titolo"] for v in visti.values() if v.get("voto") is None and v.get("titolo")]
    if seen:
        parts.append("Titoli che ho gia' visto o sto guardando (senza voto):\n" + "; ".join(seen[:300]))

    if watchlist:
        parts.append("Nella mia watchlist, cose che voglio vedere (indicano i miei interessi, non suggerirle): "
                     + _names(watchlist[:150]))
    if ignorati:
        parts.append("Titoli che ho scartato perche' non mi interessano (non suggerirli, ne' titoli troppo simili): "
                     + _names(ignorati[:150]))

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


def _json_from_text(text: str, key: str = "suggerimenti") -> list:
    """La lista `key` da una risposta JSON (anche dentro un blocco ```json)."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise AiError("Risposta del modello non leggibile.")
    try:
        items = json.loads(text[start:end + 1])[key]
    except (ValueError, KeyError, TypeError) as e:
        raise AiError("Risposta del modello non leggibile.") from e
    if not isinstance(items, list):
        raise AiError("Risposta del modello non leggibile.")
    return items


def short(text: str, words: int = NOTE_WORDS) -> str:
    """Taglia un testo a un massimo di parole."""
    parts = str(text or "").split()
    if len(parts) <= words:
        return " ".join(parts)
    return " ".join(parts[:words]).rstrip(",;:.") + "…"


def build_note_prompt(item: dict, gusto: dict | None) -> str:
    """La richiesta di qualche nota breve sul perche' un titolo e' piaciuto (o no)."""
    g = gusto or {}
    kind = "film" if item.get("type") == "movie" else "serie TV"
    title = item.get("title") or ""
    if item.get("original_title") and item["original_title"] != title:
        title += f" ({item['original_title']})"
    lines = [f"Titolo: {title}", f"Tipo: {kind}, {item.get('year') or '?'}"]
    if item.get("genres"):
        lines.append("Generi: " + ", ".join(item["genres"]))
    if item.get("overview"):
        lines.append("Trama: " + item["overview"][:700])
    voto = g.get("voto")
    if voto is not None:
        lines.append(f"Il mio voto: {voto}/5")
    if g.get("note"):
        lines.append(f'Cosa ho gia\' scritto: "{g["note"][:400]}"')
    low = voto is not None and voto <= 2 and not g.get("mi_piace")
    what = "cosa potrebbe non avermi convinto" if low else "cosa potrebbe essermi piaciuto"
    lines.append(
        f"\nProponimi {NOTE_HOW_MANY} note alternative, in prima persona, su {what} di questo titolo. "
        f"Ognuna al massimo {NOTE_WORDS} parole, concreta e diversa dalle altre (regia, interpretazioni, "
        "atmosfera, scrittura, ritmo, colonna sonora, finale...). Niente spoiler, niente riassunto della trama."
    )
    return "\n".join(lines)


class Consigli:
    def __init__(self, data_dir: str, api_key: str = "", model: str = "", provider: str = "anthropic",
                 base_url: str = ""):
        self.file = os.path.join(data_dir, "suggeriti.json")
        self.provider = provider if provider in PROVIDERS else "anthropic"
        self.api_key = api_key.strip()
        self.base_url = (base_url.strip() or DEFAULT_OPENAI_URL).rstrip("/")
        self.model = model.strip() or (DEFAULT_MODEL if self.provider == "anthropic" else "")
        self.running = False
        self.data: dict = {"titoli": [], "generato": None, "gusti": None, "fonte": None, "errore": None}
        self._last_try = float("-inf")
        try:
            with open(self.file, encoding="utf-8") as f:
                self.data |= json.load(f)
        except (OSError, ValueError):
            pass

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)

    @property
    def label(self) -> str:
        """Chi sceglie i suggerimenti, per la pagina."""
        return "Claude" if self.provider == "anthropic" else (self.model or "AI")

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

    async def verify(self, key: str, model: str, provider: str | None = None, base_url: str | None = None) -> None:
        """Controlla chiave e modello senza consumare token."""
        if (provider or self.provider) == "openai":
            await self._verify_openai(key, model, (base_url or self.base_url).rstrip("/"))
            return
        try:
            async with self._client(key) as client:
                await client.models.retrieve(model)
        except anthropic.AnthropicError as e:
            raise self._error(e) from e

    async def ask(self, prompt: str) -> list[dict]:
        """Chiede i suggerimenti al modello scelto; restituisce la lista grezza."""
        example = ('{"suggerimenti": [{"titolo": "...", "titolo_originale": "...", "anno": 1999, '
                   '"tipo": "film" o "serie", "motivo": "..."}]}')
        return await self._complete(SYSTEM, prompt, SCHEMA, "suggerimenti", example, max_tokens=16000, effort="medium")

    async def note(self, item: dict, gusto: dict | None) -> list[str]:
        """Qualche nota brevissima (max 20 parole) sul perche' un titolo e' piaciuto: una sola richiesta."""
        raw = await self._complete(NOTE_SYSTEM, build_note_prompt(item, gusto), NOTE_SCHEMA, "note",
                                   '{"note": ["...", "...", "..."]}', max_tokens=1500, effort="low")
        out = [short(n) for n in raw if isinstance(n, str) and n.strip()]
        out = list(dict.fromkeys(n for n in out if n))[:NOTE_HOW_MANY]
        if not out:
            raise AiError("Il modello non ha proposto nessuna nota: riprova.")
        return out

    async def _complete(self, system: str, prompt: str, schema: dict, key: str, example: str,
                        max_tokens: int, effort: str) -> list:
        if self.provider == "openai":
            return await self._ask_openai(system, prompt, schema, key, example)
        return await self._ask_claude(system, prompt, schema, key, max_tokens, effort)

    async def _ask_claude(self, system: str, prompt: str, schema: dict, key: str, max_tokens: int,
                          effort: str) -> list:
        extra: dict = {}
        if self.model in FALLBACK_MODELS:
            # Se il modello rifiuta la richiesta, l'API la ripete su un altro modello
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        try:
            async with self._client() as client:
                response = await client.beta.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                    output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                    **extra,
                )
        except anthropic.AnthropicError as e:
            raise self._error(e) from e
        if response.stop_reason == "refusal":
            raise AiError("Il modello non ha risposto a questa richiesta.")
        if response.stop_reason == "max_tokens":
            raise AiError("Risposta troppo lunga, interrotta: riprova.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return _json_from_text(text, key)

    # ------------------------------------------- API compatibili con OpenAI

    def _http(self, key: str, base_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(300, connect=15), headers={
            "Authorization": f"Bearer {key}",
            # OpenRouter mostra il nome dell'app nelle statistiche di utilizzo
            "HTTP-Referer": "https://github.com/land2595/CosaGuardo", "X-Title": "CosaGuardo",
        })

    @staticmethod
    def _http_error(r: httpx.Response) -> AiError:
        try:
            body = r.json()
            detail = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
        except ValueError:
            detail = None
        detail = str(detail or r.text[:200] or r.status_code)
        if r.status_code == 401:
            return AiError("Chiave API non valida.")
        if r.status_code == 402:
            return AiError("Credito esaurito sul servizio AI: ricaricalo e riprova.")
        if r.status_code == 404:
            return AiError(f"Modello o indirizzo non trovato: {detail}")
        if r.status_code == 429:
            return AiError("Troppe richieste al servizio AI: riprova tra qualche minuto.")
        return AiError(f"Errore del servizio AI ({r.status_code}): {detail}")

    async def list_models(self, key: str | None = None, base_url: str | None = None) -> list[str]:
        """I modelli offerti dal servizio (GET /models)."""
        try:
            async with self._http(key or self.api_key, (base_url or self.base_url).rstrip("/")) as client:
                r = await client.get("/models")
        except httpx.HTTPError as e:
            raise AiError(f"Servizio AI non raggiungibile: {e}") from e
        if r.status_code != 200:
            raise self._http_error(r)
        try:
            return sorted(m["id"] for m in r.json().get("data", []) if m.get("id"))
        except (ValueError, TypeError, KeyError) as e:
            raise AiError("Elenco dei modelli non leggibile: controlla l'indirizzo.") from e

    async def _verify_openai(self, key: str, model: str, base_url: str) -> None:
        if not model:
            raise AiError("Scegli il modello da usare.")
        if "openrouter.ai" in base_url:
            # Su OpenRouter l'elenco dei modelli e' pubblico: la chiave si controlla a parte
            try:
                async with self._http(key, base_url) as client:
                    r = await client.get("/key")
            except httpx.HTTPError as e:
                raise AiError(f"OpenRouter non raggiungibile: {e}") from e
            if r.status_code != 200:
                raise self._http_error(r)
        models = await self.list_models(key, base_url)
        if models and model not in models:
            raise AiError(f"Il modello «{model}» non c'e' tra quelli offerti da {base_url}.")

    async def _ask_openai(self, system: str, prompt: str, schema: dict, key: str, example: str) -> list:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        structured = {"type": "json_schema", "json_schema": {"name": key, "strict": True, "schema": schema}}
        try:
            async with self._http(self.api_key, self.base_url) as client:
                r = await client.post("/chat/completions", json={
                    "model": self.model, "messages": messages, "response_format": structured})
                unsupported = any(w in r.text.lower() for w in ("response_format", "json_schema", "structured"))
                if r.status_code in (400, 422) and unsupported:
                    # Modello senza risposte con schema: si chiede il JSON nel testo
                    plain = messages[:1] + [{"role": "user", "content": prompt + (
                        "\n\nRispondi solo con un oggetto JSON, senza altro testo, in questa forma: " + example)}]
                    r = await client.post("/chat/completions", json={"model": self.model, "messages": plain})
        except httpx.HTTPError as e:
            raise AiError(f"Servizio AI non raggiungibile: {e}") from e
        if r.status_code != 200:
            raise self._http_error(r)
        try:
            choice = r.json()["choices"][0]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise AiError("Risposta del servizio AI non leggibile.") from e
        if choice.get("finish_reason") == "length":
            raise AiError("Risposta troppo lunga, interrotta: riprova.")
        content = (choice.get("message") or {}).get("content")
        if isinstance(content, list):  # alcuni servizi restituiscono blocchi di testo
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        if not content:
            raise AiError("Il modello non ha risposto a questa richiesta.")
        return _json_from_text(content, key)

    # ------------------------------------------------------- generazione

    def stale(self, gusti_modified: str | None) -> bool:
        """I suggerimenti non rispecchiano piu' i gusti, il servizio o il modello attuali."""
        if not self.data.get("generato"):
            return False
        if self.data.get("fonte") != ("ai" if self.configured else "tmdb"):
            return True
        if self.configured and self.data.get("modello") != self.label:
            return True  # cambiato modello o servizio
        return self.data.get("gusti") != gusti_modified

    def needs_update(self, gusti_modified: str | None, has_tastes: bool) -> bool:
        """Se ricalcolare da soli i suggerimenti aprendo la sezione.

        Solo senza AI (i consigliati di TMDB sono gratis): con l'AI ogni aggiornamento e' una
        richiesta a pagamento, quindi parte solo dal pulsante (POST /api/suggeriti/aggiorna).
        """
        if self.configured or self.running or not has_tastes:
            return False
        if time.monotonic() - self._last_try < RETRY_SECONDS:
            return False
        return not self.data.get("titoli") or self.stale(gusti_modified)

    async def generate(self, catalog, gusti, visti: dict[str, dict], platforms: list[str], liste=None) -> None:
        """Ricalcola i suggerimenti e li salva. Gli errori finiscono in data["errore"]."""
        if self.running:
            return
        self.running = True
        self._last_try = time.monotonic()
        modified = gusti.modified
        try:
            watchlist = liste.items("watchlist") if liste else []
            ignorati = liste.items("ignorati") if liste else []
            exclude = {g["id"] for g in gusti.items} | set(visti) | {i["id"] for i in watchlist + ignorati}
            if self.configured:
                prompt = build_prompt(gusti.items, visti, platforms, watchlist=watchlist, ignorati=ignorati)
                raw = await self.ask(prompt)
                items = await self._resolve(catalog, raw, exclude)
                source = "ai"
            else:
                items = await self._from_tmdb(catalog, gusti.items, exclude)
                source = "tmdb"
            await catalog._add_ratings(items)
            self.data = {"titoli": items, "generato": _now(), "gusti": modified, "fonte": source, "errore": None,
                         "modello": self.label if source == "ai" else None}
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
