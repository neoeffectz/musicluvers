#!/bin/sh
# With a command (e.g. a separate Render worker: "celery -A config worker -B ..."), just run it.
# Without one, this is the web service: update the database, gather static files, start the server.
set -e

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

python manage.py migrate --noinput
python manage.py collectstatic --noinput

# Free plan (no separate worker service): run the background jobs (match scores, notifications,
# emails, city lookups, the 1-minute sweeper) in this same container, next to the website.
# One process with 4 threads: a slow job (a phone notification, a city lookup) can't hold up the others,
# and it stays light on memory. No chatter between workers, to save Upstash requests.
# If it ever stops, it restarts itself after 5 seconds.
if [ "${RUN_WORKER_IN_WEB:-}" = "1" ]; then
  (
    while true; do
      celery -A config worker -B -l info --pool threads --concurrency 4 \
        --without-gossip --without-mingle --without-heartbeat \
        --schedule /tmp/celerybeat-schedule || true
      echo "Background worker stopped; restarting in 5 seconds" >&2
      sleep 5
    done
  ) &
fi

exec daphne -b 0.0.0.0 -p "${PORT:-8000}" --proxy-headers config.asgi:application
