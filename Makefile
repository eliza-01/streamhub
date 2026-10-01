.PHONY: up down logs migrate test lint

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

migrate:
	docker compose run --rm migrate

test:
	pytest -q

lint:
	ruff check .
