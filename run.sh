#!/usr/bin/env bash
set -eu

cd "$(dirname "$0")"

# Load .env if present
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8008}"
RELOAD="${RELOAD:-false}"

RELOAD_FLAG=""
if [ "$RELOAD" = "true" ]; then
  RELOAD_FLAG="--reload"
fi

exec uvicorn app.main:app --host "$HOST" --port "$PORT" $RELOAD_FLAG
