"""Bundle the "Try demo invoices" files with their recorded model replies.

Copies a handful of test-split invoices into backend/app/demo/ (so they ship in
the Docker image) together with the replies recorded by `make eval-record`.
The demo then replays those replies: instant, no free-tier quota used, and it
still works when the daily cap is reached (blueprint section 18).

Usage (from the repo root): backend/.venv/bin/python scripts/make_demo_bundle.py
"""

import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "backend/app/demo"

# (test case, why it's in the demo)
DEMO = [
    ("test_009", "A phone photo the agent can approve on its own"),
    ("test_021", "A clean PDF with six line items"),
    ("test_016", "A 32-line bill that runs onto a second page"),
    ("test_003", "A low-resolution scan"),
    ("test_035", "A faint photocopy"),
    (
        "test_044",
        "A blurred photo where the vendor GSTIN is misread, and validation flags it",
    ),
]


def main() -> None:
    manifest = {
        e["id"]: e for e in json.loads((REPO / "evals/datasets/test/manifest.json").read_text())
    }
    recordings = json.loads((REPO / "evals/recordings/test.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("test_*"):
        old.unlink()
    bundle = []
    for case, note in DEMO:
        source = REPO / manifest[case]["file"]
        shutil.copyfile(source, OUT / source.name)
        bundle.append(
            {
                "file": source.name,
                "note": note,
                "calls": recordings["cases"][case]["calls"],
            }
        )
    (OUT / "demo.json").write_text(
        json.dumps({"model_extract": recordings["model_extract"], "invoices": bundle}, indent=1)
        + "\n"
    )
    print(f"Bundled {len(bundle)} demo invoices into {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
