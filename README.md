# InvoiceIQ

An AI agent that reads Indian GST invoices (photos or PDFs), extracts structured data, validates it, classifies line items with the right HSN/SAC code and GST rate, and lets a human approve the result and query the ledger in plain English.

> Status: early development (Phase 2: extraction pipeline). The full plan is in [docs/BLUEPRINT.md](docs/BLUEPRINT.md).

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
| POST | `/invoices` | yes | Upload a PDF, PNG or JPEG (max 10 MB, 10 pages); creates an invoice with status `processing` and starts extraction in the background |
| GET | `/invoices/{id}` | yes | Status, extracted fields, line items and an extraction summary (attempts, cost, issues) |

Uploaded files are stored on disk in development (`STORAGE_BACKEND=local`) and in Postgres in production (`STORAGE_BACKEND=db`), because free hosts wipe their disks on restart.

## Extraction pipeline

After upload, a background task renders each page to an image and sends it to a vision LLM with a strict JSON schema (structured outputs). The provider is a setting: **Google Gemini's free tier** by default (`LLM_PROVIDER=gemini`, model `gemini-3.5-flash-lite`), or Anthropic Claude (`LLM_PROVIDER=anthropic`, paid). Gemini's free tier may use inputs to improve Google's products, so only synthetic or demo invoices are sent to it. The model copies every value exactly as printed, with a confidence from 0 to 1; our own code then parses amounts into `Decimal`, dates (day-first, as on Indian invoices) and rates. If that validation fails, the model is asked again with the error (up to 2 retries). Every attempt is stored in `extraction_runs` with tokens, cost and latency, and traced in Langfuse without any invoice contents.

Safety: the extraction call has no tools, our instructions live in the system prompt, and invoice text is treated as untrusted data.

Test data: `make invoices` regenerates 20 synthetic invoices (4 layouts, PDF/scan/photo, CGST+SGST and IGST) with exact ground truth in `evals/datasets/starter/`. `make check-extraction` runs real extraction on them and reports the valid-schema rate, cost and latency (it asks before calling the API).

## Deployment (all free tiers)

| Part | Host | Notes |
| --- | --- | --- |
| Frontend | Vercel | Root directory `frontend`, env `VITE_API_URL` = backend URL |
| Backend | Render free web service | Defined in [render.yaml](render.yaml); runs migrations on start; sleeps after 15 minutes idle, so the first request can take about a minute |
| Database | Neon free plan | Postgres 16 with pgvector; scales to zero when idle and wakes on the next query |

Free tiers change, so check each provider's current limits before relying on them.

## License

MIT for this repository's code. Note: the backend depends on [PyMuPDF](https://github.com/pymupdf/PyMuPDF), which is licensed under the GNU AGPL v3 (or a commercial license from Artifex). This repository is public, which satisfies the AGPL's source-availability requirement, but anyone reusing this code in a closed-source service must either replace PyMuPDF (for example with pypdfium2) or obtain a commercial license.
