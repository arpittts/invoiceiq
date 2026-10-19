"""Measure how well the validation rules catch extraction mistakes.

1. False alarms: run the rules on the correct ground-truth invoices. Any error
   here is a rule bug, because these invoices are right by construction.
2. Catch rate: inject one realistic misread at a time (the kinds of mistakes a
   vision model makes) and check whether the expected rule reports it.

No AI is involved, so this is free, fast and deterministic (seeded). The
injection code lives in evals/injection.py (the eval harness uses it too).

Usage (from the repo root):
    backend/.venv/bin/python scripts/check_validation.py                 # dev split
    backend/.venv/bin/python scripts/check_validation.py --truth evals/datasets/test
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from evals.injection import catch_rates, false_alarms  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--truth", default="evals/datasets/starter")
    args = parser.parse_args()
    truths = {
        path.stem: json.loads(path.read_text())
        for path in sorted((REPO / args.truth).glob("*_*.json"))
    }

    alarms = false_alarms(truths)
    rates = catch_rates(truths)
    print(f"False alarms on {len(truths)} correct invoices: {len(alarms)}")
    for invoice_id, codes in alarms.items():
        print(f"  {invoice_id}: {codes}")
    print(f"\n{'Injected error':<40} {'Rule':<13} {'Tried':>5} {'By rule':>8} {'Any error':>10}")
    for name, r in rates["by_injection"].items():
        print(
            f"{name:<40} {r['rule']:<13} {r['tried']:>5} "
            f"{r['caught_by_rule'] / r['tried']:>8.0%} {r['caught_any'] / r['tried']:>10.0%}"
        )
    print(
        "Note: rules allow ₹1 of rounding drift, so a misread in the paise (e.g. .56 -> .65)\n"
        "is not detectable by arithmetic and is counted as missed."
    )
    print(
        f"\nOverall: {rates['injected']} injected errors, {rates['catch_rate_any_error']:.1%} "
        f"caught ({rates['catch_rate_expected_rule']:.1%} by the expected rule)"
    )

    report = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "invoices": len(truths),
        "false_alarm_invoices": alarms,
        **rates,
    }
    out = REPO / "evals/reports" / f"validation-catch-rate-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report: {out.relative_to(REPO)}")
    sys.exit(1 if alarms else 0)


if __name__ == "__main__":
    main()
