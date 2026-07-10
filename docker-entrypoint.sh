#!/bin/sh
set -e

export FLASK_APP="${FLASK_APP:-app.py}"

if [ "$1" = "gunicorn" ]; then
  if [ "$#" -eq 1 ]; then
    set -- gunicorn app:app \
      --bind 0.0.0.0:8000 \
      --workers "${WEB_CONCURRENCY:-2}" \
      --threads "${GUNICORN_THREADS:-8}" \
      --timeout "${GUNICORN_TIMEOUT:-360}"
  fi

  if [ -n "${SSL_CERTFILE:-}" ] && [ -n "${SSL_KEYFILE:-}" ] && [ -f "$SSL_CERTFILE" ] && [ -f "$SSL_KEYFILE" ]; then
    set -- "$@" --certfile "$SSL_CERTFILE" --keyfile "$SSL_KEYFILE"
  fi
fi

exec "$@"

