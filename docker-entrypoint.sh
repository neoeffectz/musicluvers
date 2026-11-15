#!/bin/sh
# With a command (e.g. Render's worker: "celery -A config worker -B ..."), just run it.
# Without one, this is the web service: update the database, gather static files, start the server.
set -e

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec daphne -b 0.0.0.0 -p "${PORT:-8000}" --proxy-headers config.asgi:application
