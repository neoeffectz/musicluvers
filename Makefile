.DEFAULT_GOAL := help

SETTINGS_DEV := config.settings.dev
SETTINGS_PROD := config.settings.prod
MANAGE := python manage.py

.PHONY: help run worker beat migrate makemigrations shell superuser dbshell test check freeze docker-build docker-up

help:
	@echo "make run            - run dev server (127.0.0.1:8000)"
	@echo "make worker         - run the Celery worker (handles events)"
	@echo "make beat           - run Celery beat (outbox sweeper)"
	@echo "make migrate        - apply migrations"
	@echo "make makemigrations - create migrations"
	@echo "make shell          - Django shell"
	@echo "make superuser      - create superuser"
	@echo "make dbshell        - open psql on the dev DB"
	@echo "make test           - run tests"
	@echo "make check          - django system checks"
	@echo "make freeze         - lock requirements.txt"
	@echo "make docker-build   - build the Docker image"
	@echo "make docker-up      - run the app via docker-compose"

run:
	$(MANAGE) runserver 127.0.0.1:8000 --settings=$(SETTINGS_DEV)

worker:
	DJANGO_SETTINGS_MODULE=$(SETTINGS_DEV) celery -A config worker -l info

beat:
	DJANGO_SETTINGS_MODULE=$(SETTINGS_DEV) celery -A config beat -l info

migrate:
	$(MANAGE) migrate --settings=$(SETTINGS_DEV)

makemigrations:
	$(MANAGE) makemigrations --settings=$(SETTINGS_DEV)

shell:
	$(MANAGE) shell --settings=$(SETTINGS_DEV)

superuser:
	$(MANAGE) createsuperuser --settings=$(SETTINGS_DEV)

dbshell:
	$(MANAGE) dbshell --settings=$(SETTINGS_DEV)

test:
	PIPENV_DONT_LOAD_ENV=1 DATABASE_URL=postgres://localhost:5432/postgres REDIS_URL=redis://localhost:6379 $(MANAGE) test --settings=$(SETTINGS_DEV)

check:
	$(MANAGE) check --settings=$(SETTINGS_DEV)

freeze:
	pip freeze > requirements.txt

docker-build:
	docker compose build

docker-up:
	docker compose up