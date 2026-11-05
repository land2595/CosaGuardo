"""Menu admin protetto da PIN e impostazioni modificabili dalla pagina.

Le impostazioni sono salvate in /data/impostazioni.json e hanno la precedenza
sulle variabili d'ambiente. Il PIN viene salvato solo come hash.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time

log = logging.getLogger("admin")

PIN_RE = re.compile(r"^\d{4,8}$")
SESSION_SECONDS = 12 * 3600
MAX_ATTEMPTS = 5          # tentativi sbagliati prima del blocco
LOCK_SECONDS = 5 * 60     # durata del blocco

SERVICES = ("sonarr", "radarr")
EMPTY_SERVICE = {"tag": [], "profilo_qualita": "", "cartella": ""}


def _hash(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000).hex()


class PinError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Admin:
    def __init__(self, data_dir: str, env_pin: str = ""):
        self.file = os.path.join(data_dir, "impostazioni.json")
        self.env_pin = env_pin.strip()
        self.data: dict = {}
        self._sessions: dict[str, float] = {}
        self._failures = 0
        self._locked_until = 0.0
        try:
            with open(self.file, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            pass
        if self.env_pin and not PIN_RE.match(self.env_pin):
            log.warning("ADMIN_PIN deve avere da 4 a 8 cifre: ignorato.")
            self.env_pin = ""

    # ------------------------------------------------------------ PIN

    @property
    def pin_set(self) -> bool:
        return bool(self.env_pin or self.data.get("pin_hash"))

    @property
    def pin_from_env(self) -> bool:
        return bool(self.env_pin)

    def _check_pin(self, pin: str) -> bool:
        if self.env_pin:
            return hmac.compare_digest(pin, self.env_pin)
        stored, salt = self.data.get("pin_hash"), self.data.get("pin_salt")
        return bool(stored and salt) and hmac.compare_digest(_hash(pin, salt), stored)

    def _store_pin(self, pin: str) -> None:
        salt = secrets.token_hex(16)
        self.data["pin_salt"] = salt
        self.data["pin_hash"] = _hash(pin, salt)
        self._save()

    def _new_session(self) -> str:
        now = time.time()
        self._sessions = {t: exp for t, exp in self._sessions.items() if exp > now}
        token = secrets.token_urlsafe(32)
        self._sessions[token] = now + SESSION_SECONDS
        return token

    def create_pin(self, pin: str) -> str:
        """Primo avvio: imposta il PIN e apre subito una sessione."""
        if self.pin_set:
            raise PinError("Il PIN e' gia' impostato.", 403)
        if not PIN_RE.match(pin):
            raise PinError("Il PIN deve avere da 4 a 8 cifre.")
        self._store_pin(pin)
        log.info("PIN del menu admin impostato.")
        return self._new_session()

    def login(self, pin: str) -> str:
        now = time.time()
        if now < self._locked_until:
            minutes = int((self._locked_until - now) // 60) + 1
            raise PinError(f"Troppi tentativi sbagliati. Riprova tra {minutes} minut{'o' if minutes == 1 else 'i'}.", 429)
        if not self.pin_set:
            raise PinError("Il PIN non e' ancora impostato.", 409)
        if not self._check_pin(pin):
            self._failures += 1
            if self._failures >= MAX_ATTEMPTS:
                self._failures = 0
                self._locked_until = now + LOCK_SECONDS
                log.warning("Menu admin bloccato per %s minuti dopo troppi PIN sbagliati.", LOCK_SECONDS // 60)
                raise PinError("PIN sbagliato. Troppi tentativi: riprova tra 5 minuti.", 429)
            left = MAX_ATTEMPTS - self._failures
            raise PinError(f"PIN sbagliato. Ancora {left} tentativ{'o' if left == 1 else 'i'}.", 401)
        self._failures = 0
        return self._new_session()

    def change_pin(self, current: str, new: str) -> None:
        if self.env_pin:
            raise PinError("Il PIN e' impostato con ADMIN_PIN: cambialo nella configurazione del container.", 409)
        if not self._check_pin(current):
            raise PinError("Il PIN attuale non e' corretto.", 403)
        if not PIN_RE.match(new):
            raise PinError("Il nuovo PIN deve avere da 4 a 8 cifre.")
        self._store_pin(new)
        log.info("PIN del menu admin cambiato.")

    def valid(self, token: str | None) -> bool:
        exp = self._sessions.get(token or "")
        return bool(exp and exp > time.time())

    def logout(self, token: str | None) -> None:
        self._sessions.pop(token or "", None)

    # ----------------------------------------------------- impostazioni

    def service(self, name: str) -> dict:
        return EMPTY_SERVICE | (self.data.get(name) or {})

    def update_service(self, name: str, values: dict) -> dict:
        current = self.service(name)
        if "tag" in values:
            current["tag"] = sorted({int(t) for t in values["tag"]})
        for k in ("profilo_qualita", "cartella"):
            if k in values:
                current[k] = str(values[k] or "").strip()
        self.data[name] = current
        self._save()
        return current

    def set_value(self, key: str, value) -> None:
        self.data[key] = value
        self._save()

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.file), exist_ok=True)
            tmp = self.file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.file)
        except OSError as e:
            log.warning("Impossibile salvare le impostazioni: %s", e)
