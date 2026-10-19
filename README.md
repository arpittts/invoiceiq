# InvoiceIQ

An AI agent that reads Indian GST invoices (photos or PDFs), extracts structured data, validates it, classifies line items with the right HSN/SAC code and GST rate, and lets a human approve the result and query the ledger in plain English.

> Status: early development (Phase 7: ledger Q&A). The full plan is in [docs/BLUEPRINT.md](docs/BLUEPRINT.md).

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
| GET | `/hsn/search?q=&mode=` | yes | Official HSN/SAC codes for a description (`mode`: hybrid, vector or keyword) |
| POST | `/ask` | yes | A plain-English question about your approved invoices: answer, SQL and rows |

Uploaded files are stored on disk in development (`STORAGE_BACKEND=local`) and in Postgres in production (`STORAGE_BACKEND=db`), because free hosts wipe their disks on restart.

## Extraction pipeline

After upload, a background task renders each page to an image and sends it to a vision LLM with a strict JSON schema (structured outputs). The provider is a setting: **Google Gemini's free tier** by default (`LLM_PROVIDER=gemini`, model `gemini-3.5-flash-lite`), or Anthropic Claude (`LLM_PROVIDER=anthropic`, paid). Gemini's free tier may use inputs to improve Google's products, so only synthetic or demo invoices are sent to it. The model copies every value exactly as printed, with a confidence from 0 to 1; our own code then parses amounts into `Decimal`, dates (day-first, as on Indian invoices) and rates. If that validation fails, the model is asked again with the error (up to 2 retries). Every attempt is stored in `extraction_runs` with tokens, cost and latency, and traced in Langfuse without any invoice contents.

Safety: the extraction call has no tools, our instructions live in the system prompt, and invoice text is treated as untrusted data.

Test data: `make invoices` regenerates 20 synthetic invoices (4 layouts, PDF/scan/photo, CGST+SGST and IGST) with exact ground truth in `evals/datasets/starter/`. `make check-extraction` runs real extraction on them and reports the valid-schema rate, cost and latency (it asks before calling the API).

## Review UI

The frontend (React + Vite + TypeScript + Tailwind + TanStack Query) has sign-in, an upload page (drag and drop, progress), an invoice list (filters, auto-refresh while extracting) and a review screen: the page image next to editable fields, validation issues highlighted on their fields, low-confidence fields marked "check", and Approve / Reject. Approve stays disabled until every error is fixed (validation re-runs on save) or dismissed. Every human change is stored in `review_events` as an audit trail. Run `npm test` in `frontend/` for the unit tests.

## Ledger Q&A (text-to-SQL)

The **Ask** page answers questions like "How much GST did I pay to Sharma Auto Parts last quarter?" with a plain-language answer, the SQL that ran, and the result table.

Two independent walls keep it safe:

1. **SQL guard** (`app/services/sql_guard.py`, sqlglot, allow-list): exactly one SELECT, only the views `v_invoices` and `v_line_items`, only harmless functions (no `set_config`, `pg_sleep`, `pg_read_file`...), no schema-qualified names, and a 200-row limit.
2. **The database itself**: each question runs in a transaction that is always rolled back, as the no-login role `ledger_reader` (can SELECT the two views and nothing else), with a 5-second statement timeout and read-only mode. The views show only approved invoices of the user named for the current connection in `ledger_query_context`, a table the read-only role can't write. (A setting such as `app.user_id` would not be enough: any role can change a setting, so a query that slipped past the guard could switch users. Tests bypass the guard on purpose to prove the database alone still holds.)

**Evaluation** (`make eval-ledger`): a fresh database seeded with the 20 synthetic invoices plus another user's "Secret Rival Pvt Ltd" invoices; 30 questions with hand-written reference SQL; "today" pinned to 2026-10-18.

| Metric | Result |
| --- | --- |
| Execution accuracy, strict (same rows) | **29/30 (97%)** |
| Execution accuracy, lenient (extra helpful columns allowed) | **30/30 (100%)** |
| Adversarial prompts (drop table, other users' data, set_config, pg_sleep, password hashes...) | **8/8 safe, database unchanged** |

The first run scored 73%: the evaluation caught a guard bug (sqlglot parses `AND`/`OR` as functions, so every date range was rejected). Guard tests with logic operators now prevent it from returning.

## Agent (LangGraph)

Processing an invoice is a LangGraph state graph (`backend/app/agent/`). This diagram is generated from the code:

```mermaid
graph TD;
	__start__([<p>__start__</p>])
	extract(extract)
	validate(validate)
	re_extract(re_extract)
	save(save)
	classify(classify)
	final_check(final_check)
	decide(decide)
	finalize(finalize)
	__end__([<p>__end__</p>])
	__start__ --> extract;
	classify --> final_check;
	decide --> finalize;
	extract -.-> finalize;
	extract -.-> validate;
	final_check --> decide;
	re_extract --> validate;
	save --> classify;
	validate -.-> re_extract;
	validate -.-> save;
	finalize --> __end__;
```

- **validate → re_extract:** if the deterministic rules find errors, the model re-reads the page with the failing fields and rule messages named, and only those fields (and their line items) are merged into the first read, so correct fields can't get worse. At most 2 re-reads (`MAX_REEXTRACTIONS`), and only within the per-invoice token and cost budget.
- **classify** runs once, after the numbers are settled, so re-reads don't repeat the HSN lookup.
- **decide:** auto-approve is off by default (`AUTO_APPROVE_ENABLED`); when on, it needs no errors or warnings, overall confidence of at least 0.9 and a confident HSN/SAC code on every line. Everything else goes to human review.
- **No infinite loops:** the re-read cap, the budget and LangGraph's recursion limit (25 steps; the longest legal path is 11). A test raises the cap to 1,000 and checks the recursion limit still stops the run.
- Each run is one Langfuse trace whose output is the path taken, the decision and the reason.

## HSN/SAC lookup (RAG)

Every line item gets a suggested HSN (goods) or SAC (services) code and an indicative GST rate, citing the official row it came from.

- **Corpus:** the GST portal's HSN directory (21,928 HSN + 681 SAC codes), one row per code with its parent heading as context. Rates come from Notification 09/2025-Central Tax (Rate) as amended by 01/2026: each code takes the rate of its most specific schedule entry; 73% of HSN codes get a single rate, 24% depend on the description (e.g. T-shirts by price, motorcycles by engine size) and are left for a human. Rates are indicative and each row cites its schedule entry; services rates, amendment 19/2025 and the exemption list (Notification 02/2017, nil-rated goods such as fresh vegetables) are not loaded, so an exempt item can show a 5% default. The `RATE_MISMATCH` warning flags exactly that case for a person to check, which is also why it blocks auto-approval.
- **Retrieval:** pgvector (bge-small-en-v1.5, HNSW) + Postgres full-text + the code printed on the invoice, merged with reciprocal rank fusion (k=60).
- **Selection:** one LLM call per invoice picks from each line's top 5 and must cite a candidate id; uncited or low-confidence picks are left for the reviewer.
- **Checks:** `HSN_MISMATCH` and `RATE_MISMATCH` warnings when the printed code or rate disagrees with the suggestion.

### Retrieval results

181 labelled invoice-style descriptions (written by an LLM from codes sampled across 98 chapters, filtered to avoid copying official wording, spot-checked). Heading = first 4 digits, which usually decides the rate. `make eval-retrieval` reproduces the final row set.

| Mode | Heading recall@1 | Heading recall@5 | Heading MRR | Code recall@5 |
| --- | --- | --- | --- | --- |
| Vector only | 53.6% | 71.8% | 0.599 | 50.3% |
| Keyword only | 53.0% | 66.3% | 0.579 | 53.0% |
| **Hybrid (RRF)** | **68.0%** | **81.8%** | **0.730** | **67.4%** |

What changed along the way (all measured on the same set):

1. **Baseline:** hybrid barely beat vector (heading recall@5 70.2% vs 68.5%) and was worse at rank 1, because keyword search was weak (24% @1): the word "bag" in "Basmati rice 25kg bag" returned handbags.
2. **Query cleanup + better keyword ranking:** dropping quantities and packing words, excluding 2-digit chapter rows, and ranking keyword hits by how many query words they contain lifted keyword @1 from 24% to 53% and hybrid @5 to 79.6%.
3. **A measurement bug, not a model bug:** after re-embedding rows, vector recall dropped 10 points. Cause: 13,633 dead rows behind pgvector's HNSW index. `VACUUM` + `REINDEX` restored it; the ingest script now does this after every load.
4. **Heading-first text for "OTHER" rows** (18% of the list): first judged a regression on the dirty index, then re-tested fairly: hybrid rank-1 heading recall 63.0% -> 68.0%. Kept.

Caveat: the descriptions are LLM-written from known codes, so these numbers measure realistic phrasing, not real customer invoices; code-level labels carry some noise (e.g. one "maple syrup" item labelled with the solid maple sugar code).

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
