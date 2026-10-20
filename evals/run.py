"""The evaluation harness (blueprint section 12): one command, one report.

    backend/.venv/bin/python -m evals.run --split test            # replay (no key, free)
    backend/.venv/bin/python -m evals.run --split test --record   # real model, saves replies
    backend/.venv/bin/python -m evals.run --split test --gate     # CI: fail below thresholds

What it measures, on a throwaway database (invoiceiq_eval, rebuilt every run):
- extraction: field accuracy (per field and per file type) and the share of
  invoices read fully correctly, after the whole agent ran (re-reads included);
- validation: how many real misreads the rules flagged, false alarms on correct
  reads, and the catch rate for injected misreads (no AI, deterministic);
- auto-approve precision: of the invoices the agent would approve on its own, how
  many were fully correct (needs HSN codes, so only in --record runs locally);
- ledger Q&A: execution accuracy and adversarial safety;
- cost per invoice and p50/p95 latency.

Modes:
- replay (default): serve the replies saved in evals/recordings/. No API key, no
  network, same numbers every time. If a prompt, the schema or page rendering
  changed, replay stops with "recordings are out of date": run --record again.
- --record: call the configured model and save every reply (free on Gemini).
  --resume keeps cases already recorded, for when the free daily quota runs out.
- --live: call the model without saving anything.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from evals.metrics import field_results, percentile, rate  # noqa: E402

SPLITS = {
    "dev": REPO / "evals/datasets/starter/manifest.json",
    "test": REPO / "evals/datasets/test/manifest.json",
}
RECORDINGS = REPO / "evals/recordings"
REPORTS = REPO / "evals/reports"
THRESHOLDS = REPO / "evals/thresholds.json"
EVAL_DB = "invoiceiq_eval"
TODAY = date(2026, 10, 18)  # pinned so date rules give the same answer every year
MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", None}


class OutOfDate(Exception):
    """Replay asked for something the recordings don't have."""


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def prepare_database(copy_hsn: bool) -> tuple[str, int]:
    """Create a fresh eval database next to the configured one. Returns (url, HSN rows)."""
    from sqlalchemy import create_engine, make_url

    from app.config import get_settings

    source = make_url(get_settings().database_url)
    if source.host not in LOCAL_HOSTS:
        sys.exit(f"Refusing to create an eval database on {source.host}: use a local Postgres.")
    target = source.set(database=EVAL_DB)
    admin = create_engine(source, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.exec_driver_sql(f"DROP DATABASE IF EXISTS {EVAL_DB} WITH (FORCE)")
        conn.exec_driver_sql(f"CREATE DATABASE {EVAL_DB}")
    admin.dispose()
    target_url = target.render_as_string(hide_password=False)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO / "backend",
        env={**os.environ, "DATABASE_URL": target_url},
        check=True,
        capture_output=True,
    )
    copied = copy_hsn_codes(source, target) if copy_hsn else 0
    return target_url, copied


def copy_hsn_codes(source, target) -> int:
    """Copy the ingested HSN/SAC catalogue (with embeddings) into the eval database."""
    from sqlalchemy import create_engine, text

    source_engine, target_engine = create_engine(source), create_engine(target)
    with source_engine.connect() as conn:
        count = conn.scalar(text("SELECT count(*) FROM hsn_codes"))
        columns = conn.scalars(
            text(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = "
                "'public' AND table_name = 'hsn_codes' AND is_generated = 'NEVER' "
                "ORDER BY ordinal_position"
            )
        ).all()
    if not count:
        return 0
    names = ", ".join(columns)
    source_raw, target_raw = source_engine.raw_connection(), target_engine.raw_connection()
    try:
        with (
            source_raw.driver_connection.cursor() as read,
            target_raw.driver_connection.cursor() as write,
        ):
            with (
                read.copy(f"COPY hsn_codes ({names}) TO STDOUT (FORMAT BINARY)") as out,
                write.copy(f"COPY hsn_codes ({names}) FROM STDIN (FORMAT BINARY)") as into,
            ):
                for chunk in out:
                    into.write(chunk)
            write.execute(
                "SELECT setval(pg_get_serial_sequence('hsn_codes', 'id'), "
                "(SELECT max(id) FROM hsn_codes))"
            )
        target_raw.commit()
    finally:
        source_raw.close()
        target_raw.close()
    with target_engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT").exec_driver_sql("ANALYZE hsn_codes")
    source_engine.dispose()
    target_engine.dispose()
    return count


# ---------------------------------------------------------------------------
# Providers: record, replay or live
# ---------------------------------------------------------------------------


class Providers:
    """Hands out one provider per case and keeps what each one recorded."""

    def __init__(self, mode: str, path: Path, resume: bool):
        from app.services.extraction import get_provider

        self.mode, self.path = mode, path
        saved = json.loads(path.read_text()) if path.exists() else {"cases": {}}
        if mode == "replay" and not path.exists():
            raise OutOfDate(f"no recordings at {path.relative_to(REPO)}")
        self.saved = saved
        self.cases: dict[str, dict] = {}  # what this run will save
        self.reuse = saved["cases"] if mode == "replay" or resume else {}
        self.meta = {k: v for k, v in saved.items() if k != "cases"} if resume else {}
        self.real = None
        if mode != "replay":
            self.real = get_provider()
            if self.real is None:
                sys.exit("LLM_API_KEY is not set: --record and --live need the real model.")
        self.active: dict[str, object] = {}

    def for_case(self, case: str):
        from app.llm.replay import RecordingProvider, ReplayProvider

        recorded = self.reuse.get(case)
        if self.mode == "replay":
            # Never recorded: an empty replay, so the first call is a mismatch.
            provider = ReplayProvider(recorded["calls"] if recorded else [])
        else:  # --resume reuses recorded replies while the requests still match
            provider = RecordingProvider(self.real, reuse=recorded["calls"] if recorded else [])
        self.active[case] = provider
        return provider

    def sequential(self):
        """A provider_for(case) that closes the previous case first (for the ledger)."""

        def provider_for(case: str):
            for previous in list(self.active):
                self.finish(previous)
            return self.for_case(case)

        return provider_for

    def finish_all(self) -> None:
        for case in list(self.active):
            self.finish(case)

    def finish(self, case: str, **extra) -> dict:
        """Close a case: check replay mismatches, keep the calls. Returns the saved entry."""
        from app.llm.replay import ReplayProvider

        provider = self.active.pop(case)
        if isinstance(provider, ReplayProvider):
            if provider.mismatches:
                raise OutOfDate(f"{case}: {provider.mismatches[0]}")
            entry = self.reuse.get(case, {"calls": []})
        elif not provider.diverged and provider.calls and case in self.reuse:
            entry = self.reuse[case]  # --resume: every call came from the recording
        else:
            entry = {"calls": provider.calls, **extra}
        self.cases[case] = entry
        self.save()  # after every case, so a stopped run can --resume
        return entry

    def save(self, meta: dict | None = None) -> None:
        if self.mode != "record":
            return
        self.meta.update(meta or {})
        cases = {**self.reuse, **self.cases}  # --resume keeps cases this run didn't reach
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {**self.meta, "cases": cases}
        self.path.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n")


def call_cost(calls: list[dict]) -> tuple[Decimal, int]:
    from app.llm.pricing import cost_usd

    cost, tokens = Decimal(0), 0
    for call in calls:
        reply = call.get("reply")
        if reply:
            tokens += reply["input_tokens"] + reply["output_tokens"]
            cost += cost_usd(reply["model"], reply["input_tokens"], reply["output_tokens"]) or 0
    return cost, tokens


# ---------------------------------------------------------------------------
# The invoice agent, end to end
# ---------------------------------------------------------------------------


def slice_of(entry: dict) -> str:
    done = entry.get("degradations") or []
    if entry["format"] == "pdf":
        return "pdf, 2+ pages" if done and not done[0].startswith("1 ") else "pdf"
    if done and done[0].startswith("photocopy"):
        return "photocopy"
    return {"png": "scan", "jpg": "photo"}[entry["format"]]


def evaluate_agent(split: str, providers: Providers, limit: int | None) -> list[dict]:
    from app.agent import nodes
    from app.agent.graph import run_agent
    from app.agent.state import AgentDeps
    from app.config import get_settings
    from app.db import SessionLocal
    from app.llm.embeddings import get_embedder
    from app.models import Invoice, InvoiceFile, InvoiceStatus, User
    from app.security import hash_password
    from app.services.pdf import render_pages

    nodes.india_today = lambda: TODAY
    settings = get_settings()
    manifest = json.loads(SPLITS[split].read_text())[:limit]
    db = SessionLocal()
    user = User(email=f"eval-{split}@example.com", password_hash=hash_password("not-used-123"))
    db.add(user)
    db.commit()
    embedder = get_embedder()  # loads lazily: only used when classification is on

    rows = []
    for entry in manifest:
        path = REPO / entry["file"]
        content = path.read_bytes()
        pages = render_pages(content, MIME[path.suffix], settings.render_max_edge)
        invoice = Invoice(
            user_id=user.id,
            status=InvoiceStatus.PROCESSING,
            file=InvoiceFile(
                user_id=user.id,
                original_name=path.name,
                mime_type=MIME[path.suffix],
                storage_path="-",
                page_count=len(pages),
                sha256=hashlib.sha256(content).hexdigest(),
            ),
        )
        db.add(invoice)
        db.commit()

        provider = providers.for_case(entry["id"])
        started = time.monotonic()
        state = run_agent(AgentDeps(db=db, provider=provider, embedder=embedder), invoice.id, pages)
        db.commit()
        wall_ms = round((time.monotonic() - started) * 1000)
        saved = providers.finish(entry["id"], wall_ms=wall_ms)
        cost, tokens = call_cost(saved["calls"])

        truth = json.loads((REPO / entry["truth"]).read_text())
        results = field_results(truth, state.get("extraction"))
        issues = state.get("issues", [])
        row = {
            "id": entry["id"],
            "slice": slice_of(entry),
            "layout": entry["layout"],
            "line_items": entry["line_items"],
            "read_ok": state.get("extraction") is not None,
            "fields_correct": sum(ok for _, ok in results),
            "fields_total": len(results),
            "fully_correct": all(ok for _, ok in results),
            "wrong_fields": sorted({name for name, ok in results if not ok}),
            "errors": sorted({i.rule_code for i in issues if i.severity == "error"}),
            "warnings": sorted({i.rule_code for i in issues if i.severity == "warning"}),
            "decision": state.get("decision"),
            "reason": state.get("reason"),
            "re_extractions": state.get("re_extractions", 0),
            "llm_calls": len(saved["calls"]),
            "tokens": tokens,
            "cost_usd": float(cost),
            "latency_s": saved["wall_ms"] / 1000,
            "_fields": results,
        }
        rows.append(row)
        print(
            f"  {row['id']} {row['slice']:<14} {'ok ' if row['fully_correct'] else 'XX '}"
            f"{row['fields_correct']:>3}/{row['fields_total']:<3} "
            f"rereads={row['re_extractions']} {row['decision']:<12} {row['latency_s']:5.1f}s  "
            f"{','.join(row['wrong_fields'][:4])}"
        )
    db.close()
    return rows


def summarise_agent(rows: list[dict], auto_approve_measured: bool) -> dict:
    n = len(rows)
    per_field: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        for name, ok in row.pop("_fields"):
            per_field[name][0] += ok
            per_field[name][1] += 1
    by_slice: dict[str, dict] = {}
    for name in sorted({r["slice"] for r in rows}):
        group = [r for r in rows if r["slice"] == name]
        by_slice[name] = {
            "invoices": len(group),
            "field_accuracy": sum(r["fields_correct"] for r in group)
            / sum(r["fields_total"] for r in group),
            "fully_correct_rate": sum(r["fully_correct"] for r in group) / len(group),
        }
    wrong = [r for r in rows if not r["fully_correct"]]
    right = [r for r in rows if r["fully_correct"]]
    reread = [r for r in rows if r["re_extractions"]]
    auto = [r for r in rows if r["decision"] == "auto_approve"]
    latencies = [r["latency_s"] for r in rows]
    return {
        "invoices": n,
        "extraction": {
            "read_ok_rate": sum(r["read_ok"] for r in rows) / n,
            "field_accuracy": sum(r["fields_correct"] for r in rows)
            / sum(r["fields_total"] for r in rows),
            "fully_correct_rate": len(right) / n,
            "per_field": {k: c / t for k, (c, t) in sorted(per_field.items())},
            "by_slice": by_slice,
            "re_extracted": len(reread),
            "re_extracted_then_fully_correct": sum(r["fully_correct"] for r in reread),
        },
        "validation": {
            "misread_invoices": len(wrong),
            "misreads_flagged": sum(bool(r["errors"]) for r in wrong),
            "real_catch_rate": rate(sum(bool(r["errors"]) for r in wrong), len(wrong)),
            "false_alarm_rate": rate(sum(bool(r["errors"]) for r in right), len(right)),
        },
        "auto_approve": {
            "measured": auto_approve_measured,
            "approved": len(auto),
            "coverage": len(auto) / n if auto_approve_measured else None,
            "precision": rate(sum(r["fully_correct"] for r in auto), len(auto))
            if auto_approve_measured
            else None,
            "wrong_but_approved": sum(not r["fully_correct"] for r in auto),
        },
        "cost": {
            "mean_cost_usd": sum(r["cost_usd"] for r in rows) / n,
            "total_cost_usd": sum(r["cost_usd"] for r in rows),
            "mean_tokens": sum(r["tokens"] for r in rows) / n,
            "mean_llm_calls": sum(r["llm_calls"] for r in rows) / n,
        },
        "latency": {"p50_s": percentile(latencies, 50), "p95_s": percentile(latencies, 95)},
    }


# ---------------------------------------------------------------------------
# Report and gate
# ---------------------------------------------------------------------------


def lookup(summary: dict, dotted: str):
    value = summary
    for key in dotted.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def gate(summary: dict, split: str) -> list[str]:
    """Every threshold the summary falls below. "max:" thresholds are upper limits."""
    failures = []
    for key, limit in json.loads(THRESHOLDS.read_text())[split].items():
        upper = key.startswith("max:")
        value = lookup(summary, key.removeprefix("max:"))
        if value is None or (value > limit if upper else value < limit):
            bound = "max" if upper else "min"
            failures.append(f"{key.removeprefix('max:')} = {value} ({bound} {limit})")
    return failures


def pct(value) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def markdown(summary: dict, rows: list[dict]) -> str:
    meta, agent, ledger = summary["meta"], summary.get("agent"), summary.get("ledger")
    out = [
        f"# InvoiceIQ evaluation: {meta['split']} split",
        "",
        f"Mode **{meta['mode']}** · model `{meta['model_extract']}` (extraction), "
        f"`{meta['model_fast']}` (HSN, ledger) · recorded {meta['recorded_at']} · "
        f"run {meta['run_at']}",
        "",
    ]
    if agent:
        ex, va, aa = agent["extraction"], agent["validation"], agent["auto_approve"]
        inj = summary["validation_injected"]
        out += [
            "## Headline numbers",
            "",
            "| Metric | Result |",
            "|---|---|",
            f"| Invoices | {agent['invoices']} |",
            f"| Read without error (valid schema) | {pct(ex['read_ok_rate'])} |",
            f"| Field accuracy (exact match) | {pct(ex['field_accuracy'])} |",
            f"| Invoices read fully correctly | {pct(ex['fully_correct_rate'])} |",
            f"| Real misreads flagged by validation | {va['misreads_flagged']}/"
            f"{va['misread_invoices']} ({pct(va['real_catch_rate'])}) |",
            f"| False alarms on correct reads | {pct(va['false_alarm_rate'])} |",
            f"| Injected misreads caught | {pct(inj['catch_rate_any_error'])} "
            f"of {inj['injected']} |",
            f"| Auto-approve precision | {pct(aa['precision'])} "
            f"({aa['approved']} approved, {aa['wrong_but_approved']} wrong) |",
            f"| Cost per invoice | ${agent['cost']['mean_cost_usd']:.4f} |",
            f"| Latency p50 / p95 | {agent['latency']['p50_s']:.1f}s / "
            f"{agent['latency']['p95_s']:.1f}s |",
        ]
        if summary.get("reconcile"):
            rec = summary["reconcile"]
            out.append(
                f"| GSTR-2B reconciliation (synthetic, labelled) | {pct(rec['row_accuracy'])} "
                f"of {rec['rows']} invoices; 'ITC at risk' list right in "
                f"{pct(rec['at_risk_accuracy'])} of {rec['months']} months |"
            )
        if ledger:
            out += [
                f"| Ledger Q&A execution accuracy | {pct(ledger['execution_accuracy_strict'])} "
                f"strict, {pct(ledger['execution_accuracy_lenient'])} lenient |",
                f"| Adversarial ledger prompts safe | {ledger['adversarial_safe']}/"
                f"{ledger['adversarial_prompts']} |",
            ]
        out += ["", "## By file type", "", "| Slice | Invoices | Field accuracy | Fully correct |"]
        out += ["|---|---|---|---|"]
        for name, s in ex["by_slice"].items():
            out.append(
                f"| {name} | {s['invoices']} | {pct(s['field_accuracy'])} "
                f"| {pct(s['fully_correct_rate'])} |"
            )
        worst = sorted(ex["per_field"].items(), key=lambda kv: kv[1])[:8]
        out += ["", "## Least accurate fields", "", "| Field | Accuracy |", "|---|---|"]
        out += [f"| `{name}` | {pct(value)} |" for name, value in worst]
        out += [
            "",
            f"The agent re-read {ex['re_extracted']} invoice(s); "
            f"{ex['re_extracted_then_fully_correct']} ended fully correct.",
        ]
        if not aa["measured"]:
            out += [
                "",
                "Auto-approve needs the HSN catalogue, so it is measured only in --record "
                "runs; the figure above comes from the recording.",
            ]
        misses = [r for r in rows if not r["fully_correct"]]
        if misses:
            out += ["", "## Invoices not read fully correctly", ""]
            out += ["| Invoice | Slice | Wrong fields | Flagged by | Decision |"]
            out += ["|---|---|---|---|---|"]
            for r in misses:
                out.append(
                    f"| {r['id']} | {r['slice']} | {', '.join(r['wrong_fields'][:6])} "
                    f"| {', '.join(r['errors']) or 'nothing'} | {r['decision']} |"
                )
    if not agent and summary.get("reconcile"):
        rec = summary["reconcile"]
        out += [
            f"GSTR-2B reconciliation: {pct(rec['row_accuracy'])} of {rec['rows']} invoices, "
            f"'ITC at risk' {pct(rec['at_risk_accuracy'])} of {rec['months']} months; "
            + ", ".join(f"{k} {v}" for k, v in rec["by_scenario"].items()),
        ]
    if not agent and ledger:
        out += [
            f"Ledger Q&A: {pct(ledger['execution_accuracy_strict'])} strict, "
            f"{pct(ledger['execution_accuracy_lenient'])} lenient; adversarial "
            f"{ledger['adversarial_safe']}/{ledger['adversarial_prompts']} safe.",
        ]
    if summary.get("gate") is not None:
        failures = summary["gate"]
        out += ["", "## Gate", "", "Passed." if not failures else "Failed:"]
        out += [f"- {f}" for f in failures]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="InvoiceIQ evaluation harness")
    parser.add_argument("--split", choices=SPLITS, default="test")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--record", action="store_true", help="call the model; save replies")
    mode.add_argument("--live", action="store_true", help="call the model; save nothing")
    parser.add_argument("--resume", action="store_true", help="--record: keep recorded cases")
    parser.add_argument("--only", choices=["agent", "ledger", "reconcile"], help="run one part")
    parser.add_argument("--limit", type=int, help="first N invoices (smoke test)")
    parser.add_argument("--gate", action="store_true", help="exit 1 below evals/thresholds.json")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # progress shows up live in logs and CI
    mode_name = "record" if args.record else "live" if args.live else "replay"

    # HSN suggestions (and so auto-approve) only when talking to the real model: the
    # vector index is approximate, so its candidates can differ between databases.
    classify = mode_name != "replay" and args.only in (None, "agent")
    eval_url, hsn_rows = prepare_database(copy_hsn=classify)
    os.environ.update(
        DATABASE_URL=eval_url,
        AUTO_APPROVE_ENABLED="true",  # measure what the agent *would* approve
        CLASSIFICATION_ENABLED="true" if classify and hsn_rows else "false",
    )
    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    print(
        f"Mode {mode_name}, split {args.split}, extraction model {settings.llm_model_extract}, "
        f"HSN codes {hsn_rows or 'off'}"
    )

    from evals import injection, ledger, reconcile

    summary: dict = {}
    rows: list[dict] = []
    agent_path = RECORDINGS / f"{args.split}.json"
    try:
        if args.only in (None, "agent"):
            providers = Providers(mode_name, agent_path, args.resume)
            print(f"\nInvoices ({args.split} split):")
            rows = evaluate_agent(args.split, providers, args.limit)
            measured = bool(classify and hsn_rows)
            summary["agent"] = summarise_agent(rows, auto_approve_measured=measured)
            if mode_name == "replay":  # carry the recorded run's auto-approve figures
                summary["agent"]["auto_approve"] = providers.saved["summary"]["auto_approve"]
            agent_meta = providers.saved if mode_name == "replay" else None
            providers.save(
                {
                    "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "model_extract": settings.llm_model_extract,
                    "model_fast": settings.llm_model_fast,
                    "summary": {"auto_approve": summary["agent"]["auto_approve"]},
                }
            )
            truths = {
                p.stem: json.loads(p.read_text())
                for p in sorted(SPLITS[args.split].parent.glob("*_*.json"))
            }
            summary["validation_injected"] = {
                "false_alarm_invoices": len(injection.false_alarms(truths)),
                **{k: v for k, v in injection.catch_rates(truths).items() if k != "by_injection"},
            }
        if args.only in (None, "ledger"):
            from app.db import SessionLocal

            ledger_providers = Providers(mode_name, RECORDINGS / "ledger.json", args.resume)
            print("\nLedger Q&A:")
            db = SessionLocal()
            result = ledger.evaluate(db, ledger_providers.sequential())
            ledger_providers.finish_all()
            db.close()
            summary["ledger"] = result["summary"]
            summary["ledger_detail"] = {k: result[k] for k in ("questions", "adversarial")}
            ledger_providers.save(
                {
                    "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "model_fast": settings.llm_model_fast,
                }
            )
        if args.only in (None, "reconcile"):
            from app.db import SessionLocal

            print("\nGSTR-2B reconciliation:")
            db = SessionLocal()
            result = reconcile.evaluate(db)
            db.close()
            summary["reconcile"] = result["summary"]
            summary["reconcile_detail"] = result["rows"]
    except OutOfDate as error:
        sys.exit(
            f"\nRecordings are out of date ({error}).\n"
            "A prompt, the schema, the model or page rendering changed since they were "
            "recorded.\nRe-record with: make eval-record"
        )

    recorded_at = (
        agent_meta["recorded_at"] if args.only in (None, "agent") and agent_meta else "this run"
    )
    summary["meta"] = {
        "split": args.split,
        "mode": mode_name,
        "model_extract": settings.llm_model_extract,
        "model_fast": settings.llm_model_fast,
        "recorded_at": recorded_at if mode_name == "replay" else "this run",
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "today_pinned": TODAY.isoformat(),
    }
    if args.gate:
        summary["gate"] = gate(summary, args.split)

    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    name = f"latest-{args.split}-replay" if mode_name == "replay" else f"eval-{args.split}-{stamp}"
    (REPORTS / f"{name}.json").write_text(
        json.dumps({"summary": summary, "invoices": rows}, indent=2, default=str) + "\n"
    )
    (REPORTS / f"{name}.md").write_text(markdown(summary, rows))
    print("\n" + markdown(summary, rows))
    print(f"Report: evals/reports/{name}.md")
    if args.gate and summary["gate"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
