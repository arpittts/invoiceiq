.PHONY: db db-down migrate dev test lint web invoices invoices-test check-extraction check-validation ingest-hsn eval-retrieval eval-ledger eval eval-record

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
	cd backend && .venv/bin/ruff check . ../scripts ../evals && .venv/bin/ruff format --check . ../scripts ../evals

web:
	cd frontend && npm run dev

invoices:
	backend/.venv/bin/python scripts/make_synthetic_invoices.py --count 20 --seed 42

# The frozen test split (60 harder invoices). Only regenerate on purpose: the
# recordings in evals/recordings/ are tied to these exact files.
invoices-test:
	backend/.venv/bin/python scripts/make_synthetic_invoices.py --hard --count 60 --seed 2026 \
		--prefix test --out data/sample_invoices/test --truth evals/datasets/test

check-extraction:
	backend/.venv/bin/python scripts/check_extraction.py

check-validation:
	backend/.venv/bin/python scripts/check_validation.py

ingest-hsn:
	backend/.venv/bin/python scripts/ingest_hsn.py --dir "data/hsn raw"

eval-retrieval:
	backend/.venv/bin/python scripts/eval_retrieval.py

eval-ledger:
	backend/.venv/bin/python scripts/eval_ledger_qa.py

# The evaluation harness. `eval` replays recorded model replies (free, no key, what CI runs);
# `eval-record` calls the real model and refreshes the recordings (free on Gemini, ~40 min).
eval:
	backend/.venv/bin/python -m evals.run --split test --gate

eval-record:
	backend/.venv/bin/python -m evals.run --split test --record
