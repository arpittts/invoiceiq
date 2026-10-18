# InvoiceIQ: GST Invoice Intelligence Agent

Project blueprint and build specification. Author: Arpit Singh (GitHub: arpittts). Version 1.0, October 2026.

Revision notes:
- 2026-10-18: Frontend uses React 19 (the current Vite `react-ts` template) instead of React 18.
- 2026-10-18: Hosting is fully free: backend on Render's free web service (Docker, `render.yaml`), Postgres + pgvector on Neon's free plan, frontend on Vercel. Railway was dropped because it has no free plan. Uploaded files are stored as DB blobs in production (`file_blobs` table, `STORAGE_BACKEND=db`).
- 2026-10-18: LLM provider is configurable (`LLM_PROVIDER`). Default is **Google Gemini's free tier** with `gemini-3.5-flash-lite` for extraction and as the fast model (`gemini-3.8-flash` was tried first, but its free tier allows only about 20 requests a day; Flash-Lite matched its accuracy on the starter set and was 2.5x faster) because the owner chose not to pay for an API key; the Anthropic provider stays available (`claude-opus-5-5` / `claude-haiku-5-5`). Only synthetic or demo invoices go to the free tier, since Google may use free-tier inputs to improve its products. PyMuPDF is kept despite its AGPL license; the README explains the implication.

## 0. How to use this document (instructions for the AI builder)

This file is the single source of truth for building InvoiceIQ. If you are an AI coding assistant reading it:

- Build one phase at a time, in the order of section 16. Do not start a phase until the previous phase’s acceptance criteria pass.
- Before writing code, restate the phase goal and list the files you will create or change.
- Write tests together with the code, run them, and show the output. Never claim something works without running it.
- Commit at the end of each phase using the conventions in section 17.
- Never hard-code secrets. Use environment variables and keep `.env.example` current.
- If a requirement is ambiguous, or an external fact (a price, a tax rate, an API detail) may have changed, ask or verify instead of guessing.
- Prefer simple, readable code over clever abstractions. The owner is a third-year CS student who must be able to explain every part in an interview.
- Explain each phase’s commands step by step with exact commands, because the owner prefers that style.

## 1. Project overview

**Name:** InvoiceIQ

**One-liner:** An AI agent that reads Indian GST invoices (photos or PDFs), extracts structured data, validates it, classifies line items with the right HSN/SAC code and GST rate, and lets a human approve the result and query the ledger in plain English.

**Problem:** Small businesses and shops in India still enter purchase and sales invoices into books by hand. This is slow, error-prone, and causes GST input-credit mismatches. Generic OCR returns text but not trustworthy structured data, and nobody checks the tax math.

**Solution:** A pipeline of vision LLM extraction, deterministic validation, retrieval-based tax code lookup, and an agent that decides whether to auto-approve, self-correct, or escalate to a human reviewer. Approved invoices go into a Postgres ledger that can be queried in natural language.

**Target users:** Small business owners, bookkeepers, and accountants. Secondary audience: recruiters and interviewers reviewing the owner’s portfolio.

**Disclaimer to show in the product:** Output is an automation aid, not tax or legal advice. Rates and codes change, so users must verify.

## 2. Objectives and purpose

**Product objectives**

1. Extract invoice fields with high field-level accuracy, measured on a labelled test set.
2. Catch arithmetic and format errors automatically (GSTIN format, tax math, intra- vs inter-state tax logic).
3. Suggest the correct HSN/SAC code and GST rate for each line item, with a cited source row.
4. Keep a human in the loop for low-confidence or failed-validation fields.
5. Let users ask questions about their approved data in natural language, safely.

**Career objectives (why this project exists)**

- Demonstrate the skills AI developer internships ask for: LLM APIs, structured outputs, RAG with embeddings and a vector database, agent orchestration and tool calling, text-to-SQL with guardrails, evaluation, tracing, and deployment.
- Produce numbers a recruiter can quote, such as field accuracy, retrieval recall@5, cost per invoice, and p95 latency.
- Ship a live demo and a clean GitHub repository that can be listed on a resume.

## 3. Scope

**In scope (v1):** single-user-per-account web app, invoice upload (JPG, PNG, PDF), extraction, validation, tax code retrieval, agent pipeline, human review UI, ledger Q&A, evaluation harness, tracing, CI, Docker, deployment, optional MCP server.

**Out of scope (v1):** multi-tenant billing, e-way bills, GSTR filing, direct accounting software integrations, fine-tuning models, mobile app, handwritten invoices.

## 4. Core features and user flows

1. **Upload:** user uploads one or more invoice files. The system stores the file and creates an invoice record with status `processing`.
2. **Extract:** a vision LLM reads page images and returns a strictly typed JSON object with per-field confidence.
3. **Validate:** deterministic rules check formats and math (section 8). Failures are recorded as structured issues.
4. **Classify:** for each line item, hybrid retrieval over HSN/SAC data proposes a code and rate with a citation.
5. **Decide (agent):** a LangGraph workflow chooses one of auto-approve, re-extract a problem field, or send to review.
6. **Review:** the user sees the invoice image beside editable fields, with issues and low-confidence fields highlighted. Approve or correct.
7. **Ledger:** approved invoices become ledger entries. Corrections are saved and feed the evaluation set.
8. **Ask:** the user types a question such as “How much GST did I pay to vendor X last quarter?” and gets an answer, the SQL used, and a result table.
9. **Export:** CSV export of approved invoices and line items.

## 5. System architecture

```
[React UI] --HTTPS--> [FastAPI backend] --> [PostgreSQL 16 + pgvector]
                           |
                           +--> [LangGraph agent pipeline]
                           |        extract -> validate -> classify(RAG) -> decide
                           |        decide -> re-extract (max 2) | review | auto-approve
                           +--> [LLM provider API: vision + text]
                           +--> [Embedding model (local, ONNX)]
                           +--> [Langfuse tracing]
                           +--> [File storage: local volume in dev, object storage or DB blob in prod]
[Optional MCP server] --> calls the same service layer as the API
```

Design principles: LLM calls only where needed, everything else deterministic and testable. A thin provider abstraction so the LLM and embedding models can be swapped via environment variables. Every LLM call is traced and its cost and latency recorded.

## 6. Technology stack

| Layer | Choice | Why |
| --- | --- | --- |
| Language | Python 3.12 | Standard for AI work |
| API | FastAPI, Pydantic v2, Uvicorn | Typed, async, auto docs |
| Database | PostgreSQL 16 + pgvector | One DB for relational data and vectors; fits the owner’s Postgres skills |
| ORM and migrations | SQLAlchemy 2.0, Alembic | Industry standard |
| LLM provider | Anthropic API (vision and tool use for structured output), behind a provider interface | Strong document understanding; swappable. Default models via env: a stronger model for extraction, a cheaper one for routing and text-to-SQL |
| Structured output | Pydantic schemas enforced through tool/JSON schema calls, with retry on schema failure | Reliable typed data |
| PDF handling | PyMuPDF to render pages to images | No OCR engine needed; the vision model reads images |
| Embeddings | fastembed with BAAI/bge-small-en-v1.5 (local, ONNX) | Free, light enough for small hosts |
| Retrieval | pgvector cosine search + Postgres full-text (tsvector), merged by reciprocal rank fusion, optional rerank step | Hybrid search is the modern baseline |
| Orchestration | LangGraph | Stateful agent graphs with conditional edges |
| Text-to-SQL guardrails | sqlglot for parsing and allow-listing, read-only DB role, row limit, statement timeout | Safe natural-language querying |
| Tracing | Langfuse (cloud free tier or self-hosted) | LLM observability, cost and latency |
| Evaluation | pytest-based eval harness, custom metrics; Ragas optional for retrieval faithfulness | Measurable quality |
| Auth | JWT (access token), bcrypt password hashing | Matches owner’s existing skills |
| Frontend | React 19, Vite, TypeScript, Tailwind CSS, TanStack Query | Owner’s strength; fast iteration. React 19 is what the current Vite template ships |
| Optional | MCP Python SDK (FastMCP) | Exposes tools to AI clients |
| Testing | pytest, httpx, pytest-asyncio; Vitest for key UI logic | Quality signal |
| Quality | Ruff, mypy (basic), pre-commit | Clean code signal |
| Containers | Docker, docker-compose (image pgvector/pgvector:pg16) | Reproducible setup |
| CI/CD | GitHub Actions | Tests, lint, eval gate |
| Hosting | Frontend on Vercel; backend as Docker service on Railway or Render; Postgres with pgvector on Neon or a Railway pgvector template | Low cost; verify current free-tier limits before choosing |

## 7. Data model (PostgreSQL)

- **users**: id, email (unique), password_hash, created_at.
- **invoices**: id, user_id, status (`processing`, `needs_review`, `approved`, `rejected`, `failed`), file_id, vendor_name, vendor_gstin, buyer_name, buyer_gstin, invoice_number, invoice_date, place_of_supply, subtotal, cgst, sgst, igst, total, currency (default INR), overall_confidence, created_at, approved_at.
- **invoice_files**: id, user_id, original_name, mime_type, storage_path, page_count, sha256 (used for duplicate detection).
- **line_items**: id, invoice_id, position, description, quantity, unit, unit_price, discount, taxable_value, gst_rate, hsn_sac_code, hsn_source_id, cgst_amount, sgst_amount, igst_amount, line_total, confidence.
- **extraction_runs**: id, invoice_id, attempt, model, raw_json, issues_json, input_tokens, output_tokens, cost_usd, latency_ms, trace_id, created_at.
- **validation_issues**: id, invoice_id, field, rule_code, severity (`error`, `warning`), message, resolved.
- **hsn_codes**: id, code, kind (`HSN` or `SAC`), description, chapter, default_gst_rate, source_note, tsv (tsvector), embedding (vector(384)).
- **review_events**: id, invoice_id, user_id, field, old_value, new_value, created_at (audit trail and future eval data).
- **eval_cases**: id, image_path, ground_truth_json, split (`dev` or `test`), origin (`synthetic` or `real_anonymized`).

Indexes: GIN on `hsn_codes.tsv`, HNSW or IVFFlat on `hsn_codes.embedding`, unique on (user_id, vendor_gstin, invoice_number).

## 8. Extraction schema and validation rules

**Extraction output (Pydantic):** a model `InvoiceExtraction` containing vendor, buyer, invoice number, date, place of supply, a list of `LineItem`, and totals. Every scalar is wrapped as `{value, confidence}` where confidence is 0 to 1. Money fields use `Decimal`, never float. Missing fields must be returned as null, never invented. The prompt must instruct the model to copy values exactly as printed.

**Validation rules (deterministic, each with a rule code and unit tests):**

| Code | Rule |
| --- | --- |
| GSTIN_FORMAT | Matches `^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$`; optional mod-36 checksum |
| STATE_CODE | First two digits are a valid state or UT code |
| TAX_TYPE | If vendor and place-of-supply states match, expect CGST + SGST; otherwise IGST. Flag a mismatch |
| LINE_MATH | quantity × unit_price − discount equals taxable_value within ₹1 tolerance |
| TAX_MATH | taxable_value × rate equals tax amount within ₹1 tolerance; CGST equals SGST |
| TOTAL_MATH | Sum of line totals equals invoice total within ₹1 tolerance |
| DATE_RANGE | Date is not in the future and not older than a configurable limit |
| DUPLICATE | Same file hash, or same vendor GSTIN + invoice number, already exists |
| REQUIRED | Vendor GSTIN, invoice number, date, and total are present |

## 9. RAG design: HSN/SAC tax code lookup

- **Corpus:** official HSN and SAC code lists and GST rate schedules from public government sources (GST portal, CBIC). The owner downloads them manually, and a script in `scripts/ingest_hsn.py` normalizes them into `hsn_codes`. Record the source and download date in `source_note`. Rates change, so the UI must show the source and date.
- **Chunking:** one row per code (code, description, chapter/heading context, rate). No arbitrary text splitting is needed for this corpus, which is a deliberate and explainable design choice.
- **Indexing:** embed `code + description + heading` with bge-small (384 dimensions) and store in pgvector. Also fill a tsvector for keyword search.
- **Query:** the line item description, optionally rewritten by a cheap LLM call into a cleaner product phrase (for example, “brake pad set for motorcycle” to “brake linings and pads”).
- **Retrieval:** top 20 from vector search and top 20 from full-text search, merged with reciprocal rank fusion (k=60).
- **Selection:** a cheap LLM call receives the top 5 candidates and returns the best code, a confidence, and a one-line reason that must cite a candidate id. If the confidence is low, mark the line for human review instead of guessing.
- **Output:** HSN/SAC code, default rate, confidence, and the cited source row shown in the UI.
- **Metrics:** recall@1, recall@5, and MRR on a labelled set of at least 150 line-item descriptions, with a comparison of vector-only vs keyword-only vs hybrid. Put this comparison table in the README.

## 10. Agent workflow (LangGraph)

**State:** invoice_id, page images, extraction, issues, line classifications, attempt count, decision.

**Nodes:**

1. `extract`: vision LLM call returning `InvoiceExtraction`.
2. `validate`: run all rules and collect issues.
3. `classify`: run the RAG lookup per line item.
4. `decide`: route using these rules.
   - No errors and all confidences above threshold: `auto_approve`.
   - Math or format errors on specific fields and attempts below 2: `re_extract`, a targeted second call focused on the failing fields, then back to `validate`.
   - Otherwise: `send_to_review`.
5. `finalize`: persist results, set status, and write the trace.

**Guardrails:** hard cap of 2 re-extraction attempts; per-invoice token and cost budget; a global kill switch via env var. Auto-approve is configurable and off by default in the demo, so a human always confirms.

## 11. Ledger Q&A (text-to-SQL)

- The user asks a question in natural language. A cheap LLM receives the schema of an allow-listed set of views (`v_invoices`, `v_line_items`) with column descriptions and 5 few-shot examples.
- The model returns SQL only. The backend parses it with sqlglot and rejects anything that is not a single SELECT on allow-listed views.
- Execution uses a dedicated read-only database role, a statement timeout of 5 seconds, a row limit of 200, and `WHERE user_id = :uid` enforced by row-level security or by wrapping views per user.
- The response includes the answer in plain language, the SQL, and the result table.
- Evaluate with 30 question and expected-result pairs, and include adversarial prompts (such as “drop table” and “show other users’ data”) in the test suite.

## 12. API and optional MCP server

**REST endpoints (all under `/api/v1`):** `POST /auth/register`, `POST /auth/login`, `POST /invoices` (upload), `GET /invoices` (filter and paginate), `GET /invoices/{id}`, `PATCH /invoices/{id}` (corrections), `POST /invoices/{id}/approve`, `POST /invoices/{id}/reject`, `POST /invoices/{id}/reprocess`, `GET /invoices/export.csv`, `POST /ask`, `GET /hsn/search?q=`, `GET /health`.

Processing runs as a FastAPI background task in v1. The frontend polls invoice status. Keep the pipeline code independent of FastAPI so it can move to a queue later.

**MCP server (optional, last phase):** a FastMCP server exposing tools `extract_invoice(file_path)`, `lookup_hsn(description)`, and `query_ledger(question)`, reusing the same service layer. Document how to connect it to an MCP-capable client.

## 13. Frontend (React + Vite + TypeScript)

Pages: Login and Register; Dashboard (counts by status, totals, recent invoices); Upload (drag and drop, progress); Invoice list (filters by status, vendor, date); Review (image viewer left, editable form right, issues panel, confidence badges, approve and reject buttons); Ask (chat-style input showing the SQL and a result table); Settings (auto-approve toggle, thresholds). Include a “Try demo invoices” button that loads bundled sample invoices so recruiters can test without their own files.

## 14. Evaluation plan

- **Dataset:** at least 60 invoices. Generate synthetic invoices with varied layouts, fonts, and tax structures using a script (`scripts/make_synthetic_invoices.py`) so ground truth is exact. Add a few real invoices only after removing personal data. Never commit real customers’ data.
- **Split:** `dev` set for prompt tuning, `test` set frozen for reporting.
- **Metrics:** field-level exact-match accuracy per field, invoice-level fully-correct rate, validation catch rate on injected errors, auto-approve precision, HSN recall@1 and @5, text-to-SQL execution accuracy, mean cost per invoice, p50 and p95 latency.
- **Harness:** `python -m evals.run --split test` writes a JSON report and a Markdown summary to `evals/reports/`.
- **CI gate:** a small fixed subset runs in GitHub Actions against recorded model outputs (no live API cost) and fails if metrics drop below thresholds. Run the full live evaluation manually before releases.
- **Reporting rule:** only publish numbers measured on the frozen test set, and state the dataset size in the README.

## 15. Repository structure

```
invoiceiq/
  README.md  LICENSE  .env.example  .gitignore  docker-compose.yml  Makefile
  .github/workflows/ci.yml
  backend/
    pyproject.toml  Dockerfile  alembic.ini
    app/
      main.py  config.py  db.py  deps.py
      api/ (auth.py invoices.py ask.py hsn.py health.py)
      models/ (SQLAlchemy)   schemas/ (Pydantic)
      services/ (extraction.py validation.py classification.py ledger_qa.py storage.py)
      agent/ (graph.py nodes.py state.py)
      llm/ (provider.py prompts/ embeddings.py)
      mcp_server.py
    migrations/
    tests/
  frontend/ (Vite React TS app)
  data/ (hsn_raw/ sample_invoices/)
  scripts/ (ingest_hsn.py make_synthetic_invoices.py seed_demo.py)
  evals/ (datasets/ run.py metrics.py reports/)
  docs/ (architecture.md screenshots/ demo.md)
```

## 16. Phased execution plan

Target: about 5 to 6 weeks part-time. Each phase ends with passing acceptance criteria and a commit.

**Phase 0: Setup (1 to 2 days)**

```
mkdir invoiceiq && cd invoiceiq
git init -b main
mkdir -p backend frontend data scripts evals docs
cd backend && python3.12 -m venv .venv && source .venv/bin/activate
pip install fastapi 'uvicorn[standard]' pydantic-settings sqlalchemy alembic psycopg[binary] pgvector python-multipart pyjwt bcrypt pymupdf fastembed langgraph sqlglot langfuse pytest pytest-asyncio httpx ruff
cd ../frontend && npm create vite@latest . -- --template react-ts && npm install tailwindcss @tailwindcss/vite @tanstack/react-query
```

Create `docker-compose.yml` with `pgvector/pgvector:pg16` and run `docker compose up -d db`. Add `.gitignore` (venv, node_modules, .env, uploads, __pycache__) and `.env.example`. Acceptance: `/health` returns ok; database connects; `ruff check` passes.

**Phase 1: Backend foundation and early deploy (week 1)**

Build config, database session, Alembic initial migration (users, invoices, invoice_files, line_items), JWT register and login, and file upload that saves a file and creates a record. Deploy this skeleton to Railway (or Render) and Vercel now, so deployment problems surface early. Acceptance: register, login, and upload work locally and on the deployed URL; tests cover auth and upload.

**Phase 2: Extraction pipeline (week 1 to 2)**

Implement the LLM provider interface, PDF-to-image rendering, the extraction prompt, the `InvoiceExtraction` schema with retry on schema failure, and saving `extraction_runs` with tokens, cost, and latency. Create the synthetic invoice generator and 20 starter invoices. Acceptance: extraction returns a valid schema on 90 percent or more of starter invoices; every call is traced in Langfuse.

**Phase 3: Validation engine (week 2)**

Implement every rule in section 8 as a pure function with unit tests, including tests with injected errors. Acceptance: all rule tests pass; the validation catch rate on injected errors is reported.

**Phase 4: Review UI (week 2 to 3)**

Build Upload, Invoice list, and Review pages with an image viewer, editable fields, issue highlighting, and approve and reject actions. Save corrections to `review_events`. Acceptance: a full manual flow works end to end in the browser on the deployed app.

**Phase 5: RAG for HSN/SAC (week 3 to 4)**

Download the official lists, write `ingest_hsn.py`, create the `hsn_codes` table with embeddings and tsvector, implement hybrid retrieval with RRF and the LLM selection step, expose `GET /hsn/search`, and integrate into the line-item flow. Build the labelled retrieval set and the comparison of vector-only vs keyword-only vs hybrid. Acceptance: retrieval metrics are produced by a script; the UI shows the code, rate, confidence, and source row.

**Phase 6: LangGraph agent (week 4)**

Rebuild the pipeline as the graph in section 10 with the re-extraction loop, budgets, and the configurable auto-approve. Add graph-level tests using a fake LLM. Acceptance: injected-error invoices trigger the re-extract path or the review path as designed; no infinite loops.

**Phase 7: Ledger Q&A (week 5)**

Create the views, read-only role, sqlglot guard, `/ask` endpoint, and Ask page. Write 30 test questions and adversarial tests. Acceptance: execution accuracy is measured; all adversarial queries are blocked.

**Phase 8: Evaluation and CI (week 5)**

Finish the harness, freeze the test split, generate the report, and set up the GitHub Actions workflow (lint, unit tests, replayed eval subset, frontend build). Acceptance: CI is green; the README metrics table is filled from the real report.

**Phase 9: Polish, optional MCP, and release (week 6)**

Add the demo-invoice button, rate limiting, a cost cap, the optional MCP server, the architecture diagram, a 60-second demo video, and a tagged release `v1.0.0`. Acceptance: the definition of done in section 22 is met.

## 17. Git and GitHub plan

- Repository name: `invoiceiq`. Visibility: public, with an MIT license.
- Branching: `main` is always deployable. Work in short-lived branches such as `feat/extraction` and merge through pull requests, even when working alone, so the history looks professional.
- Commit messages: Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- Commands for the first push (after creating an empty repo on GitHub):

```
git add . && git commit -m 'chore: initial project setup'
git remote add origin https://github.com/arpittts/invoiceiq.git
git push -u origin main
```

- Never commit `.env`, API keys, real invoices, or database dumps. If a secret is committed by mistake, rotate it immediately; deleting the commit is not enough.
- Add GitHub repository topics (`llm`, `rag`, `langgraph`, `fastapi`, `pgvector`, `document-ai`, `gst`), a short description, and the live demo URL. Pin the repository on the GitHub profile.
- README structure: title and one-liner; live demo link and demo video; problem and solution; screenshots; architecture diagram; features; tech stack; evaluation results table; design decisions and what failed; run locally with exact commands; environment variables; deployment; limitations; roadmap; license.

## 18. Deployment plan

- **Backend:** build from `backend/Dockerfile` (Python 3.12 slim). Start command runs migrations, then the server: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Add a `/health` check.
- **Database:** managed Postgres with pgvector enabled (`CREATE EXTENSION IF NOT EXISTS vector;` in the first migration). Run `scripts/ingest_hsn.py` and `scripts/seed_demo.py` once against it.
- **Frontend:** deploy the `frontend` folder to Vercel with `VITE_API_URL` pointing to the backend. Configure CORS on the backend to allow only that origin.
- **Environment variables:** `DATABASE_URL`, `JWT_SECRET`, `LLM_API_KEY`, `LLM_MODEL_EXTRACT`, `LLM_MODEL_FAST`, `EMBEDDING_MODEL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `MAX_COST_PER_INVOICE_USD`, `DAILY_BUDGET_USD`, `AUTO_APPROVE_ENABLED`, `ALLOWED_ORIGINS`.
- **Demo safety:** per-user and per-IP rate limits, a maximum file size and page count, a daily spending cap that disables LLM calls when reached, and a clear message when the cap is hit. Pre-load sample invoices so the demo works even if the cap is reached.
- **Cold starts:** free tiers may sleep. Note this in the README and show a loading state in the UI.
- **Storage:** hosts with ephemeral disks lose uploaded files on restart. Use object storage or store files as database blobs for the demo.
- Verify current pricing and free-tier limits of each host before relying on them.

## 19. Security, privacy, and cost control

- Hash passwords with bcrypt; short-lived JWTs; validate file type and size server-side.
- Row-level isolation: every query is scoped by `user_id`.
- Treat invoice content as untrusted input. Text inside an invoice must never be able to change system behavior (prompt injection). Keep instructions and document content clearly separated in prompts, and never give the extraction step tools.
- Do not log full invoice contents in traces for real users; mask GSTIN and amounts in logs where practical.
- Cost: use the cheaper model for routing, rewriting, and text-to-SQL; cache results by file hash; track `cost_usd` per run and show totals on the dashboard.

## 20. Resume and interview packaging

**Resume bullets (fill the brackets only with measured results):**

- Built InvoiceIQ, an LLM agent that extracts and validates GST invoices, reaching [X]% field-level accuracy on a [N]-invoice held-out set at [₹/$]Y per invoice.
- Implemented hybrid retrieval (pgvector + full-text with rank fusion) for HSN/SAC classification, improving recall@5 from [A]% (vector-only) to [B]%.
- Designed a LangGraph workflow with deterministic validation, targeted re-extraction, and human-in-the-loop review; added tracing with Langfuse and an eval-gated CI pipeline.
- Shipped a guarded text-to-SQL ledger assistant (read-only role, SQL allow-listing) and deployed the full stack with Docker, Vercel, and Railway.

**Interview talking points:** why hybrid search beat vector-only; how validation catches model mistakes; why a human review step exists; what failed and what you changed; how you control cost; how you prevent prompt injection and unsafe SQL; how you would scale processing with a queue.

## 21. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| LLM misreads digits | Per-field confidence, deterministic math checks, targeted re-extraction, human review |
| Scope too large | Strict phase order; every phase is demoable on its own |
| API cost overruns | Budgets, caching, cheaper model for light steps, daily cap |
| Outdated tax data | Show source and date, disclaimer, easy re-ingestion script |
| Free-tier limits on hosting | Early deployment, demo mode, documented cold starts |
| Privacy of real invoices | Synthetic data for evaluation; never commit real data |

## 22. Definition of done

- [ ] Live frontend and backend URLs work, with demo invoices loadable in one click.
- [ ] Public GitHub repository with README, license, architecture diagram, screenshots, and demo video.
- [ ] CI is green (lint, tests, replayed eval subset, frontend build).
- [ ] Evaluation report generated from the frozen test set, with numbers copied into the README.
- [ ] Retrieval comparison table (vector vs keyword vs hybrid) included.
- [ ] Adversarial tests for prompt injection and unsafe SQL pass.
- [ ] No secrets in the repository history; `.env.example` is complete.
- [ ] Release `v1.0.0` tagged; repository pinned on GitHub; resume bullets filled with measured numbers.
