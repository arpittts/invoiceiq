# InvoiceIQ

An AI agent that reads Indian GST invoices (photos or PDFs), extracts structured data, validates it, classifies line items with the right HSN/SAC code and GST rate, and lets a human approve the result and query the ledger in plain English.

> Status: early development (Phase 1: backend foundation). The full plan is in [docs/BLUEPRINT.md](docs/BLUEPRINT.md).

> Disclaimer: output is an automation aid, not tax or legal advice. Rates and codes change, so always verify.

## Run locally

Requirements: Python 3.12, Node 22, Docker.

```bash
cp .env.example .env                 # then set JWT_SECRET to a long random string
make db                              # start Postgres + pgvector (also creates the test database)
cd backend && python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]" && cd ..
make migrate                         # create the tables
make dev                             # API on http://localhost:8000 (interactive docs at /docs)
make test && make lint
cp frontend/.env.example frontend/.env.local
cd frontend && npm install && cd ..
make web                             # frontend on http://localhost:5173
```

## API so far

All routes are under `/api/v1`.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/health` | no | API and database status |
| POST | `/auth/register` | no | Create an account (`email`, `password` of 8 to 72 bytes) |
| POST | `/auth/login` | no | Get a JWT access token |
| GET | `/auth/me` | yes | The logged-in user |
| POST | `/invoices` | yes | Upload a PDF, PNG or JPEG (max 10 MB, 10 pages); creates an invoice with status `processing` |

Uploaded files are stored on disk in development (`STORAGE_BACKEND=local`) and in Postgres in production (`STORAGE_BACKEND=db`), because free hosts wipe their disks on restart.

## License

MIT
