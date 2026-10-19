"""Measure text-to-SQL execution accuracy and adversarial safety (blueprint section 11).

Now part of the eval harness (evals/ledger.py). This runs only the ledger part,
live against the configured LLM (free on Gemini), without saving recordings.
Usage (from the repo root):
    backend/.venv/bin/python scripts/eval_ledger_qa.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.run import main  # noqa: E402

if __name__ == "__main__":
    main(["--only", "ledger", "--live"])
