.PHONY: up down logs test test-local dev migrate

up:         ## Start the API + worker + PostgreSQL + Redis (Docker)
	docker compose up --build -d

down:       ## Stop the stack (add -v to also wipe DB + stored PDFs)
	docker compose down

logs:       ## Follow API logs
	docker compose logs -f web

test:       ## Run the test suite in Docker
	docker build --target test -t bcg-tests . && docker run --rm bcg-tests

test-local: ## Run the test suite with the local Python interpreter
	pytest -v

migrate:    ## Apply database migrations (Alembic)
	alembic upgrade head

dev:        ## Run the API locally without Docker (uses SQLite)
	alembic upgrade head && uvicorn app.main:app --reload
