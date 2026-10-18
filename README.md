# InvoiceIQ

An AI agent that reads Indian GST invoices (photos or PDFs), extracts structured data, validates it, classifies line items with the right HSN/SAC code and GST rate, and lets a human approve the result and query the ledger in plain English.

> Status: early development (Phase 0: project setup).

> Disclaimer: output is an automation aid, not tax or legal advice. Rates and codes change, so always verify.

## Run locally

Requirements: Python 3.12, Node 22, Docker.

```bash
cp .env.example .env
make db                      # start Postgres + pgvector
cd backend && python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
make dev                     # API on http://localhost:8000 (docs at /docs)
make test && make lint
make web                     # frontend on http://localhost:5173
```

## License

MIT
