# InvoiceIQ

An AI agent that reads Indian GST invoices (photos or PDFs), extracts structured data, validates it, classifies line items with the right HSN/SAC code and GST rate, and lets a human approve the result and query the ledger in plain English.

> Status: early development (Phase 4: review UI). The full plan is in [docs/BLUEPRINT.md](docs/BLUEPRINT.md).

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
| GET | `/invoices` | yes | Your invoices, filtered by `status`, `vendor`, `date_from`, `date_to`, with `limit`/`offset` paging |
| GET | `/invoices/{id}` | yes | Status, extracted fields, line items, validation issues, per-field AI confidence and an extraction summary |
| GET | `/invoices/{id}/pages/{n}` | yes | Page `n` of the invoice as an image (for the review viewer) |
| PATCH | `/invoices/{id}` | yes | Reviewer corrections; each change is logged to `review_events` and validation re-runs |
| POST | `/invoices/{id}/issues/{issue_id}/dismiss` | yes | Mark a flagged issue as checked and acceptable (logged) |
| POST | `/invoices/{id}/approve` | yes | Add to the ledger; blocked while errors are open or if the same vendor invoice is already approved |
| POST | `/invoices/{id}/reject` | yes | Reject the invoice |

Uploaded files are stored on disk in development (`STORAGE_BACKEND=local`) and in Postgres in production (`STORAGE_BACKEND=db`), because free hosts wipe their disks on restart.

## Extraction pipeline

After upload, a background task renders each page to an image and sends it to a vision LLM with a strict JSON schema (structured outputs). The provider is a setting: **Google Gemini's free tier** by default (`LLM_PROVIDER=gemini`, model `gemini-3.5-flash-lite`), or Anthropic Claude (`LLM_PROVIDER=anthropic`, paid). Gemini's free tier may use inputs to improve Google's products, so only synthetic or demo invoices are sent to it. The model copies every value exactly as printed, with a confidence from 0 to 1; our own code then parses amounts into `Decimal`, dates (day-first, as on Indian invoices) and rates. If that validation fails, the model is asked again with the error (up to 2 retries). Every attempt is stored in `extraction_runs` with tokens, cost and latency, and traced in Langfuse without any invoice contents.

Safety: the extraction call has no tools, our instructions live in the system prompt, and invoice text is treated as untrusted data.

Test data: `make invoices` regenerates 20 synthetic invoices (4 layouts, PDF/scan/photo, CGST+SGST and IGST) with exact ground truth in `evals/datasets/starter/`. `make check-extraction` runs real extraction on them and reports the valid-schema rate, cost and latency (it asks before calling the API).

## Review UI

The frontend (React + Vite + TypeScript + Tailwind + TanStack Query) has sign-in, an upload page (drag and drop, progress), an invoice list (filters, auto-refresh while extracting) and a review screen: the page image next to editable fields, validation issues highlighted on their fields, low-confidence fields marked "check", and Approve / Reject. Approve stays disabled until every error is fixed (validation re-runs on save) or dismissed. Every human change is stored in `review_events` as an audit trail. Run `npm test` in `frontend/` for the unit tests.

## Validation rules

After extraction, deterministic rules check what the model read (no AI involved): GSTIN format and mod-36 checksum, state codes, CGST+SGST vs IGST for the place of supply, line math, tax math, totals (each within ₹1), date range, duplicates and required fields. Problems are stored as issues with a rule code, field, severity and message, and returned with the invoice.

`make check-validation` injects 12 kinds of realistic misreads into the 20 correct starter invoices. Latest run: **0 false alarms, 97.9% of 632 injected errors caught**. Every miss was a digit swap in the paise (largest ₹0.72), which is inside the ₹1 rounding tolerance and cannot be detected by arithmetic.

## Deployment (all free tiers)

| Part | Host | Notes |
| --- | --- | --- |
| Frontend | Vercel | Root directory `frontend`, env `VITE_API_URL` = backend URL |
| Backend | Render free web service | Defined in [render.yaml](render.yaml); runs migrations on start; sleeps after 15 minutes idle, so the first request can take about a minute |
| Database | Neon free plan | Postgres 16 with pgvector; scales to zero when idle and wakes on the next query |

Free tiers change, so check each provider's current limits before relying on them.

## License

MIT for this repository's code. Note: the backend depends on [PyMuPDF](https://github.com/pymupdf/PyMuPDF), which is licensed under the GNU AGPL v3 (or a commercial license from Artifex). This repository is public, which satisfies the AGPL's source-availability requirement, but anyone reusing this code in a closed-source service must either replace PyMuPDF (for example with pypdfium2) or obtain a commercial license.
