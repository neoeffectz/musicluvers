# Music Luvers (SoundMatch) — one image for every Render service:
#   web    -> runs docker-entrypoint.sh (migrate, collectstatic, Daphne on $PORT)
#   worker -> same image with a different command (Celery worker + beat)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

WORKDIR /app

# libpq/gcc: Postgres driver + building the one source-only package (http-ece)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN chmod +x docker-entrypoint.sh \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /app/staticfiles \
    && chown -R app:app /app

# Don't run as root
USER app

# Render tells the app which port to use via $PORT; 8000 when run elsewhere
EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
