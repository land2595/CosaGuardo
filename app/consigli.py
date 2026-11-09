"""La sezione "Suggeriti per te".

I suggerimenti li sceglie un modello AI, partendo da mi piace, voti e note (e dai titoli gia'
visti su Yamtrack o Plex), pescando tra film e serie degli ultimi 50 anni. Il modello tiene una
memoria dei gusti (app/profilo.py) che riscrive a ogni aggiornamento e ricorda i titoli gia'
suggeriti, per non riproporli e per imparare da come sono stati accolti. Il modello puo' essere
Claude (API di Anthropic) oppure uno qualsiasi raggiungibile con un'API compatibile con quella
di OpenAI (OpenRouter, OpenAI, un server locale...).
Ogni titolo suggerito viene poi cercato su TMDB per avere locandina, trama e piattaforme italiane.
Senza chiave si usano i "consigliati" di TMDB dei titoli che ti sono piaciuti.
"""

import asyncio
import json
import logging
import os
import re
import time
import unicodedata
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
HOW_MANY_FILMS = 100     # suggerimenti per ogni aggiornamento
HOW_MANY_SERIES = 100
HOW_MANY = HOW_MANY_FILMS + HOW_MANY_SERIES
MAX_TOKENS = 64000       # 200 titoli con il motivo: risposta lunga, quindi in streaming
YEARS = 50
RETRY_SECONDS = 120   # dopo un errore non si riprova da soli prima di 2 minuti
NOTE_WORDS = 20       # note suggerite dall'AI: brevissime
NOTE_HOW_MANY = 3

SCHEMA = {
    "type": "object",
    "properties": {
        # Prima la memoria: se la risposta venisse interrotta, si perdono solo gli ultimi titoli
        "profilo": {"type": "string", "description": "La memoria dei gusti aggiornata, in punti brevi"},
        "suggerimenti": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titolo": {"type": "string", "description": "Titolo italiano, se esiste"},
                    "titolo_originale": {"type": "string"},
                    "anno": {"type": "integer", "description": "Anno di uscita (per le serie, del debutto)"},
                    "tipo": {"type": "string", "enum": ["film", "serie"]},
                    "motivo": {"type": "string", "description": "Perche' piacera', in italiano, una frase breve"},
                },
                "required": ["titolo", "titolo_originale", "anno", "tipo", "motivo"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["profilo", "suggerimenti"],
    "additionalProperties": False,
}

SYSTEM = (
    "Sei un critico cinematografico e televisivo con una conoscenza enciclopedica di film e serie TV "
    "di tutto il mondo. Consigli titoli a una persona che vive in Italia partendo dai suoi gusti reali: "
    "cosa le e' piaciuto, i voti che ha dato e soprattutto le note in cui spiega cosa l'ha colpita. "
    "Scegli titoli che condividono quelle qualita' (tono, temi, stile di regia o scrittura, ritmo, "
    "tipo di personaggi), non solo lo stesso genere. Tieni una memoria dei suoi gusti che affini ogni "
    "volta, imparando da come reagisce ai titoli che le hai gia' consigliato. Rispondi sempre in italiano."
)
PROFILE_SECTIONS = ("Ama", "Non ama", "Autori e interpreti", "Toni e temi", "Da esplorare")
PROFILE_CHARS = 2000


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


def reactions(storico: list[dict], gusti: list[dict], visti: dict[str, dict],
              watchlist: list[dict], ignorati: list[dict]) -> list[str]:
    """Come sono stati accolti i titoli suggeriti in passato: il segnale piu' utile per imparare."""
    past = {h["id"] for h in storico}
    out = []
    for g in gusti:
        if g["id"] not in past:
            continue
        bits = ["mi piace" if g.get("mi_piace") else None,
                f"voto {g['voto']}/5" if g.get("voto") is not None else None,
                f'note: "{g["note"]}"' if g.get("note") else None]
        out.append(f"- {g.get('title')} ({g.get('year') or '?'}): " + ", ".join(b for b in bits if b))
    for key, v in visti.items():
        if key in past and key not in {g["id"] for g in gusti}:
            out.append(f"- {v.get('titolo')}: visto" + (f", voto {v['voto']:g}/10" if v.get("voto") is not None else ""))
    out += [f"- {i.get('title')} ({i.get('year') or '?'}): messo in watchlist" for i in watchlist if i["id"] in past]
    out += [f"- {i.get('title')} ({i.get('year') or '?'}): scartato, non mi interessa" for i in ignorati if i["id"] in past]
    return out


def build_prompt(gusti: list[dict], visti: dict[str, dict], platforms: list[str],
                 films: int = HOW_MANY_FILMS, series: int = HOW_MANY_SERIES,
                 watchlist: list[dict] | None = None, ignorati: list[dict] | None = None,
                 profilo: str = "", storico: list[dict] | None = None) -> str:
    """Il profilo dei gusti spiegato al modello."""
    first_year = date.today().year - YEARS
    watchlist, ignorati, storico = watchlist or [], ignorati or [], storico or []
    parts = [("La tua memoria dei miei gusti (l'hai scritta tu le volte scorse; se l'ho corretta io, "
              "le mie correzioni valgono piu' di tutto):\n" + profilo) if profilo.strip()
             else "La memoria dei miei gusti e' ancora vuota: questa e' la prima volta."]
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
    parts.append("Ecco i miei giudizi (voto da 0 a 5 stelle):\n" + ("\n".join(lines) or "(nessuno, per ora)"))

    # Yamtrack tiene anche i titoli "da vedere" (Planning) e quelli abbandonati: non sono visti
    watched = [v for v in visti.values() if v.get("titolo") and v.get("stato") in SEEN_STATES]
    rated = sorted((v for v in watched if v.get("voto") is not None), key=lambda v: -v["voto"])
    if rated:
        parts.append("Altri titoli che ho visto, con il mio voto da 0 a 10 (da Yamtrack o Plex):\n" +
                     "\n".join(f"- {v['titolo']}: {v['voto']:g}/10" for v in rated[:120]))
    seen = [v["titolo"] for v in watched if v.get("voto") is None]
    if seen:
        parts.append("Titoli che ho gia' visto o sto guardando, senza voto (non suggerirli): " + "; ".join(seen[:300]))
    planned = [v["titolo"] for v in visti.values() if v.get("titolo") and v.get("stato") == "Planning"]
    if planned:
        parts.append("Titoli che ho in lista per vederli, ma che NON ho visto (non suggerirli): " + "; ".join(planned[:200]))
    dropped = [v["titolo"] for v in visti.values() if v.get("titolo") and v.get("stato") == "Dropped"]
    if dropped:
        parts.append("Titoli che ho iniziato e abbandonato (non suggerirli): " + "; ".join(dropped[:150]))

    if watchlist:
        parts.append("Nella mia watchlist, cose che voglio vedere (indicano i miei interessi, non suggerirle): "
                     + _names(watchlist[:150]))
    if ignorati:
        parts.append("Titoli che ho scartato perche' non mi interessano (non suggerirli, ne' titoli troppo simili): "
                     + _names(ignorati[:150]))
    react = reactions(storico, gusti, visti, watchlist, ignorati)
    if react:
        parts.append("Come ho accolto i titoli che mi hai suggerito le volte scorse (impara da qui cosa funziona "
                     "e cosa no):\n" + "\n".join(react[:150]))
    if storico:
        parts.append("Titoli che mi hai gia' suggerito (non riproporli, cerca altro): " + _names(storico[:400]))

    parts.append(
        f"Suggeriscimi {films} film e {series} serie TV (esattamente, tutti diversi) che potrebbero piacermi "
        f"davvero, scegliendo tra tutto cio' che e' uscito dal {first_year} a oggi, non solo le novita': al massimo "
        "un terzo degli ultimi 3 anni, il resto distribuito nei decenni, con molte perle meno conosciute e "
        "titoli di tutto il mondo accanto ai classici. Prima tutti i film, poi tutte le serie. Non suggerire "
        "nessuno dei titoli elencati sopra (ne' i loro sequel se ho dato un voto basso). "
        f"Se puoi, preferisci titoli disponibili in Italia su: {', '.join(platforms) or 'qualsiasi piattaforma'}. "
        "Per ogni titolo indica titolo italiano, titolo originale, anno esatto di uscita, tipo e il motivo "
        "in una frase breve legata ai miei gusti (cita le mie note quando servono). Ordina film e serie dal "
        "piu' adatto a me."
    )
    parts.append(
        "Nel campo «profilo», che scrivi per primo, riscrivi la memoria dei miei gusti, che userai la prossima "
        "volta. Regole:\n"
        "- Basala SOLO sui miei giudizi elencati sopra (mi piace, stelle e note) e sulle mie correzioni alla "
        "memoria. Non dedurre gusti da titoli visti senza giudizio, watchlist, liste, scartati o suggerimenti.\n"
        "- Come esempi puoi citare solo titoli che ho giudicato; mai altri titoli, nemmeno famosi. Non inventare: "
        "se i giudizi dicono poco, scrivi poco.\n"
        f"- Esattamente {len(PROFILE_SECTIONS)} righe, una per voce e ogni voce una sola volta, nell'ordine: "
        + ", ".join(f"«{k}: ...»" for k in PROFILE_SECTIONS) + ". Nella stessa riga separa i punti con «;». "
        "Se per una voce non hai elementi, scrivi solo un trattino.\n"
        "- «Autori e interpreti»: solo registi, autori e attori dei titoli che ho giudicato bene. «Da esplorare»: "
        "generi o direzioni nuove, senza nominare titoli.\n"
        f"- Al massimo {PROFILE_CHARS} caratteri in tutto, frasi asciutte e concrete."
    )
    return "\n\n".join(parts)


SEEN_STATES = ("Completed", "In progress", "Paused")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def clean_profile(text: str, gusti: list[dict]) -> str:
    """Ripulisce la memoria scritta dal modello.

    - una riga per voce, nell'ordine previsto (le voci ripetute vengono unite);
    - tra parentesi restano solo i titoli che hai giudicato: gli esempi inventati spariscono.
    """
    known = [n for g in gusti for n in (_norm(g.get("title")), _norm(g.get("original_title"))) if len(n) >= 2]

    def judged(name: str) -> bool:
        n = _norm(name)
        return len(n) >= 2 and any(n == k or (len(n) >= 4 and (n in k or k in n)) for k in known)

    def fix_parens(value: str) -> str:
        def keep(m: re.Match) -> str:
            names = [x.strip() for x in re.split(r"[,;/]| e ", m.group(1)) if x.strip()]
            ok = [x for x in names if judged(x)]
            return f" ({', '.join(ok)})" if ok else ""
        return re.sub(r"\s*\(([^()]*)\)", keep, value).strip()

    sections: dict[str, list[str]] = {k: [] for k in PROFILE_SECTIONS}
    by_norm = {_norm(k): k for k in PROFILE_SECTIONS}
    current = None
    for line in str(text or "").splitlines():
        line = line.strip().lstrip("-•*").strip()
        if not line:
            continue
        m = re.match(r"^\**([^:*]{2,32})\**\s*:\s*(.*)$", line)
        key = by_norm.get(_norm(m.group(1))) if m else None
        if key:
            current, value = key, m.group(2)
        elif m:
            current = None  # voce sconosciuta: si scarta
            continue
        elif current:
            value = line
        else:
            continue
        if key != "Autori e interpreti":
            value = fix_parens(value)
        value = value.strip(" ;.")
        if value and value != "-" and value not in sections[current]:
            sections[current].append(value)
    return "\n".join(f"{k}: {'; '.join(v)}" for k, v in sections.items() if v)


def _salvage(text: str, key: str) -> dict | None:
    """Da una risposta JSON interrotta a meta' recupera i titoli completi (e la memoria, se c'e')."""
    m = re.search(r'"%s"\s*:\s*\[' % re.escape(key), text or "")
    if not m:
        return None
    dec, pos, items = json.JSONDecoder(), m.end(), []
    while True:
        while pos < len(text) and text[pos] in " \t\r\n,":
            pos += 1
        try:
            obj, pos = dec.raw_decode(text, pos)
        except ValueError:
            break
        if isinstance(obj, dict):
            items.append(obj)
    if not items:
        return None
    out: dict = {key: items}
    p = re.search(r'"profilo"\s*:\s*', text)
    if p:
        try:
            out["profilo"], _ = dec.raw_decode(text, p.end())
        except ValueError:
            pass
    return out


def _json_from_text(text: str, key: str = "suggerimenti", partial: bool = False) -> dict:
    """L'oggetto JSON della risposta (anche dentro un blocco ```json), con la lista `key`."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    if partial:
        # Risposta interrotta per lunghezza: si tiene quello che e' arrivato intero
        found = _salvage(text, key)
        if found:
            return found
        raise AiError("Risposta troppo lunga, interrotta: riprova.")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise AiError("Risposta del modello non leggibile.")
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError as e:
        raise AiError("Risposta del modello non leggibile.") from e
    if not isinstance(obj, dict) or not isinstance(obj.get(key), list):
        raise AiError("Risposta del modello non leggibile.")
    return obj


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
        return anthropic.AsyncAnthropic(api_key=key or self.api_key, timeout=1200, max_retries=2)

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

    async def ask(self, prompt: str) -> dict:
        """Chiede i suggerimenti al modello scelto: {"suggerimenti": [...], "profilo": "..."}."""
        example = ('{"suggerimenti": [{"titolo": "...", "titolo_originale": "...", "anno": 1999, '
                   '"tipo": "film" o "serie", "motivo": "..."}], "profilo": "Ama: ...\\nNon ama: ..."}')
        return await self._complete(SYSTEM, prompt, SCHEMA, "suggerimenti", example, max_tokens=MAX_TOKENS,
                                    effort="medium")

    async def note(self, item: dict, gusto: dict | None) -> list[str]:
        """Qualche nota brevissima (max 20 parole) sul perche' un titolo e' piaciuto: una sola richiesta."""
        raw = (await self._complete(NOTE_SYSTEM, build_note_prompt(item, gusto), NOTE_SCHEMA, "note",
                                    '{"note": ["...", "...", "..."]}', max_tokens=1500, effort="low"))["note"]
        out = [short(n) for n in raw if isinstance(n, str) and n.strip()]
        out = list(dict.fromkeys(n for n in out if n))[:NOTE_HOW_MANY]
        if not out:
            raise AiError("Il modello non ha proposto nessuna nota: riprova.")
        return out

    async def _complete(self, system: str, prompt: str, schema: dict, key: str, example: str,
                        max_tokens: int, effort: str) -> dict:
        if self.provider == "openai":
            return await self._ask_openai(system, prompt, schema, key, example)
        return await self._ask_claude(system, prompt, schema, key, max_tokens, effort)

    async def _ask_claude(self, system: str, prompt: str, schema: dict, key: str, max_tokens: int,
                          effort: str) -> dict:
        extra: dict = {}
        if self.model in FALLBACK_MODELS:
            # Se il modello rifiuta la richiesta, l'API la ripete su un altro modello
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        params = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            **extra,
        )
        try:
            async with self._client() as client:
                if max_tokens > 16000:
                    # Risposte lunghe (200 titoli): in streaming, cosi' non scadono i tempi della richiesta
                    async with client.beta.messages.stream(**params) as stream:
                        response = await stream.get_final_message()
                else:
                    response = await client.beta.messages.create(**params)
        except anthropic.AnthropicError as e:
            raise self._error(e) from e
        if response.stop_reason == "refusal":
            raise AiError("Il modello non ha risposto a questa richiesta.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return _json_from_text(text, key, partial=response.stop_reason == "max_tokens")

    # ------------------------------------------- API compatibili con OpenAI

    def _http(self, key: str, base_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(1200, connect=15), headers={
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

    async def _ask_openai(self, system: str, prompt: str, schema: dict, key: str, example: str) -> dict:
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
        content = (choice.get("message") or {}).get("content")
        if isinstance(content, list):  # alcuni servizi restituiscono blocchi di testo
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        if not content:
            raise AiError("Il modello non ha risposto a questa richiesta.")
        return _json_from_text(content, key, partial=choice.get("finish_reason") == "length")

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

    async def generate(self, catalog, gusti, visti: dict[str, dict], platforms: list[str], liste=None,
                       profilo=None) -> None:
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
                prompt = build_prompt(gusti.items, visti, platforms, watchlist=watchlist, ignorati=ignorati,
                                      profilo=profilo.text if profilo else "",
                                      storico=profilo.history if profilo else [])
                answer = await self.ask(prompt)
                raw = answer if isinstance(answer, list) else answer.get("suggerimenti") or []
                memory = "" if isinstance(answer, list) else str(answer.get("profilo") or "").strip()
                # I titoli gia' suggeriti in passato non tornano: si va sempre oltre
                items = await self._resolve(catalog, raw, exclude | (profilo.history_ids if profilo else set()))
                if profilo:
                    if memory:
                        profilo.set_text(clean_profile(memory, gusti.items), "ai")
                    profilo.add_history(items)
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
        recs = await asyncio.gather(*(catalog.recommendations(g["type"], g["tmdb_id"]) for g in liked[:40]),
                                    return_exceptions=True)
        votes: Counter = Counter()
        source: dict[tuple, str] = {}
        for g, lst in zip(liked, recs):
            if isinstance(lst, Exception):
                continue
            for rank, key in enumerate(lst[:20]):
                votes[key] += (g.get("voto") or 3) * (20 - rank)
                source.setdefault(key, g.get("title") or "")
        ranked = [k for k, _ in votes.most_common() if f"{k[0]}-{k[1]}" not in exclude]
        best = ([k for k in ranked if k[0] == "movie"][:HOW_MANY_FILMS] +
                [k for k in ranked if k[0] == "tv"][:HOW_MANY_SERIES])
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
