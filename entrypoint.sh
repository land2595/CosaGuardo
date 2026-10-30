#!/bin/sh
# Avvia CosaGuardo come utente senza privilegi (PUID/PGID, predefiniti 1000),
# dopo aver sistemato i permessi della cartella dati. Cosi' funziona sia con un
# volume Docker sia con una cartella del computer/NAS montata in /data.
set -e

DATA_DIR="${DATA_DIR:-/data}"
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR"
  if chown -R "$PUID:$PGID" "$DATA_DIR" 2>/dev/null; then
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
  fi
  echo "Attenzione: impossibile cambiare i permessi di $DATA_DIR, avvio come root." >&2
fi
exec "$@"
