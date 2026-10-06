.PHONY: up down build logs ps api-test web-build clean

up:
	docker compose up --build

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

ps:
	docker compose ps

api-test:
	docker compose run --rm api pytest

web-build:
	docker compose run --rm web npm run build

clean:
	docker compose down -v --remove-orphans
