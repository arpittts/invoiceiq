.PHONY: db db-down migrate dev test lint web

db:
	docker compose up -d --wait db

db-down:
	docker compose down

migrate:
	cd backend && .venv/bin/alembic upgrade head

dev:
	cd backend && .venv/bin/uvicorn app.main:app --reload

test:
	cd backend && .venv/bin/pytest -v

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .

web:
	cd frontend && npm run dev
