#!/bin/sh
set -e

# Daten-Verzeichnis (gemountetes Volume) anlegen und dem App-Benutzer übereignen,
# danach Privilegien ablegen und den Server als nicht-root-Benutzer starten.
mkdir -p /app/data/uploads /app/data/branding
chown -R appuser:appuser /app/data 2>/dev/null || true

exec gosu appuser "$@"
