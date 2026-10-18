.PHONY: db db-down migrate dev test lint web invoices check-extraction

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
	cd backend && .venv/bin/ruff check . ../scripts && .venv/bin/ruff format --check . ../scripts

web:
	cd frontend && npm run dev

invoices:
	backend/.venv/bin/python scripts/make_synthetic_invoices.py --count 20 --seed 42

check-extraction:
	backend/.venv/bin/python scripts/check_extraction.py
