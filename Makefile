.PHONY: db db-down dev test lint web

db:
	docker compose up -d db

db-down:
	docker compose down

dev:
	cd backend && .venv/bin/uvicorn app.main:app --reload

test:
	cd backend && .venv/bin/pytest -v

lint:
	cd backend && .venv/bin/ruff check .

web:
	cd frontend && npm run dev
